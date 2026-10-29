import hashlib
import secrets
import uuid

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone

from apps.core.fields import EncryptedTextField
from apps.core.models import OrgScopedModel, TimeStampedModel

KEY_PREFIX = "tbd"


def _hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def new_token():
    return secrets.token_urlsafe(24)


def new_webhook_secret():
    return "whsec_" + secrets.token_urlsafe(24)


# --- REST API keys -----------------------------------------------------------------------------------------------------


class ApiScope(models.TextChoices):
    READ = "read", "Read only"
    WRITE = "write", "Read and write"


class ApiKey(OrgScopedModel):
    """A workspace API key. Only a hash is stored; the full key is shown once when it's created.

    The key acts as the person who created it (their role and name on the timeline) and stops working if
    they leave the workspace.
    """

    name = models.CharField(max_length=100, help_text="What uses this key, e.g. “Website” or “Zapier”.")
    prefix = models.CharField(max_length=16, unique=True, editable=False)
    key_hash = models.CharField(max_length=64, unique=True, editable=False)
    scope = models.CharField(max_length=8, choices=ApiScope.choices, default=ApiScope.WRITE)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_keys")
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["revoked_at", "-created_at"]

    def __str__(self):
        return f"{self.name} ({self.prefix}…)"

    @classmethod
    def issue(cls, *, org, name, scope, user):
        """Returns (key, raw_key). The raw key is never stored."""
        prefix = f"{KEY_PREFIX}_{secrets.token_hex(4)}"
        raw = f"{prefix}_{secrets.token_urlsafe(32)}"
        key = cls.objects.create(org=org, name=name, scope=scope, created_by=user, prefix=prefix, key_hash=_hash(raw))
        return key, raw

    @classmethod
    def lookup(cls, raw):
        if not raw or not raw.startswith(f"{KEY_PREFIX}_"):
            return None
        return cls.objects.filter(key_hash=_hash(raw), revoked_at__isnull=True).select_related("org", "created_by").first()

    @property
    def is_active(self):
        return self.revoked_at is None

    def revoke(self):
        self.revoked_at = timezone.now()
        self.save(update_fields=["revoked_at", "updated_at"])


# --- Outgoing webhooks -------------------------------------------------------------------------------------------------


class WebhookEndpoint(OrgScopedModel):
    """Another system's URL that receives a signed POST for each matching timeline event."""

    url = models.URLField(max_length=500, help_text="Must be a public https:// address.")
    description = models.CharField(max_length=200, blank=True)
    events = models.JSONField(default=list, blank=True, help_text="Timeline event kinds to send; empty means all.")
    secret = models.CharField(max_length=64, default=new_webhook_secret, editable=False)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    consecutive_failures = models.PositiveIntegerField(default=0, editable=False)
    disabled_reason = models.CharField(max_length=255, blank=True, editable=False)
    last_delivery_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.url

    def wants(self, kind):
        return self.is_active and (not self.events or kind in self.events)


class DeliveryStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SUCCESS = "success", "Delivered"
    FAILED = "failed", "Failed"


class WebhookDelivery(OrgScopedModel):
    endpoint = models.ForeignKey(WebhookEndpoint, on_delete=models.CASCADE, related_name="deliveries")
    event = models.CharField(max_length=32)
    event_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    payload = models.JSONField(encoder=DjangoJSONEncoder)
    status = models.CharField(max_length=8, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.TextField(blank=True)
    error = models.CharField(max_length=500, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name_plural = "webhook deliveries"
        indexes = [models.Index(fields=["status", "next_attempt_at"])]

    def __str__(self):
        return f"{self.event} → {self.endpoint}"


# --- Facebook lead ads -------------------------------------------------------------------------------------------------


class FacebookLeadSettings(TimeStampedModel):
    """One Meta app per workspace receives leadgen webhooks for the pages below."""

    org = models.OneToOneField("orgs.Organization", on_delete=models.CASCADE, related_name="facebook_lead_settings")
    app_secret = EncryptedTextField(blank=True, help_text="Meta app secret; used to check webhook signatures.")
    verify_token = models.CharField(max_length=64, default=new_token, editable=False)
    webhook_token = models.CharField(max_length=64, default=new_token, unique=True, editable=False)
    default_owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                      help_text="New leads are assigned to this person (workflows can re-assign them).")
    lifecycle_stage = models.CharField(max_length=16, default="lead")
    add_tags = models.ManyToManyField("contacts.Tag", blank=True, related_name="+")

    class Meta:
        verbose_name_plural = "Facebook lead settings"

    def __str__(self):
        return f"Facebook lead ads for {self.org}"

    @classmethod
    def for_org(cls, org):
        return cls.objects.get_or_create(org=org)[0]


class FacebookPage(OrgScopedModel):
    page_id = models.CharField(max_length=32, unique=True, help_text="Numeric Page ID (Page → About → Page transparency).")
    name = models.CharField(max_length=150)
    access_token = EncryptedTextField(help_text="Page access token with leads_retrieval permission.")
    is_active = models.BooleanField(default=True)
    lead_count = models.PositiveIntegerField(default=0, editable=False)
    last_lead_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class LeadStatus(models.TextChoices):
    RECEIVED = "received", "Received"
    IMPORTED = "imported", "Imported"
    FAILED = "failed", "Failed"


class FacebookLead(OrgScopedModel):
    page = models.ForeignKey(FacebookPage, on_delete=models.CASCADE, related_name="leads")
    leadgen_id = models.CharField(max_length=40, unique=True)
    form_id = models.CharField(max_length=40, blank=True)
    form_name = models.CharField(max_length=200, blank=True)
    ad_id = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=8, choices=LeadStatus.choices, default=LeadStatus.RECEIVED)
    field_data = models.JSONField(default=dict, blank=True, help_text="Answers as {question: value}.")
    contact = models.ForeignKey("contacts.Contact", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_contact = models.BooleanField(default=False)
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"Lead {self.leadgen_id}"


# --- Calendar feed -----------------------------------------------------------------------------------------------------


class CalendarFeed(OrgScopedModel):
    """A private iCalendar URL with a person's tasks (and deal close dates) for Google Calendar, Outlook or Apple Calendar."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calendar_feeds")
    token = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    include_deals = models.BooleanField(default=True, help_text="Also show expected close dates of my open deals.")
    last_fetched_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["org", "user"], name="unique_calendar_feed")]

    def __str__(self):
        return f"Calendar feed for {self.user}"

    def rotate(self):
        self.token = new_token()
        self.save(update_fields=["token", "updated_at"])
