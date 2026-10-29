from django import forms

from apps.messaging.forms import KeepSecretsMixin, SecretInput

from .models import AssistantSettings
from .providers import PRESETS


class AssistantSettingsForm(KeepSecretsMixin, forms.ModelForm):
    secret_fields = ("api_key",)
    provider = forms.ChoiceField(required=False, label="AI provider")

    class Meta:
        model = AssistantSettings
        fields = ["provider", "base_url", "model", "api_key", "temperature", "help_enabled", "reply_drafts_enabled",
                  "business_context", "daily_limit"]
        widgets = {"api_key": SecretInput(), "business_context": forms.Textarea(attrs={"rows": 6}),
                   "temperature": forms.NumberInput(attrs={"step": "0.05", "min": "0", "max": "1.5"})}
        labels = {"base_url": "API address", "model": "Model", "api_key": "API key", "help_enabled": "Help assistant for staff",
                  "reply_drafts_enabled": "“Draft with AI” in the inbox", "business_context": "About your business",
                  "daily_limit": "AI answers per person per day", "temperature": "Creativity (0 = precise, 1 = creative)"}
        help_texts = {"base_url": "Leave empty to use the provider's usual address.",
                      "model": "Leave empty for the provider's suggested model."}

    def __init__(self, *args, platform_label="", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["provider"].choices = [("", f"Platform default ({platform_label})")] + [(k, p.label) for k, p in PRESETS.items()]
        for name in ("base_url", "model"):
            self.fields[name].widget.attrs["data-preset-field"] = name

    def clean(self):
        cleaned = super().clean()
        provider = cleaned.get("provider")
        preset = PRESETS.get(provider)
        if provider == "openai_compatible":
            for name in ("base_url", "model"):
                if not cleaned.get(name):
                    self.add_error(name, "Required for an OpenAI-compatible API.")
        if preset and preset.needs_key and not cleaned.get("api_key"):
            self.add_error("api_key", f"{preset.label.split(':')[0]} needs an API key.")
        base = (cleaned.get("base_url") or "").strip()
        if base and not base.startswith(("http://", "https://")):
            self.add_error("base_url", "Start with http:// or https://")
        cleaned["base_url"] = base.rstrip("/")
        temperature = cleaned.get("temperature")
        if temperature is not None and not 0 <= temperature <= 1.5:
            self.add_error("temperature", "Use a value between 0 and 1.5.")
        return cleaned
