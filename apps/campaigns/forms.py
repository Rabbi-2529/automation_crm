from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone

from apps.contacts.forms import OrgModelForm
from apps.messaging.models import MessageTemplate

from .models import Broadcast, Segment, Sequence, SequenceStep


class SegmentForm(OrgModelForm):
    class Meta:
        model = Segment
        fields = ["name", "description"]

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if Segment.objects.for_org(self.org).filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("A segment with this name already exists.")
        return name


class SequenceForm(OrgModelForm):
    class Meta:
        model = Sequence
        fields = ["name", "description", "is_active", "exit_on_reply", "send_as"]
        labels = {"is_active": "Active (sending)", "exit_on_reply": "Stop when the contact replies"}


class StepForm(forms.ModelForm):
    class Meta:
        model = SequenceStep
        fields = ["position", "delay_days", "delay_hours", "channel", "template", "subject", "body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 4}), "position": forms.HiddenInput}

    def clean(self):
        cleaned = super().clean()
        if self.cleaned_data.get("DELETE"):
            return cleaned
        if not cleaned.get("body") and not cleaned.get("template"):
            raise forms.ValidationError("Write a message or pick a template.")
        if cleaned.get("channel") == "email" and not cleaned.get("subject") and not cleaned.get("template"):
            self.add_error("subject", "Emails need a subject.")
        return cleaned


class BaseStepFormSet(BaseInlineFormSet):
    def __init__(self, *args, org, **kwargs):
        self.org = org
        super().__init__(*args, **kwargs)
        for form in self.forms:
            form.fields["template"].queryset = MessageTemplate.objects.for_org(org)
            form.fields["template"].required = False

    def clean(self):
        super().clean()
        kept = [f for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]
        if not any(self.errors) and not kept:
            raise forms.ValidationError("Add at least one step.")


StepFormSet = inlineformset_factory(Sequence, SequenceStep, form=StepForm, formset=BaseStepFormSet, extra=1, can_delete=True)


class BroadcastForm(OrgModelForm):
    class Meta:
        model = Broadcast
        fields = ["name", "channel", "segment", "template", "subject", "body", "send_as"]
        widgets = {"body": forms.Textarea(attrs={"rows": 8})}
        help_texts = {"body": "Variables like {{ contact.first_name }} work. Marketing emails get an unsubscribe link automatically."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["segment"].queryset = Segment.objects.for_org(self.org)
        self.fields["template"].queryset = MessageTemplate.objects.for_org(self.org)

    def clean(self):
        cleaned = super().clean()
        template = cleaned.get("template")
        if template and template.channel != cleaned.get("channel"):
            self.add_error("template", "Pick a template for the same channel.")
        if not cleaned.get("body") and not template:
            self.add_error("body", "Write a message or pick a template.")
        if cleaned.get("channel") == "email" and not cleaned.get("subject") and not template:
            self.add_error("subject", "Emails need a subject.")
        return cleaned


class ScheduleForm(forms.Form):
    when = forms.DateTimeField(required=False, widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
                               input_formats=["%Y-%m-%dT%H:%M"], label="Send at", help_text="Leave empty to send now.")

    def clean_when(self):
        when = self.cleaned_data.get("when")
        if when and when < timezone.now():
            raise forms.ValidationError("Pick a time in the future (or leave empty to send now).")
        return when


class EnrollForm(forms.Form):
    sequence = forms.ModelChoiceField(queryset=Sequence.objects.none(), empty_label="Pick a sequence")

    def __init__(self, *args, org, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sequence"].queryset = Sequence.objects.for_org(org).filter(is_active=True)
