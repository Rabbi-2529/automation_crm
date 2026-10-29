from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.urls import reverse_lazy

from apps.contacts.custom_fields import CustomFieldFormMixin, definitions_for
from apps.contacts.forms import OrgModelForm
from apps.contacts.models import Company, Contact, Entity
from apps.core.widgets import LookupWidget

from .models import Deal, LostReason, Pipeline, Stage


class StageChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, stage):
        return stage.name


def grouped_stage_choices(stages):
    """<optgroup> per pipeline so one select covers every pipeline."""
    groups = {}
    for stage in stages:
        groups.setdefault(stage.pipeline.name, []).append((stage.pk, stage.name))
    return [("", "———")] + list(groups.items())


class DealForm(CustomFieldFormMixin, OrgModelForm):
    stage = StageChoiceField(queryset=Stage.objects.none(), help_text="The pipeline follows from the stage.")
    contact = forms.ModelChoiceField(
        queryset=Contact.objects.none(), required=False,
        widget=LookupWidget(reverse_lazy("contacts:contact_lookup"), placeholder="Search contacts…"),
    )
    company = forms.ModelChoiceField(
        queryset=Company.objects.none(), required=False,
        widget=LookupWidget(reverse_lazy("contacts:company_lookup"), placeholder="Search companies…"),
        help_text="Filled from the contact's company when left empty.",
    )

    class Meta:
        model = Deal
        fields = ["title", "value", "stage", "contact", "company", "owner", "expected_close_date", "tags"]
        widgets = {"expected_close_date": forms.DateInput(attrs={"type": "date"})}
        labels = {"value": "Value"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        stages = Stage.objects.for_org(self.org).select_related("pipeline").order_by("pipeline__position", "pipeline__name", "position")
        self.fields["stage"].queryset = stages
        self.fields["stage"].choices = grouped_stage_choices(stages)
        for name, model in (("contact", Contact), ("company", Company)):
            self.fields[name].queryset = model.objects.for_org(self.org)
            self.fields[name].widget.queryset = self.fields[name].queryset
        self.fields["value"].label = f"Value ({self.org.currency})"
        self.add_custom_fields(definitions_for(self.org, Entity.DEAL))

    def clean_value(self):
        value = self.cleaned_data["value"]
        if value is not None and value < 0:
            raise forms.ValidationError("Value can't be negative.")
        return value or 0

    def save(self, commit=True):
        deal = self.instance
        deal.pipeline = self.cleaned_data["stage"].pipeline
        if not deal.company and deal.contact and deal.contact.company_id:
            deal.company = deal.contact.company
        deal.custom_fields = self.custom_field_values()
        return super().save(commit)


class LostForm(forms.Form):
    reason = forms.ModelChoiceField(queryset=LostReason.objects.none(), empty_label="Pick a reason")
    note = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False, label="What happened? (optional)")

    def __init__(self, *args, org, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].queryset = LostReason.objects.for_org(org)
        self.fields["reason"].required = self.fields["reason"].queryset.exists()


class PipelineForm(OrgModelForm):
    class Meta:
        model = Pipeline
        fields = ["name", "is_default", "position"]
        labels = {"is_default": "Default pipeline"}

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if Pipeline.objects.for_org(self.org).filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("A pipeline with this name already exists.")
        return name


class BaseStageFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        kept = [f for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]
        if not kept:
            raise forms.ValidationError("A pipeline needs at least one stage.")
        for form in self.forms:
            if form.cleaned_data.get("DELETE") and form.instance.pk and form.instance.deals.exists():
                raise forms.ValidationError(
                    f"“{form.instance.name}” still has {form.instance.deals.count()} deal(s). Move them to another stage first."
                )


StageFormSet = inlineformset_factory(
    Pipeline, Stage, formset=BaseStageFormSet, fields=["name", "probability", "rot_days", "position"], extra=2, can_delete=True,
    widgets={
        "probability": forms.NumberInput(attrs={"min": 0, "max": 100}),
        "rot_days": forms.NumberInput(attrs={"min": 1, "placeholder": "—"}),
        "position": forms.NumberInput(attrs={"min": 0}),
    },
)


class LostReasonForm(OrgModelForm):
    class Meta:
        model = LostReason
        fields = ["name", "position"]

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if LostReason.objects.for_org(self.org).filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("This reason already exists.")
        return name
