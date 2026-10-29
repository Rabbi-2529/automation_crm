import secrets

from django.conf import settings
from django.db import models

from apps.core.models import OrgScopedModel, TimeStampedModel


def new_token():
    return secrets.token_urlsafe(12)


class LeadSettings(TimeStampedModel):
    org = models.OneToOneField("orgs.Organization", on_delete=models.CASCADE, related_name="lead_settings")
    warm_threshold = models.IntegerField(default=20, help_text="Score at which a lead becomes warm.")
    hot_threshold = models.IntegerField(default=50, help_text="Score at which a lead becomes hot.")

    def __str__(self):
        return f"Lead settings for {self.org}"

    @classmethod
    def for_org(cls, org):
        return cls.objects.get_or_create(org=org)[0]

    def grade_for(self, score):
        if score >= self.hot_threshold:
            return "hot"
        if score >= self.warm_threshold:
            return "warm"
        return "cold"


class WebForm(OrgScopedModel):
    """A public lead-capture form, hosted at /f/<token>/ and embeddable with an iframe."""

    name = models.CharField(max_length=120, help_text="Internal name.")
    token = models.CharField(max_length=40, unique=True, default=new_token, editable=False)
    is_active = models.BooleanField(default=True)
    title = models.CharField(max_length=150, default="Get in touch")
    intro = models.TextField(blank=True)
    fields = models.JSONField(default=list, help_text='[{"key": "email", "label": "Email", "required": true}]')
    submit_label = models.CharField(max_length=50, default="Send")
    success_message = models.TextField(default="Thanks! We'll be in touch shortly.")
    redirect_url = models.URLField(blank=True, help_text="Send people here after submitting instead of showing the message.")
    consent_text = models.CharField(max_length=300, blank=True,
                                    help_text="If set, people must tick this to submit (e.g. “I agree to be contacted about my enquiry”).")
    lifecycle_stage = models.CharField(max_length=16, default="lead")
    default_owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                      help_text="Owner of new contacts (use a workflow for round robin).")
    add_tags = models.ManyToManyField("contacts.Tag", blank=True, related_name="+")
    submission_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class FormSubmission(OrgScopedModel):
    form = models.ForeignKey(WebForm, on_delete=models.CASCADE, related_name="submissions")
    contact = models.ForeignKey("contacts.Contact", null=True, blank=True, on_delete=models.SET_NULL, related_name="form_submissions")
    data = models.JSONField(default=dict)
    created_contact = models.BooleanField(default=False)
    utm = models.JSONField(default=dict, blank=True)
    referrer = models.URLField(max_length=500, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.form} submission"


class ScoringEvent(models.TextChoices):
    FORM_SUBMITTED = "form_submitted", "Submits a form"
    EMAIL_OPENED = "email_opened", "Opens an email"
    EMAIL_CLICKED = "email_clicked", "Clicks a link in an email"
    EMAIL_RECEIVED = "email_received", "Replies by email"
    SMS_RECEIVED = "sms_received", "Replies by SMS"
    WHATSAPP_RECEIVED = "whatsapp_received", "Replies on WhatsApp"
    FACEBOOK_LEAD = "facebook_lead", "Submits a Facebook lead ad"
    CALL_LOGGED = "call_logged", "Has a call logged"
    DEAL_CREATED = "deal_created", "Gets a deal"
    UNSUBSCRIBED = "unsubscribed", "Unsubscribes"


class ScoringRule(OrgScopedModel):
    """Profile rules add points while their conditions hold; event rules add points each time the event happens."""

    PROFILE, EVENT = "profile", "event"

    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=8, choices=[(PROFILE, "Who they are (profile)"), (EVENT, "What they do (behaviour)")])
    points = models.IntegerField(help_text="Negative points lower the score.")
    conditions = models.JSONField(default=dict, blank=True)
    event = models.CharField(max_length=20, choices=ScoringEvent.choices, blank=True)
    max_times = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Behaviour rules: count at most this many times.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["kind", "-points", "name"]

    def __str__(self):
        return f"{self.name} ({self.points:+d})"


class ScoreLog(models.Model):
    """Behaviour points earned (profile points are recomputed, not logged)."""

    contact = models.ForeignKey("contacts.Contact", on_delete=models.CASCADE, related_name="score_logs")
    rule = models.ForeignKey(ScoringRule, null=True, on_delete=models.SET_NULL, related_name="logs")
    points = models.IntegerField()
    reason = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["contact", "rule"])]
