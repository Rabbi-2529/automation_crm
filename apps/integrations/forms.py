from urllib.parse import urlsplit

from django import forms
from django.conf import settings

from apps.contacts.forms import OrgModelForm, org_users
from apps.contacts.models import LifecycleStage, Tag
from apps.core.net import check_public_url
from apps.messaging.forms import KeepSecretsMixin, SecretInput

from .models import ApiScope, CalendarFeed, FacebookLeadSettings, FacebookPage, WebhookEndpoint
from .webhooks import event_choices


class ApiKeyForm(forms.Form):
    name = forms.CharField(max_length=100, help_text="What will use it, e.g. “Website”, “Zapier” or “Accounting sync”.")
    scope = forms.ChoiceField(choices=ApiScope.choices, initial=ApiScope.WRITE, label="Access")


class WebhookEndpointForm(OrgModelForm):
    events = forms.MultipleChoiceField(choices=event_choices, required=False, label="Events",
                                       help_text="Leave empty to receive every event.")

    class Meta:
        model = WebhookEndpoint
        fields = ["url", "description", "events", "is_active"]
        labels = {"url": "Endpoint URL", "is_active": "Active"}
        widgets = {"url": forms.URLInput(attrs={"placeholder": "https://example.com/crm-webhook"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["events"].widget.attrs["data-placeholder"] = "All events"

    def clean_url(self):
        url = self.cleaned_data["url"]
        allow_private = getattr(settings, "ALLOW_PRIVATE_WEBHOOKS", False)
        if urlsplit(url).scheme != "https" and not allow_private:
            raise forms.ValidationError("Use an https:// URL so event data is encrypted in transit.")
        try:
            check_public_url(url)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc
        return url

    def save(self, commit=True):
        if self.instance.is_active and self.instance.disabled_reason:  # switched back on after being switched off for failures
            self.instance.consecutive_failures, self.instance.disabled_reason = 0, ""
        return super().save(commit)


class FacebookSettingsForm(KeepSecretsMixin, forms.ModelForm):
    secret_fields = ("app_secret",)
    lifecycle_stage = forms.ChoiceField(choices=LifecycleStage.choices, label="New leads start as")

    class Meta:
        model = FacebookLeadSettings
        fields = ["app_secret", "default_owner", "lifecycle_stage", "add_tags"]
        widgets = {"app_secret": SecretInput()}
        labels = {"default_owner": "Assign new leads to", "add_tags": "Tag new leads"}

    def __init__(self, *args, org, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["default_owner"].queryset = org_users(org)
        self.fields["default_owner"].label_from_instance = lambda u: u.display_name
        self.fields["add_tags"].queryset = Tag.objects.for_org(org)
        self.fields["add_tags"].widget.attrs["data-placeholder"] = "Add tags…"


class FacebookPageForm(KeepSecretsMixin, OrgModelForm):
    secret_fields = ("access_token",)

    class Meta:
        model = FacebookPage
        fields = ["page_id", "name", "access_token", "is_active"]
        widgets = {"access_token": SecretInput()}
        labels = {"page_id": "Page ID", "name": "Page name", "access_token": "Page access token", "is_active": "Import leads from this page"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["access_token"].widget.attrs["placeholder"] = "EAAG…"

    def clean_page_id(self):
        page_id = self.cleaned_data["page_id"].strip()
        if not page_id.isdigit():
            raise forms.ValidationError("The Page ID is a number, e.g. 104123456789012.")
        if FacebookPage.objects.filter(page_id=page_id).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("This page is already connected to a workspace.")
        return page_id

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("access_token"):
            self.add_error("access_token", "Paste the page access token.")
        return cleaned

    def validate_unique(self):
        pass  # page_id uniqueness is checked (with a friendlier message) in clean_page_id


class CalendarFeedForm(forms.ModelForm):
    class Meta:
        model = CalendarFeed
        fields = ["include_deals"]
