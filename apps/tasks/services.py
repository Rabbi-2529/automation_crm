from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.activities.models import ActivityKind
from apps.activities.services import record

from .models import Task


def _targets(task):
    return {"contact": task.contact, "company": task.company, "deal": task.deal}


def task_created(task, actor):
    if task.related is None:
        return
    record(org=task.org, kind=ActivityKind.TASK_CREATED, actor=actor, task=task,
           summary=f"Task: {task.title}" + (f" (due {timezone.localtime(task.due_at):%d %b %H:%M})" if task.due_at else ""),
           **_targets(task))


@transaction.atomic
def complete(task, actor):
    task.completed_at = timezone.now()
    task.completed_by = actor
    task.save(update_fields=["completed_at", "completed_by", "updated_at"])
    if task.related is not None:
        record(org=task.org, kind=ActivityKind.TASK_COMPLETED, actor=actor, task=task,
               summary=f"Completed task: {task.title}", **_targets(task))


def reopen(task):
    task.completed_at = None
    task.completed_by = None
    task.save(update_fields=["completed_at", "completed_by", "updated_at"])


def send_due_reminders(now=None):
    """Emails the assignee of each open task whose reminder time has passed. Safe to run every minute."""
    now = now or timezone.now()
    due = (
        Task.objects.open()
        .filter(remind_at__lte=now, reminder_sent_at__isnull=True, assignee__isnull=False, org__is_active=True)
        .select_related("assignee", "org", "contact", "company", "deal")
    )
    sent = 0
    for task in due:
        # Claim the row first so two workers never email the same reminder.
        if not Task.objects.filter(pk=task.pk, reminder_sent_at__isnull=True).update(reminder_sent_at=now):
            continue
        send_mail(
            subject=f"Reminder: {task.title}",
            message=render_to_string("tasks/emails/reminder.txt", {
                "task": task, "link": settings.SITE_URL.rstrip("/") + reverse("tasks:task_list"),
            }),
            from_email=None,
            recipient_list=[task.assignee.email],
        )
        sent += 1
    return sent
