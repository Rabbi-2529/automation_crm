from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.fields import EncryptedTextField
from apps.core.models import OrgScopedModel, TimeStampedModel


class AssistantSettings(TimeStampedModel):
    """Which AI the workspace uses. Leave provider blank to use the platform default (AI_* settings)."""

    org = models.OneToOneField("orgs.Organization", on_delete=models.CASCADE, related_name="assistant_settings")
    provider = models.CharField(max_length=24, blank=True, help_text="Blank = the platform default.")
    base_url = models.CharField(max_length=300, blank=True, help_text="OpenAI-compatible API address, ending in /v1.")
    model = models.CharField(max_length=150, blank=True)
    api_key = EncryptedTextField(blank=True)
    temperature = models.DecimalField(max_digits=3, decimal_places=2, default=Decimal("0.30"))
    help_enabled = models.BooleanField(default=True, help_text="Staff can ask the help assistant.")
    reply_drafts_enabled = models.BooleanField(default=True, help_text="“Draft with AI” in the inbox.")
    business_context = models.TextField(blank=True, help_text="Products, prices, opening hours, tone of voice… used when drafting replies.")
    daily_limit = models.PositiveIntegerField(default=100, help_text="AI questions per person per day.")

    class Meta:
        verbose_name_plural = "assistant settings"

    def __str__(self):
        return f"AI assistant for {self.org}"

    @classmethod
    def for_org(cls, org):
        return cls.objects.get_or_create(org=org)[0]


class AssistantKind(models.TextChoices):
    HELP = "help", "Help question"
    REPLY = "reply", "Reply draft"


class AssistantLog(OrgScopedModel):
    """One AI call: what was asked, which model answered, and whether it helped. Used for limits and the usage panel."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    kind = models.CharField(max_length=8, choices=AssistantKind.choices)
    question = models.TextField()
    answer = models.TextField(blank=True)
    provider = models.CharField(max_length=24, blank=True)
    model = models.CharField(max_length=150, blank=True)
    used_ai = models.BooleanField(default=False, help_text="False when the built-in help answered.")
    error = models.CharField(max_length=500, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)
    helpful = models.BooleanField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["org", "user", "-created_at"])]

    def __str__(self):
        return self.question[:60]
