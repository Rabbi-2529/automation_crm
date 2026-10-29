import datetime
import secrets
import uuid

from django.conf import settings
from django.db import models

from apps.core.fields import EncryptedTextField
from apps.core.models import OrgScopedModel, TimeStampedModel


def new_token():
    return secrets.token_urlsafe(24)


class Channel(models.TextChoices):
    EMAIL = "email", "Email"
    SMS = "sms", "SMS"
    WHATSAPP = "whatsapp", "WhatsApp"


class SmtpSecurity(models.TextChoices):
    STARTTLS = "starttls", "STARTTLS (port 587)"
    SSL = "ssl", "SSL/TLS (port 465)"
    NONE = "none", "None (not recommended)"


class SmsProvider(models.TextChoices):
    NONE = "", "Not set up"
    CONSOLE = "console", "Console (development: prints instead of sending)"
    TWILIO = "twilio", "Twilio"
    HTTP = "http", "HTTP gateway (BulkSMSBD, SSL Wireless, etc.)"


class WhatsAppProvider(models.TextChoices):
    NONE = "", "Not set up"
    CONSOLE = "console", "Console (development: prints instead of sending)"
    CLOUD = "cloud", "WhatsApp Cloud API (Meta)"


class ChannelSettings(TimeStampedModel):
    """Per-workspace email and SMS configuration. Secrets are encrypted at rest."""

    org = models.OneToOneField("orgs.Organization", on_delete=models.CASCADE, related_name="channel_settings")

    # Outgoing email. Leave smtp_host empty to send through the platform's default mail server.
    email_from_name = models.CharField(max_length=100, blank=True, help_text="Defaults to the workspace name.")
    email_from_address = models.EmailField(blank=True, help_text="Must be allowed by your mail server.")
    email_reply_to = models.EmailField(blank=True, help_text="Where replies go. Use the IMAP mailbox to see them in the inbox.")
    email_signature = models.TextField(blank=True)
    smtp_host = models.CharField(max_length=200, blank=True)
    smtp_port = models.PositiveIntegerField(default=587)
    smtp_username = models.CharField(max_length=200, blank=True)
    smtp_password = EncryptedTextField(blank=True)
    smtp_security = models.CharField(max_length=10, choices=SmtpSecurity.choices, default=SmtpSecurity.STARTTLS)
    track_opens = models.BooleanField(default=True)
    track_clicks = models.BooleanField(default=True)

    # Incoming email (IMAP polling).
    imap_enabled = models.BooleanField(default=False)
    imap_host = models.CharField(max_length=200, blank=True)
    imap_port = models.PositiveIntegerField(default=993)
    imap_username = models.CharField(max_length=200, blank=True)
    imap_password = EncryptedTextField(blank=True)
    imap_folder = models.CharField(max_length=100, default="INBOX")
    imap_last_uid = models.PositiveBigIntegerField(default=0, editable=False)
    imap_last_checked_at = models.DateTimeField(null=True, blank=True, editable=False)
    imap_last_error = models.TextField(blank=True, editable=False)

    # SMS.
    sms_provider = models.CharField(max_length=10, choices=SmsProvider.choices, blank=True, default=SmsProvider.NONE)
    sms_sender_id = models.CharField(max_length=20, blank=True, help_text="Sender ID / masking name approved by your provider.")
    twilio_account_sid = models.CharField(max_length=64, blank=True)
    twilio_auth_token = EncryptedTextField(blank=True)
    twilio_from_number = models.CharField(max_length=20, blank=True)
    http_url_template = models.CharField(
        max_length=500, blank=True,
        help_text="Use {to}, {message}, {sender} and {api_key}. Example: "
                  "https://bulksmsbd.net/api/smsapi?api_key={api_key}&type=text&number={to}&senderid={sender}&message={message}",
    )
    http_method = models.CharField(max_length=4, choices=[("GET", "GET"), ("POST", "POST")], default="GET")
    http_api_key = EncryptedTextField(blank=True)
    http_success_text = models.CharField(max_length=100, blank=True, help_text="Optional text the response must contain on success.")
    webhook_token = models.CharField(max_length=64, default=new_token, unique=True, editable=False)

    # Automated sending rules (Phase 5): workflows, sequences and broadcasts respect these; 1:1 messages don't.
    quiet_hours_enabled = models.BooleanField(default=True, help_text="Hold automated email/SMS outside the sending window.")
    send_window_start = models.TimeField(default=datetime.time(9, 0))
    send_window_end = models.TimeField(default=datetime.time(21, 0))
    sms_opt_out_footer = models.BooleanField(default=True, help_text="Add “Reply STOP to opt out” to marketing SMS.")

    # WhatsApp (Phase 7): Meta's WhatsApp Cloud API. Inbound messages arrive on the webhook URL shown in settings.
    whatsapp_provider = models.CharField(max_length=10, choices=WhatsAppProvider.choices, blank=True, default=WhatsAppProvider.NONE)
    whatsapp_number = models.CharField(max_length=20, blank=True, help_text="Your WhatsApp business number, for display.")
    whatsapp_phone_number_id = models.CharField(max_length=32, blank=True, help_text="Phone number ID from Meta's WhatsApp → API setup page.")
    whatsapp_access_token = EncryptedTextField(blank=True)
    whatsapp_app_secret = EncryptedTextField(blank=True, help_text="Meta app secret; used to check webhook signatures.")
    whatsapp_verify_token = models.CharField(max_length=64, default=new_token, editable=False)

    class Meta:
        verbose_name_plural = "channel settings"

    def __str__(self):
        return f"Channels for {self.org}"

    @property
    def uses_custom_smtp(self):
        return bool(self.smtp_host)

    @property
    def sms_ready(self):
        if self.sms_provider == SmsProvider.CONSOLE:
            return True
        if self.sms_provider == SmsProvider.TWILIO:
            return bool(self.twilio_account_sid and self.twilio_auth_token and (self.twilio_from_number or self.sms_sender_id))
        if self.sms_provider == SmsProvider.HTTP:
            return bool(self.http_url_template)
        return False

    @property
    def whatsapp_ready(self):
        if self.whatsapp_provider == WhatsAppProvider.CONSOLE:
            return True
        if self.whatsapp_provider == WhatsAppProvider.CLOUD:
            return bool(self.whatsapp_phone_number_id and self.whatsapp_access_token)
        return False

    @classmethod
    def for_org(cls, org):
        return cls.objects.get_or_create(org=org)[0]


