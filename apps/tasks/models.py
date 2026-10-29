from datetime import datetime, time, timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import OrgScopedModel, OrgScopedQuerySet


class TaskKind(models.TextChoices):
    TODO = "todo", "To-do"
    CALL = "call", "Call"
    EMAIL = "email", "Email"
    MEETING = "meeting", "Meeting"
    FOLLOW_UP = "follow_up", "Follow-up"


class Priority(models.TextChoices):
    LOW = "low", "Low"
    NORMAL = "normal", "Normal"
    HIGH = "high", "High"


class Reminder(models.TextChoices):
    NONE = "", "No reminder"
    AT_TIME = "0", "At due time"
    MIN_15 = "15", "15 minutes before"
    HOUR_1 = "60", "1 hour before"
    DAY_1 = "1440", "1 day before"


class TaskQuerySet(OrgScopedQuerySet):
    def open(self):
        return self.filter(completed_at__isnull=True)

    def overdue(self):
        return self.open().filter(due_at__lt=timezone.now())

    def due_today(self):
        start, end = today_bounds()
        return self.open().filter(due_at__gte=start, due_at__lt=end)


def today_bounds():
    """Start and end of today in the active (Dhaka by default) time zone."""
    start = timezone.make_aware(datetime.combine(timezone.localdate(), time.min))
    return start, start + timedelta(days=1)


class Task(OrgScopedModel):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    kind = models.CharField(max_length=16, choices=TaskKind.choices, default=TaskKind.TODO)
    priority = models.CharField(max_length=8, choices=Priority.choices, default=Priority.NORMAL)
    due_at = models.DateTimeField(null=True, blank=True)
    reminder = models.CharField(max_length=8, choices=Reminder.choices, blank=True, default=Reminder.NONE)
    remind_at = models.DateTimeField(null=True, blank=True, editable=False, db_index=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True, editable=False)
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    contact = models.ForeignKey("contacts.Contact", null=True, blank=True, on_delete=models.CASCADE, related_name="tasks")
    company = models.ForeignKey("contacts.Company", null=True, blank=True, on_delete=models.CASCADE, related_name="tasks")
    deal = models.ForeignKey("pipeline.Deal", null=True, blank=True, on_delete=models.CASCADE, related_name="tasks")
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    objects = TaskQuerySet.as_manager()

    class Meta:
        ordering = [models.F("completed_at").desc(nulls_first=True), models.F("due_at").asc(nulls_last=True), "pk"]
        indexes = [
            models.Index(fields=["org", "assignee", "completed_at", "due_at"]),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        self.remind_at = self.due_at - timedelta(minutes=int(self.reminder)) if self.due_at and self.reminder != "" else None
        super().save(*args, **kwargs)

    @property
    def is_done(self):
        return self.completed_at is not None

    @property
    def is_overdue(self):
        return not self.is_done and self.due_at is not None and self.due_at < timezone.now()

    @property
    def related(self):
        return self.deal or self.contact or self.company
