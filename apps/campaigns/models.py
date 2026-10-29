from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import OrgScopedModel, TimeStampedModel


class Segment(OrgScopedModel):
    """A saved, always up-to-date list of contacts defined by conditions (same rule format as workflows)."""

    name = models.CharField(max_length=120)
    description = models.CharField(max_length=255, blank=True)
    conditions = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["org", "name"], name="unique_segment_name")]

    def __str__(self):
        return self.name

    def contacts(self):
        from .segments import segment_queryset

        return segment_queryset(self)


class SendAs(models.TextChoices):
    OWNER = "owner", "Each contact's owner"
    CREATOR = "creator", "The person who set this up"


class Sequence(OrgScopedModel):
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True, help_text="Paused sequences keep enrollments but send nothing.")
    exit_on_reply = models.BooleanField(default=True, help_text="Stop as soon as the contact replies by email or SMS.")
    exit_conditions = models.JSONField(default=dict, blank=True, help_text="Also stop when these are true (e.g. stage is Customer).")
    send_as = models.CharField(max_length=8, choices=SendAs.choices, default=SendAs.OWNER)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class SequenceStep(TimeStampedModel):
    sequence = models.ForeignKey(Sequence, on_delete=models.CASCADE, related_name="steps")
    position = models.PositiveSmallIntegerField(default=0)
    delay_days = models.PositiveSmallIntegerField(default=0)
    delay_hours = models.PositiveSmallIntegerField(default=0)
    channel = models.CharField(max_length=8, choices=[("email", "Email"), ("sms", "SMS")], default="email")
    template = models.ForeignKey("messaging.MessageTemplate", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField(blank=True)

    class Meta:
        ordering = ["sequence", "position", "pk"]

    def __str__(self):
        return f"{self.sequence} step {self.position + 1}"


class EnrollmentStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    COMPLETED = "completed", "Completed"
    REPLIED = "replied", "Stopped: replied"
    EXITED = "exited", "Stopped: exit condition"
    UNSUBSCRIBED = "unsubscribed", "Stopped: unsubscribed"
    REMOVED = "removed", "Removed"
    FAILED = "failed", "Failed"


class SequenceEnrollment(OrgScopedModel):
    sequence = models.ForeignKey(Sequence, on_delete=models.CASCADE, related_name="enrollments")
    contact = models.ForeignKey("contacts.Contact", on_delete=models.CASCADE, related_name="sequence_enrollments")
    status = models.CharField(max_length=14, choices=EnrollmentStatus.choices, default=EnrollmentStatus.ACTIVE)
    current_step = models.PositiveSmallIntegerField(default=0)
    next_run_at = models.DateTimeField(null=True, blank=True, db_index=True)
    enrolled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=255, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["sequence", "contact"], condition=Q(status="active"),
                                               name="one_active_enrollment_per_sequence")]
        indexes = [models.Index(fields=["status", "next_run_at"])]

    def __str__(self):
        return f"{self.contact} in {self.sequence}"


class SequenceDelivery(models.Model):
    """Which message each step produced, for per-step stats."""

    enrollment = models.ForeignKey(SequenceEnrollment, on_delete=models.CASCADE, related_name="deliveries")
    step = models.ForeignKey(SequenceStep, null=True, on_delete=models.SET_NULL, related_name="deliveries")
    message = models.ForeignKey("messaging.Message", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    skipped_reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class BroadcastStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SCHEDULED = "scheduled", "Scheduled"
    SENDING = "sending", "Sending"
    SENT = "sent", "Sent"
    CANCELLED = "cancelled", "Cancelled"


class Broadcast(OrgScopedModel):
    name = models.CharField(max_length=120)
    channel = models.CharField(max_length=8, choices=[("email", "Email"), ("sms", "SMS")], default="email")
    segment = models.ForeignKey(Segment, on_delete=models.PROTECT, related_name="broadcasts")
    template = models.ForeignKey("messaging.MessageTemplate", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField(blank=True)
    send_as = models.CharField(max_length=8, choices=SendAs.choices, default=SendAs.CREATOR)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=BroadcastStatus.choices, default=BroadcastStatus.DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class RecipientStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SENT = "sent", "Queued to send"
    SKIPPED = "skipped", "Skipped"


class BroadcastRecipient(models.Model):
    broadcast = models.ForeignKey(Broadcast, on_delete=models.CASCADE, related_name="recipients")
    contact = models.ForeignKey("contacts.Contact", on_delete=models.CASCADE, related_name="+")
    status = models.CharField(max_length=8, choices=RecipientStatus.choices, default=RecipientStatus.PENDING)
    message = models.ForeignKey("messaging.Message", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    skipped_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["broadcast", "contact"], name="unique_broadcast_recipient")]
        indexes = [models.Index(fields=["broadcast", "status"])]
