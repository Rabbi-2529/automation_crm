from django import forms
from django.core.validators import validate_email
from django.utils import timezone

from apps.contacts.forms import OrgModelForm
from apps.pipeline.models import Deal

from .models import CallLog, Channel, ChannelSettings, MessageTemplate
from .sms import normalize_phone


class SecretInput(forms.PasswordInput):
    """Never echoes the stored secret; a blank submit keeps it."""

    def __init__(self, attrs=None):
        super().__init__(attrs={"autocomplete": "new-password", "placeholder": "Leave blank to keep the saved value", **(attrs or {})},
                         render_value=False)


class KeepSecretsMixin:
    secret_fields = ()

    def clean(self):
        cleaned = super().clean()
        for name in self.secret_fields:
            if not cleaned.get(name) and self.instance.pk:
                cleaned[name] = getattr(type(self.instance).objects.get(pk=self.instance.pk), name)
        return cleaned


class EmailSettingsForm(KeepSecretsMixin, forms.ModelForm):
    secret_fields = ("smtp_password",)

    class Meta:
        model = ChannelSettings
        fields = ["email_from_name", "email_from_address", "email_reply_to", "email_signature", "track_opens", "track_clicks",
                  "smtp_host", "smtp_port", "smtp_security", "smtp_username", "smtp_password"]
        widgets = {"smtp_password": SecretInput(), "email_signature": forms.Textarea(attrs={"rows": 3})}
        labels = {"smtp_host": "SMTP server", "smtp_port": "Port", "smtp_security": "Security", "smtp_username": "Username",
                  "smtp_password": "Password", "track_opens": "Track opens (invisible pixel)", "track_clicks": "Track link clicks"}
        help_texts = {"smtp_host": "e.g. smtp.gmail.com or mail.thingbd.com. Leave empty to use ThingBD CRM's mail server."}

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("smtp_host") and not cleaned.get("email_from_address"):
            self.add_error("email_from_address", "Set the address your SMTP server sends as.")
        return cleaned


class ImapSettingsForm(KeepSecretsMixin, forms.ModelForm):
    secret_fields = ("imap_password",)

    class Meta:
        model = ChannelSettings
        fields = ["imap_enabled", "imap_host", "imap_port", "imap_username", "imap_password", "imap_folder"]
        widgets = {"imap_password": SecretInput()}
        labels = {"imap_enabled": "Check this mailbox for replies every 2 minutes", "imap_host": "IMAP server", "imap_port": "Port",
                  "imap_username": "Username", "imap_password": "Password", "imap_folder": "Folder"}

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("imap_enabled"):
            for name in ("imap_host", "imap_username"):
                if not cleaned.get(name):
                    self.add_error(name, "Required to check the mailbox.")
            if not cleaned.get("imap_password"):
                self.add_error("imap_password", "Required to check the mailbox.")
        return cleaned


class SmsSettingsForm(KeepSecretsMixin, forms.ModelForm):
    secret_fields = ("twilio_auth_token", "http_api_key")

    class Meta:
        model = ChannelSettings
        fields = ["sms_provider", "sms_sender_id", "twilio_account_sid", "twilio_auth_token", "twilio_from_number",
                  "http_url_template", "http_method", "http_api_key", "http_success_text"]
        widgets = {"twilio_auth_token": SecretInput(), "http_api_key": SecretInput()}
        labels = {"sms_provider": "Provider", "sms_sender_id": "Sender ID", "twilio_account_sid": "Twilio account SID",
                  "twilio_auth_token": "Twilio auth token", "twilio_from_number": "Twilio number",
                  "http_url_template": "Gateway URL", "http_method": "Method", "http_api_key": "API key",
                  "http_success_text": "Success text"}

    def clean(self):
        cleaned = super().clean()
        provider = cleaned.get("sms_provider")
        if provider == "twilio" and not cleaned.get("twilio_account_sid"):
            self.add_error("twilio_account_sid", "Required for Twilio.")
        if provider == "http":
            template = cleaned.get("http_url_template", "")
            if "{to}" not in template or "{message}" not in template:
                self.add_error("http_url_template", "Must contain {to} and {message}.")
        return cleaned


class TemplateForm(OrgModelForm):
    class Meta:
        model = MessageTemplate
        fields = ["name", "channel", "subject", "body", "whatsapp_template", "whatsapp_language", "whatsapp_params"]
        widgets = {"body": forms.Textarea(attrs={"rows": 10}), "whatsapp_params": forms.Textarea(attrs={"rows": 3})}
        labels = {"whatsapp_template": "WhatsApp template name", "whatsapp_language": "Template language",
                  "whatsapp_params": "Template variables"}
        help_texts = {"subject": "Email only.",
                      "body": "For WhatsApp templates, paste the approved wording here so the inbox shows what was sent."}

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("channel") == Channel.EMAIL and not cleaned.get("subject"):
            self.add_error("subject", "Emails need a subject.")
        if cleaned.get("channel") != Channel.WHATSAPP:
            cleaned["whatsapp_template"], cleaned["whatsapp_params"] = "", ""
        elif cleaned.get("whatsapp_template") and not cleaned.get("whatsapp_language"):
            self.add_error("whatsapp_language", "Set the language the template was approved in.")
        name = (cleaned.get("name") or "").strip()
        clash = MessageTemplate.objects.for_org(self.org).filter(name__iexact=name, channel=cleaned.get("channel"))
        if name and clash.exclude(pk=self.instance.pk).exists():
            self.add_error("name", "A template with this name already exists.")
        return cleaned