class MessageTemplate(OrgScopedModel):
    name = models.CharField(max_length=100)
    channel = models.CharField(max_length=8, choices=Channel.choices, default=Channel.EMAIL)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField(help_text="Use variables like {{ contact.first_name }} — see the list beside the editor.")
    # WhatsApp only: a Meta-approved template is required to message someone who hasn't written in the last 24 hours.
    whatsapp_template = models.CharField(max_length=100, blank=True, help_text="Name of the approved template in WhatsApp Manager.")
    whatsapp_language = models.CharField(max_length=10, blank=True, default="en", help_text="Template language code, e.g. en or bn.")
    whatsapp_params = models.TextField(blank=True, help_text="One variable per line; fills {{1}}, {{2}}… in the approved template.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["channel", "name"]
        constraints = [models.UniqueConstraint(fields=["org", "channel", "name"], name="unique_template_name")]

    def __str__(self):
        return self.name


class Conversation(OrgScopedModel):
    """A thread with one contact (or an unknown address) on one channel."""

    channel = models.CharField(max_length=8, choices=Channel.choices)
    contact = models.ForeignKey("contacts.Contact", null=True, blank=True, on_delete=models.SET_NULL, related_name="conversations")
    address = models.CharField(max_length=254, help_text="The other side's email address or phone number.")
    subject = models.CharField(max_length=255, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    is_unread = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["-last_message_at", "-pk"]
        indexes = [models.Index(fields=["org", "is_archived", "-last_message_at"])]

    def __str__(self):
        return self.subject or self.address

    @property
    def display_name(self):
        return self.contact.display_name if self.contact else self.address


class Direction(models.TextChoices):
    OUTBOUND = "out", "Sent"
    INBOUND = "in", "Received"


class MessageStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    RECEIVED = "received", "Received"


class Message(OrgScopedModel):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    channel = models.CharField(max_length=8, choices=Channel.choices)
    direction = models.CharField(max_length=3, choices=Direction.choices)
    status = models.CharField(max_length=10, choices=MessageStatus.choices, default=MessageStatus.QUEUED)
    contact = models.ForeignKey("contacts.Contact", null=True, blank=True, on_delete=models.SET_NULL, related_name="messages")
    deal = models.ForeignKey("pipeline.Deal", null=True, blank=True, on_delete=models.SET_NULL, related_name="messages")
    template = models.ForeignKey(MessageTemplate, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    from_address = models.CharField(max_length=254, blank=True)
    to_address = models.CharField(max_length=254)
    subject = models.CharField(max_length=255, blank=True)
    body_text = models.TextField()
    body_html = models.TextField(blank=True)
    message_id = models.CharField(max_length=255, blank=True, db_index=True, help_text="Email Message-ID header.")
    in_reply_to = models.CharField(max_length=255, blank=True)
    provider_ref = models.CharField(max_length=100, blank=True, help_text="SMS / WhatsApp provider's message id.")
    meta = models.JSONField(default=dict, blank=True, help_text="Provider extras, e.g. the WhatsApp template and its values.")
    error = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    is_marketing = models.BooleanField(default=False, help_text="Sequences/broadcasts: gets an unsubscribe link and List-Unsubscribe header.")
    tracking_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    opened_at = models.DateTimeField(null=True, blank=True)
    open_count = models.PositiveIntegerField(default=0)
    click_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self):
        return f"{self.get_direction_display()} {self.channel} to {self.to_address}"


class TrackedLink(models.Model):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="links")
    url = models.URLField(max_length=2000)
    token = models.CharField(max_length=40, unique=True, default=new_token)
    clicks = models.PositiveIntegerField(default=0)
    first_clicked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.url


class CallOutcome(models.TextChoices):
    CONNECTED = "connected", "Connected"
    NO_ANSWER = "no_answer", "No answer"
    BUSY = "busy", "Busy"
    VOICEMAIL = "voicemail", "Left voicemail"
    WRONG_NUMBER = "wrong_number", "Wrong number"


class CallLog(OrgScopedModel):
    contact = models.ForeignKey("contacts.Contact", on_delete=models.CASCADE, related_name="calls")
    deal = models.ForeignKey("pipeline.Deal", null=True, blank=True, on_delete=models.SET_NULL, related_name="calls")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    direction = models.CharField(max_length=3, choices=Direction.choices, default=Direction.OUTBOUND)
    outcome = models.CharField(max_length=16, choices=CallOutcome.choices, default=CallOutcome.CONNECTED)
    duration_minutes = models.PositiveSmallIntegerField(default=0)
    notes = models.TextField(blank=True)
    called_at = models.DateTimeField()

    class Meta:
        ordering = ["-called_at"]

    def __str__(self):
        return f"Call with {self.contact} ({self.get_outcome_display()})"
