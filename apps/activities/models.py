import os

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone

from apps.core.models import OrgScopedModel


class Note(OrgScopedModel):
    contact = models.ForeignKey("contacts.Contact", null=True, blank=True, on_delete=models.CASCADE, related_name="notes")
    company = models.ForeignKey("contacts.Company", null=True, blank=True, on_delete=models.CASCADE, related_name="notes")
    deal = models.ForeignKey("pipeline.Deal", null=True, blank=True, on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    body = models.TextField()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.body[:60]


def attachment_upload_path(instance, filename):
    return f"org_{instance.org_id}/attachments/{timezone.now():%Y/%m}/{filename}"


class Attachment(OrgScopedModel):
    """Private file: served only through the org-checked download view, never from MEDIA_URL."""

    contact = models.ForeignKey("contacts.Contact", null=True, blank=True, on_delete=models.CASCADE, related_name="attachments")
    company = models.ForeignKey("contacts.Company", null=True, blank=True, on_delete=models.CASCADE, related_name="attachments")
    deal = models.ForeignKey("pipeline.Deal", null=True, blank=True, on_delete=models.CASCADE, related_name="attachments")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    file = models.FileField(upload_to=attachment_upload_path)
    original_name = models.CharField(max_length=255)
    size = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.original_name

    @property
    def extension(self):
        return os.path.splitext(self.original_name)[1].lstrip(".").upper() or "FILE"


class ActivityKind(models.TextChoices):
    CREATED = "created", "Created"
    UPDATED = "updated", "Updated"
    STAGE_CHANGED = "stage_changed", "Lifecycle stage changed"
    OWNER_CHANGED = "owner_changed", "Owner changed"
    TAG_ADDED = "tag_added", "Tag added"
    TAG_REMOVED = "tag_removed", "Tag removed"
    NOTE_ADDED = "note_added", "Note added"
    ATTACHMENT_ADDED = "attachment_added", "File attached"
    IMPORTED = "imported", "Imported"
    DEAL_STAGE_CHANGED = "deal_stage_changed", "Deal moved"
    DEAL_WON = "deal_won", "Deal won"
    DEAL_LOST = "deal_lost", "Deal lost"
    DEAL_REOPENED = "deal_reopened", "Deal reopened"
    TASK_CREATED = "task_created", "Task created"
    TASK_COMPLETED = "task_completed", "Task completed"
    EMAIL_SENT = "email_sent", "Email sent"
    EMAIL_RECEIVED = "email_received", "Email received"
    EMAIL_OPENED = "email_opened", "Email opened"
    EMAIL_CLICKED = "email_clicked", "Email link clicked"
    SMS_SENT = "sms_sent", "SMS sent"
    SMS_RECEIVED = "sms_received", "SMS received"
    WHATSAPP_SENT = "whatsapp_sent", "WhatsApp sent"
    WHATSAPP_RECEIVED = "whatsapp_received", "WhatsApp received"
    MESSAGE_FAILED = "message_failed", "Message failed"
    CALL_LOGGED = "call_logged", "Call logged"
    AUTOMATION = "automation", "Automation"
    FORM_SUBMITTED = "form_submitted", "Form submitted"
    LEAD_GRADE_CHANGED = "lead_grade_changed", "Lead grade changed"
    UNSUBSCRIBED = "unsubscribed", "Unsubscribed"
    RESUBSCRIBED = "resubscribed", "Resubscribed"
    SEQUENCE_ENROLLED = "sequence_enrolled", "Added to sequence"
    SEQUENCE_FINISHED = "sequence_finished", "Left sequence"
    FACEBOOK_LEAD = "facebook_lead", "Facebook lead ad"


class Activity(OrgScopedModel):
    """One timeline entry. Phase 4 automations will listen to these as trigger events."""

    contact = models.ForeignKey("contacts.Contact", null=True, blank=True, on_delete=models.CASCADE, related_name="activities")
    company = models.ForeignKey("contacts.Company", null=True, blank=True, on_delete=models.CASCADE, related_name="activities")
    deal = models.ForeignKey("pipeline.Deal", null=True, blank=True, on_delete=models.CASCADE, related_name="activities")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    kind = models.CharField(max_length=32, choices=ActivityKind.choices)
    summary = models.CharField(max_length=255)
    data = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)  # Decimal/date values in change logs
    note = models.ForeignKey(Note, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    attachment = models.ForeignKey(Attachment, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    task = models.ForeignKey("tasks.Task", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    message = models.ForeignKey("messaging.Message", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name_plural = "activities"
        indexes = [
            models.Index(fields=["contact", "-created_at"]),
            models.Index(fields=["company", "-created_at"]),
            models.Index(fields=["deal", "-created_at"]),
            models.Index(fields=["org", "-created_at"]),
        ]

    def __str__(self):
        return self.summary

    @property
    def automation_name(self):
        """Workflow that caused this event, if any (templates can't read the "_automation" key directly)."""
        return ((self.data or {}).get("_automation") or {}).get("name")