class ComposeForm(forms.Form):
    to = forms.CharField(max_length=254, label="To")
    template = forms.ModelChoiceField(queryset=MessageTemplate.objects.none(), required=False, empty_label="No template",
                                      help_text="Fills in the subject and message below.")
    subject = forms.CharField(max_length=200, required=False)
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 10}), max_length=50_000, label="Message")
    deal = forms.ModelChoiceField(queryset=Deal.objects.none(), required=False, label="Related deal",
                                  help_text="Shows the message on the deal's timeline too.")

    def __init__(self, *args, org, channel, contact=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.channel = channel
        self.fields["template"].queryset = MessageTemplate.objects.for_org(org).filter(channel=channel)
        deals = Deal.objects.for_org(org).filter(contact=contact) if contact else Deal.objects.none()
        self.fields["deal"].queryset = deals
        if not deals.exists():
            del self.fields["deal"]
        if channel in (Channel.SMS, Channel.WHATSAPP):
            limit = 1000 if channel == Channel.SMS else 4096
            del self.fields["subject"]
            self.fields["body"].widget.attrs.update(rows=4, maxlength=limit)
            self.fields["body"].max_length = limit
            self.fields["to"].label = "To (phone)"
            if channel == Channel.WHATSAPP:
                self.fields["template"].help_text = "Templates with a WhatsApp template name send the approved template."
        else:
            self.fields["subject"].required = True

    def clean_to(self):
        to = self.cleaned_data["to"].strip()
        if self.channel == Channel.EMAIL:
            validate_email(to)
            return to.lower()
        if len(normalize_phone(to)) < 8:
            raise forms.ValidationError("Enter a valid phone number.")
        return to


class ReplyForm(forms.Form):
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Write a reply…"}), max_length=50_000, label="Reply")


class CallLogForm(forms.ModelForm):
    class Meta:
        model = CallLog
        fields = ["direction", "outcome", "duration_minutes", "called_at", "deal", "notes"]
        widgets = {"called_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
                   "notes": forms.Textarea(attrs={"rows": 4})}
        labels = {"duration_minutes": "Duration (minutes)", "called_at": "When", "deal": "Related deal"}

    def __init__(self, *args, contact, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["direction"].choices = [("out", "Outgoing"), ("in", "Incoming")]
        self.fields["deal"].queryset = Deal.objects.for_org(contact.org).filter(contact=contact)
        self.fields["called_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.initial.setdefault("called_at", timezone.localtime().replace(second=0, microsecond=0))


class WhatsAppSettingsForm(KeepSecretsMixin, forms.ModelForm):
    secret_fields = ("whatsapp_access_token", "whatsapp_app_secret")

    class Meta:
        model = ChannelSettings
        fields = ["whatsapp_provider", "whatsapp_number", "whatsapp_phone_number_id", "whatsapp_access_token", "whatsapp_app_secret"]
        widgets = {"whatsapp_access_token": SecretInput(), "whatsapp_app_secret": SecretInput()}
        labels = {"whatsapp_provider": "Provider", "whatsapp_number": "Business number", "whatsapp_phone_number_id": "Phone number ID",
                  "whatsapp_access_token": "Access token", "whatsapp_app_secret": "App secret"}
        help_texts = {"whatsapp_access_token": "A permanent System User token with whatsapp_business_messaging permission."}

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("whatsapp_provider") == "cloud":
            for name in ("whatsapp_phone_number_id", "whatsapp_access_token", "whatsapp_app_secret"):
                if not cleaned.get(name):
                    self.add_error(name, "Required for the WhatsApp Cloud API.")
        return cleaned


class SendingRulesForm(forms.ModelForm):
    class Meta:
        model = ChannelSettings
        fields = ["quiet_hours_enabled", "send_window_start", "send_window_end", "sms_opt_out_footer"]
        widgets = {"send_window_start": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
                   "send_window_end": forms.TimeInput(attrs={"type": "time"}, format="%H:%M")}
        labels = {"quiet_hours_enabled": "Only send automated messages inside the sending window",
                  "send_window_start": "Window opens", "send_window_end": "Window closes",
                  "sms_opt_out_footer": "Add “Reply STOP to opt out” to marketing SMS"}
        help_texts = {"send_window_start": "Workspace time zone. Workflows, sequences and broadcasts wait until the window opens."}
