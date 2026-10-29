import os

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import OrgScopedModel


class Note(OrgScopedModel):
    contact = models.ForeignKey("contacts.Contact", null=True, blank=True, on_delete=models.CASCADE, related_name="notes")
    company = models.ForeignKey("contacts.Company", null=True, blank=True, on_delete=models.CASCADE, related_name="notes")
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


class Activity(OrgScopedModel):
    """One timeline entry. Phase 4 automations will listen to these as trigger events."""

    contact = models.ForeignKey("contacts.Contact", null=True, blank=True, on_delete=models.CASCADE, related_name="activities")
    company = models.ForeignKey("contacts.Company", null=True, blank=True, on_delete=models.CASCADE, related_name="activities")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    kind = models.CharField(max_length=32, choices=ActivityKind.choices)
    summary = models.CharField(max_length=255)
    data = models.JSONField(default=dict, blank=True)
    note = models.ForeignKey(Note, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    attachment = models.ForeignKey(Attachment, null=True, blank=True, on_delete=models.CASCADE, related_name="+")

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name_plural = "activities"
        indexes = [
            models.Index(fields=["contact", "-created_at"]),
            models.Index(fields=["company", "-created_at"]),
            models.Index(fields=["org", "-created_at"]),
        ]

    def __str__(self):
        return self.summary
