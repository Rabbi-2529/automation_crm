from datetime import datetime, time

from django import forms
from django.utils import timezone

from apps.contacts.forms import org_users
from apps.contacts.models import Company, Contact
from apps.pipeline.models import Deal

from .models import Task

DEFAULT_DUE_TIME = time(17, 0)  # date without a time = end of the working day


class TaskForm(forms.ModelForm):
    due_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Due date")
    due_time = forms.TimeField(required=False, widget=forms.TimeInput(attrs={"type": "time"}), label="Time",
                               help_text="Leave empty for 5:00 PM.")
    contact = forms.ModelChoiceField(queryset=Contact.objects.none(), required=False, widget=forms.HiddenInput)
    company = forms.ModelChoiceField(queryset=Company.objects.none(), required=False, widget=forms.HiddenInput)
    deal = forms.ModelChoiceField(queryset=Deal.objects.none(), required=False, widget=forms.HiddenInput)

    class Meta:
        model = Task
        fields = ["title", "kind", "priority", "due_date", "due_time", "reminder", "assignee", "description",
                  "contact", "company", "deal"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, org, **kwargs):
        self.org = org
        super().__init__(*args, **kwargs)
        self.fields["assignee"].queryset = org_users(org)
        self.fields["assignee"].label_from_instance = lambda u: u.display_name
        for name, model in (("contact", Contact), ("company", Company), ("deal", Deal)):
            self.fields[name].queryset = model.objects.for_org(org)
        if self.instance.due_at:
            local = timezone.localtime(self.instance.due_at)
            self.initial.setdefault("due_date", local.date())
            self.initial.setdefault("due_time", local.time().replace(second=0, microsecond=0))

    def clean(self):
        cleaned = super().clean()
        due_date, due_time = cleaned.get("due_date"), cleaned.get("due_time")
        if due_time and not due_date:
            self.add_error("due_date", "Pick a date for this time.")
        if cleaned.get("reminder") and not due_date:
            self.add_error("reminder", "Reminders need a due date.")
        return cleaned

    def save(self, commit=True):
        task = self.instance
        task.org = self.org
        due_date = self.cleaned_data.get("due_date")
        task.due_at = (
            timezone.make_aware(datetime.combine(due_date, self.cleaned_data.get("due_time") or DEFAULT_DUE_TIME))
            if due_date else None
        )
        previous_remind_at = Task.objects.filter(pk=task.pk).values_list("remind_at", flat=True).first() if task.pk else None
        task = super().save(commit)
        if commit and task.remind_at != previous_remind_at and task.reminder_sent_at:
            # Due time or reminder changed after a reminder went out: allow a fresh one.
            Task.objects.filter(pk=task.pk).update(reminder_sent_at=None)
            task.reminder_sent_at = None
        return task


class QuickTaskForm(TaskForm):
    """Title + due date from the inline add box; everything else defaults."""

    class Meta(TaskForm.Meta):
        fields = ["title", "kind", "due_date", "due_time", "assignee", "contact", "company", "deal"]
