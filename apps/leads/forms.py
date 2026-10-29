from django import forms

from apps.contacts.forms import OrgModelForm, org_users
from apps.contacts.models import LifecycleStage, Tag

from .capture import available_fields
from .models import LeadSettings, ScoringRule, WebForm


class WebFormSettingsForm(OrgModelForm):
    lifecycle_stage = forms.ChoiceField(choices=LifecycleStage.choices, initial=LifecycleStage.LEAD, label="New contacts start as")

    class Meta:
        model = WebForm
        fields = ["name", "is_active", "title", "intro", "submit_label", "success_message", "redirect_url", "consent_text",
                  "lifecycle_stage", "default_owner", "add_tags"]
        widgets = {"intro": forms.Textarea(attrs={"rows": 2}), "success_message": forms.Textarea(attrs={"rows": 2})}
        labels = {"is_active": "Accepting submissions", "title": "Heading shown on the form", "add_tags": "Tag new submissions with"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["default_owner"].queryset = org_users(self.org)
        self.fields["default_owner"].label_from_instance = lambda u: u.display_name
        self.fields["add_tags"].queryset = Tag.objects.for_org(self.org)
        self.fields["add_tags"].widget.attrs["data-placeholder"] = "No tags"


def parse_field_table(post, org):
    """The builder posts include/label/required/position per available field; returns WebForm.fields or raises."""
    chosen = []
    for key, default_label, _ in available_fields(org):
        slug = key.replace(":", "__")
        if not post.get(f"include__{slug}"):
            continue
        try:
            position = int(post.get(f"position__{slug}") or 0)
        except ValueError:
            position = 0
        chosen.append({"key": key, "label": (post.get(f"label__{slug}") or default_label).strip()[:100],
                       "required": bool(post.get(f"required__{slug}")), "position": position})
    chosen.sort(key=lambda f: f["position"])
    if not any(f["key"] in ("email", "phone") for f in chosen):
        raise forms.ValidationError("Include an Email or Phone field so submissions can become contacts.")
    return [{k: v for k, v in f.items() if k != "position"} for f in chosen]


class ScoringRuleForm(OrgModelForm):
    class Meta:
        model = ScoringRule
        fields = ["name", "kind", "points", "event", "max_times", "is_active"]
        widgets = {"kind": forms.RadioSelect}

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("kind") == ScoringRule.EVENT and not cleaned.get("event"):
            self.add_error("event", "Pick the behaviour that earns these points.")
        if cleaned.get("points") == 0:
            self.add_error("points", "Use a positive or negative number.")
        return cleaned


class LeadSettingsForm(forms.ModelForm):
    class Meta:
        model = LeadSettings
        fields = ["warm_threshold", "hot_threshold"]

    def clean(self):
        cleaned = super().clean()
        if (cleaned.get("warm_threshold") or 0) >= (cleaned.get("hot_threshold") or 0):
            raise forms.ValidationError("Hot must need more points than warm.")
        return cleaned
