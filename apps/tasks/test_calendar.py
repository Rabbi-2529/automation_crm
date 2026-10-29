import json
from datetime import datetime, time, timedelta

from django.urls import reverse
from django.utils import timezone

from apps.contacts.tests.base import CrmTestCase
from apps.pipeline.models import Deal
from apps.pipeline.services import default_pipeline

from .models import Task


def local(day, at):
    return timezone.make_aware(datetime.combine(day, at))


class CalendarTests(CrmTestCase):
    def setUp(self):
        super().setUp()
        self.day = timezone.localdate()
        self.window = {"start": (self.day - timedelta(days=3)).isoformat() + "T00:00:00",
                       "end": (self.day + timedelta(days=30)).isoformat() + "T00:00:00"}
        pipeline = default_pipeline(self.org)
        self.deal = Deal.objects.create(org=self.org, pipeline=pipeline, stage=pipeline.stages.first(), title="Daraz",
                                        value=1000, owner=self.agent.user, expected_close_date=self.day + timedelta(days=5))
        self.task = Task.objects.create(org=self.org, title="Call Rahim", assignee=self.agent.user, created_by=self.agent.user,
                                        due_at=local(self.day, time(10, 0)), reminder="60")

    def events(self, **params):
        return self.client.get(reverse("tasks:calendar_events"), {**self.window, **params}).json()

    def test_events_feed(self):
        Task.objects.create(org=self.org, title="Theirs", assignee=self.manager.user, due_at=local(self.day, time(11, 0)))
        Task.objects.create(org=self.other_org, title="Secret", due_at=local(self.day, time(11, 0)))
        self.login(self.agent)
        titles = sorted(e["title"] for e in self.events())
        self.assertEqual(titles, ["Call Rahim", "Close: Daraz (BDT 1,000)"])
        everyone = [e["title"] for e in self.events(assignee="all")]
        self.assertIn("Theirs", everyone)
        self.assertNotIn("Secret", everyone)
        self.assertEqual([e["title"] for e in self.events(show="tasks")], ["Call Rahim"])

    def test_range_is_required_and_bounded(self):
        self.login(self.agent)
        self.assertEqual(self.client.get(reverse("tasks:calendar_events")).status_code, 400)
        response = self.client.get(reverse("tasks:calendar_events"), {"start": "2026-01-01T00:00:00", "end": "2027-01-01T00:00:00"})
        self.assertEqual(response.status_code, 400)

    def move(self, **payload):
        return self.client.post(reverse("tasks:calendar_move"), json.dumps(payload), content_type="application/json")

    def test_drag_task_to_new_day_keeps_time_and_resets_reminder(self):
        self.task.reminder_sent_at = timezone.now()
        self.task.save()
        self.login(self.agent)
        target = self.day + timedelta(days=2)
        self.assertEqual(self.move(kind="task", pk=self.task.pk, start=target.isoformat(), allDay=True).status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.due_at, local(target, time(10, 0)))
        self.assertEqual(self.task.remind_at, local(target, time(9, 0)))
        self.assertIsNone(self.task.reminder_sent_at)

    def test_drag_task_in_week_view_sets_time(self):
        self.login(self.agent)
        self.move(kind="task", pk=self.task.pk, start=f"{self.day.isoformat()}T15:30:00+06:00")
        self.task.refresh_from_db()
        self.assertEqual(timezone.localtime(self.task.due_at).time(), time(15, 30))

    def test_drag_deal_logs_change(self):
        self.login(self.agent)
        target = self.day + timedelta(days=9)
        self.move(kind="deal", pk=self.deal.pk, start=target.isoformat())
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.expected_close_date, target)
        self.assertTrue(self.deal.activities.filter(kind="updated").exists())

    def test_cannot_move_others_or_foreign_items(self):
        theirs = Task.objects.create(org=self.org, title="Theirs", assignee=self.manager.user, created_by=self.manager.user,
                                     due_at=timezone.now())
        foreign = Task.objects.create(org=self.other_org, title="Secret", due_at=timezone.now())
        self.login(self.agent)
        self.assertEqual(self.move(kind="task", pk=theirs.pk, start=self.day.isoformat(), allDay=True).status_code, 400)
        self.assertEqual(self.move(kind="task", pk=foreign.pk, start=self.day.isoformat(), allDay=True).status_code, 404)
        self.assertEqual(self.move(kind="nope", pk=1, start="x").status_code, 400)

    def test_click_day_prefills_new_task(self):
        self.login(self.agent)
        response = self.client.get(reverse("tasks:task_create"), {"due_date": "2026-11-05"})
        self.assertEqual(str(response.context["form"].initial["due_date"]), "2026-11-05")
