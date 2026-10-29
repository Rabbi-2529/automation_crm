from datetime import date, datetime, time, timedelta
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.activities.models import ActivityKind
from apps.contacts.tests.base import CrmTestCase

from .models import Task
from .services import send_due_reminders


def local(d, t):
    return timezone.make_aware(datetime.combine(d, t))


class TaskFormTests(CrmTestCase):
    def test_quick_add_from_contact_page(self):
        contact = self.contact(first_name="Rahim")
        self.login(self.agent)
        url = reverse("contacts:contact_detail", args=[contact.pk])
        response = self.client.post(reverse("tasks:task_create"), {
            "quick": "1", "title": "Call Rahim", "kind": "call", "due_date": "2026-11-02", "assignee": self.agent.user.pk,
            "contact": contact.pk, "next": url,
        })
        self.assertRedirects(response, url)
        task = Task.objects.get()
        self.assertEqual(task.contact, contact)
        self.assertEqual(timezone.localtime(task.due_at).time(), time(17, 0))  # date only -> 5 PM
        self.assertEqual(task.created_by, self.agent.user)
        self.assertTrue(contact.activities.filter(kind=ActivityKind.TASK_CREATED).exists())
        self.assertContains(self.client.get(url), "Call Rahim")

    def test_reminder_time_is_computed(self):
        self.login(self.agent)
        self.client.post(reverse("tasks:task_create"), {
            "title": "Demo", "kind": "meeting", "priority": "high", "due_date": "2026-11-02", "due_time": "10:30",
            "reminder": "60", "assignee": self.agent.user.pk,
        })
        task = Task.objects.get()
        self.assertEqual(task.remind_at, local(date(2026, 11, 2), time(9, 30)))

    def test_reminder_needs_due_date_and_time_needs_date(self):
        self.login(self.agent)
        response = self.client.post(reverse("tasks:task_create"), {"title": "X", "kind": "todo", "priority": "normal",
                                                                    "reminder": "15", "due_time": "10:00"})
        errors = response.context["form"].errors
        self.assertIn("reminder", errors)
        self.assertIn("due_date", errors)

    def test_cannot_link_or_assign_across_orgs(self):
        foreign_contact = self.contact(org=self.other_org)
        self.login(self.agent)
        for field, value in (("contact", foreign_contact.pk), ("assignee", self.outsider.user.pk)):
            response = self.client.post(reverse("tasks:task_create"), {"title": "X", "kind": "todo", "priority": "normal", field: value})
            self.assertIn(field, response.context["form"].errors)
        self.assertFalse(Task.objects.exists())

    def test_open_redirect_is_blocked(self):
        self.login(self.agent)
        response = self.client.post(reverse("tasks:task_create"), {"quick": "1", "title": "X", "kind": "todo",
                                                                    "next": "https://evil.example/"})
        self.assertRedirects(response, reverse("tasks:task_list"))

    def test_only_assignee_creator_or_manager_can_edit(self):
        task = Task.objects.create(org=self.org, title="Theirs", assignee=self.manager.user, created_by=self.manager.user)
        self.login(self.agent)
        self.assertEqual(self.client.get(reverse("tasks:task_edit", args=[task.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("tasks:task_delete", args=[task.pk])).status_code, 403)
        self.login(self.admin)
        self.assertEqual(self.client.get(reverse("tasks:task_edit", args=[task.pk])).status_code, 200)

    def test_foreign_task_404(self):
        task = Task.objects.create(org=self.other_org, title="Secret")
        self.login(self.admin)
        self.assertEqual(self.client.post(reverse("tasks:task_toggle", args=[task.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("tasks:task_edit", args=[task.pk])).status_code, 404)


class TaskListTests(CrmTestCase):
    def test_groups_by_due_time_in_local_timezone(self):
        now = timezone.now()
        today_end = local(timezone.localdate(), time(23, 59))
        Task.objects.create(org=self.org, title="Late", assignee=self.agent.user, due_at=now - timedelta(hours=1))
        Task.objects.create(org=self.org, title="Tonight", assignee=self.agent.user, due_at=today_end)
        Task.objects.create(org=self.org, title="Soon", assignee=self.agent.user, due_at=now + timedelta(days=3))
        Task.objects.create(org=self.org, title="Someday", assignee=self.agent.user)
        Task.objects.create(org=self.org, title="Someone else's", assignee=self.manager.user)
        Task.objects.create(org=self.org, title="Finished", assignee=self.agent.user, completed_at=now)
        self.login(self.agent)
        groups = {key: [t.title for t in tasks] for _, key, tasks in self.client.get(reverse("tasks:task_list")).context["groups"]}
        self.assertEqual(groups["overdue"], ["Late"])
        self.assertEqual(groups["today"], ["Tonight"] if today_end > now else [])
        self.assertEqual(groups["week"], ["Soon"])
        self.assertEqual(groups["nodate"], ["Someday"])
        everyone = self.client.get(reverse("tasks:task_list"), {"assignee": "all"}).context["groups"]
        self.assertIn("Someone else's", [t.title for _, _, tasks in everyone for t in tasks])
        done = self.client.get(reverse("tasks:task_list"), {"show": "done"}).context["done"]
        self.assertEqual([t.title for t in done], ["Finished"])

    def test_sidebar_badge_counts_my_due_tasks(self):
        Task.objects.create(org=self.org, title="Late", assignee=self.agent.user, due_at=timezone.now() - timedelta(hours=2))
        Task.objects.create(org=self.org, title="Next week", assignee=self.agent.user, due_at=timezone.now() + timedelta(days=7))
        self.login(self.agent)
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.context["my_task_due_count"], 1)
        self.assertEqual(response.context["my_task_overdue_count"], 1)

    def test_toggle_complete_and_reopen_with_htmx(self):
        contact = self.contact(first_name="Rahim")
        task = Task.objects.create(org=self.org, title="Send quote", contact=contact, assignee=self.manager.user)
        self.login(self.agent)
        response = self.client.post(reverse("tasks:task_toggle", args=[task.pk]), HTTP_HX_REQUEST="true")
        self.assertTemplateUsed(response, "tasks/partials/task_row.html")
        task.refresh_from_db()
        self.assertEqual(task.completed_by, self.agent.user)
        self.assertTrue(contact.activities.filter(kind=ActivityKind.TASK_COMPLETED).exists())
        self.client.post(reverse("tasks:task_toggle", args=[task.pk]))
        task.refresh_from_db()
        self.assertIsNone(task.completed_at)


class ReminderTests(CrmTestCase):
    def test_sends_once_to_assignee_when_due(self):
        due = timezone.now() + timedelta(minutes=30)
        task = Task.objects.create(org=self.org, title="Call Rahim", assignee=self.agent.user, due_at=due, reminder="60")
        Task.objects.create(org=self.org, title="Later", assignee=self.agent.user, due_at=due + timedelta(days=2), reminder="60")
        Task.objects.create(org=self.org, title="Done", assignee=self.agent.user, due_at=due, reminder="60", completed_at=timezone.now())
        Task.objects.create(org=self.org, title="Nobody", due_at=due, reminder="60")
        self.assertEqual(send_due_reminders(), 1)
        self.assertEqual(send_due_reminders(), 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.agent.user.email])
        self.assertIn("Call Rahim", mail.outbox[0].subject)
        task.refresh_from_db()
        self.assertIsNotNone(task.reminder_sent_at)

    def test_rescheduling_allows_a_new_reminder(self):
        task = Task.objects.create(org=self.org, title="Call", assignee=self.agent.user, due_at=timezone.now(), reminder="0",
                                   created_by=self.agent.user)
        send_due_reminders()
        self.login(self.agent)
        tomorrow = timezone.localdate() + timedelta(days=1)
        self.client.post(reverse("tasks:task_edit", args=[task.pk]), {"title": "Call", "kind": "todo", "priority": "normal",
                                                                       "due_date": tomorrow.isoformat(), "reminder": "0",
                                                                       "assignee": self.agent.user.pk})
        task.refresh_from_db()
        self.assertIsNone(task.reminder_sent_at)
        with mock.patch("django.utils.timezone.now", return_value=task.remind_at + timedelta(seconds=1)):
            self.assertEqual(send_due_reminders(), 1)

    def test_management_command(self):
        Task.objects.create(org=self.org, title="Call", assignee=self.agent.user, due_at=timezone.now(), reminder="0")
        with mock.patch("sys.stdout"):
            call_command("send_task_reminders")
        self.assertEqual(len(mail.outbox), 1)
