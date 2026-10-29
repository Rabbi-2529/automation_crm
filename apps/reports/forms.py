from datetime import date

from django import forms

from apps.contacts.forms import org_users

from .models import ReportKind, ReportSubscription, SalesTarget, TargetPeriod


class TargetForm(forms.ModelForm):
    month = forms.DateField(widget=forms.DateInput(attrs={"type": "month"}, format="%Y-%m"), input_formats=["%Y-%m"],
                            label="Month (or first month of the quarter)")

    class Meta:
        model = SalesTarget
        fields = ["user", "metric", "period", "value"]
        labels = {"user": "Who", "value": "Target"}

    def __init__(self, *args, org, **kwargs):
        self.org = org
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = org_users(org)
        self.fields["user"].label_from_instance = lambda u: u.display_name
        self.fields["user"].empty_label = "Whole team"
        if self.instance.pk:
            self.initial["month"] = self.instance.period_start

    def clean(self):
        cleaned = super().clean()
        month = cleaned.get("month")
        if not month:
            return cleaned
        start = date(month.year, month.month, 1)
        if cleaned.get("period") == TargetPeriod.QUARTER and start.month not in (1, 4, 7, 10):
            self.add_error("month", "Quarters start in January, April, July or October.")
        if cleaned.get("value") is not None and cleaned["value"] <= 0:
            self.add_error("value", "Use a positive number.")
        clash = SalesTarget.objects.filter(org=self.org, user=cleaned.get("user"), metric=cleaned.get("metric"),
                                           period=cleaned.get("period"), period_start=start).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("That target already exists. Edit it instead.")
        self.instance.period_start = start
        return cleaned

    def save(self, commit=True):
        self.instance.org = self.org
        return super().save(commit)


class SubscriptionForm(forms.ModelForm):
    class Meta:
        model = ReportSubscription
        fields = ["kind", "frequency", "weekday", "hour", "is_active"]
        widgets = {"hour": forms.NumberInput(attrs={"min": 0, "max": 23})}
        labels = {"kind": "Report", "is_active": "Active"}

    def __init__(self, *args, org, user, can_summary, **kwargs):
        self.org, self.user = org, user
        super().__init__(*args, **kwargs)
        if not can_summary:
            self.fields["kind"].choices = [(ReportKind.DIGEST, ReportKind.DIGEST.label)]

    def clean_hour(self):
        hour = self.cleaned_data["hour"]
        if not 0 <= hour <= 23:
            raise forms.ValidationError("Use an hour from 0 to 23.")
        return hour

    def clean(self):
        cleaned = super().clean()
        clash = ReportSubscription.objects.filter(org=self.org, user=self.user, kind=cleaned.get("kind"),
                                                  frequency=cleaned.get("frequency")).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("You already get that report on that schedule.")
        return cleaned

    def save(self, commit=True):
        self.instance.org, self.instance.user = self.org, self.user
        return super().save(commit)
