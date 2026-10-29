import secrets

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from apps.core.models import OrgScopedModel, TimeStampedModel


def new_token():
    return secrets.token_urlsafe(24)


class Workflow(OrgScopedModel):
    """Trigger -> conditions -> ordered actions. Trigger/condition/action vocab lives in registry.py."""

    name = models.CharField(max_length=120)
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=False)
    trigger_type = models.CharField(max_length=40)
    trigger_config = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    conditions = models.JSONField(default=dict, blank=True, help_text='{"match": "all"|"any", "rules": [{field, op, value}]}')
    allow_reenroll = models.BooleanField(default=False, help_text="Let the same contact/deal go through this workflow more than once.")
    webhook_token = models.CharField(max_length=64, default=new_token, unique=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    run_count = models.PositiveIntegerField(default=0)
    last_run_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-is_active", "name"]
        indexes = [models.Index(fields=["org", "is_active", "trigger_type"])]

    def __str__(self):
        return self.name


class WorkflowAction(TimeStampedModel):
    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="actions")
    position = models.PositiveSmallIntegerField(default=0)
    action_type = models.CharField(max_length=40)
    config = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    state = models.JSONField(default=dict, blank=True, help_text="Engine bookkeeping, e.g. round-robin pointer.")

    class Meta:
        ordering = ["workflow", "position", "pk"]

    def __str__(self):
        return f"{self.workflow} #{self.position}: {self.action_type}"


class RunStatus(models.TextChoices):
    RUNNING = "running", "Running"
    WAITING = "waiting", "Waiting"
    COMPLETED = "completed", "Completed"
    STOPPED = "stopped", "Stopped by a condition"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class WorkflowRun(OrgScopedModel):
    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="runs")
    contact = models.ForeignKey("contacts.Contact", null=True, blank=True, on_delete=models.CASCADE, related_name="workflow_runs")
    deal = models.ForeignKey("pipeline.Deal", null=True, blank=True, on_delete=models.CASCADE, related_name="workflow_runs")
    trigger_activity = models.ForeignKey("activities.Activity", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    trigger_summary = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=RunStatus.choices, default=RunStatus.RUNNING)
    current_step = models.PositiveSmallIntegerField(default=0)
    resume_at = models.DateTimeField(null=True, blank=True, db_index=True)
    depth = models.PositiveSmallIntegerField(default=0, help_text="How many workflows deep this run was caused by.")
    error = models.TextField(blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(fields=["workflow", "contact"]),
            models.Index(fields=["workflow", "deal"]),
            models.Index(fields=["status", "resume_at"]),
        ]

    def __str__(self):
        return f"{self.workflow} for {self.subject_label}"

    @property
    def subject_label(self):
        if self.deal_id:
            return self.deal.title
        if self.contact_id:
            return self.contact.display_name
        return "—"

    @property
    def is_open(self):
        return self.status in (RunStatus.RUNNING, RunStatus.WAITING)


class StepStatus(models.TextChoices):
    DONE = "done", "Done"
    SKIPPED = "skipped", "Skipped"
    FAILED = "failed", "Failed"
    WAITING = "waiting", "Waiting"
    STOPPED = "stopped", "Stopped the run"


class WorkflowStepLog(models.Model):
    run = models.ForeignKey(WorkflowRun, on_delete=models.CASCADE, related_name="steps")
    position = models.PositiveSmallIntegerField()
    action_type = models.CharField(max_length=40)
    status = models.CharField(max_length=10, choices=StepStatus.choices)
    message = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self):
        return f"{self.run_id} step {self.position}: {self.status}"
