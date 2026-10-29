from zoneinfo import available_timezones

from django import forms

from .models import Membership, Organization, Role, Team
from .services import other_active_admins

TIMEZONE_CHOICES = [(tz, tz) for tz in sorted(available_timezones())]


class OrganizationForm(forms.ModelForm):
    timezone = forms.ChoiceField(choices=TIMEZONE_CHOICES, initial="Asia/Dhaka")

    class Meta:
        model = Organization
        fields = ["name", "timezone", "currency"]

    def clean_currency(self):
        return self.cleaned_data["currency"].upper()


class InviteMemberForm(forms.Form):
    email = forms.EmailField()
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    role = forms.ChoiceField(choices=Role.choices, initial=Role.AGENT)

    def __init__(self, *args, org, **kwargs):
        self.org = org
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if Membership.objects.filter(org=self.org, user__email=email, is_active=True).exists():
            raise forms.ValidationError("This person is already a member of this organization.")
        return email


class MembershipForm(forms.ModelForm):
    class Meta:
        model = Membership
        fields = ["role", "is_active"]
        labels = {"is_active": "Active member"}

    def clean(self):
        cleaned = super().clean()
        was_admin = self.instance.role == Role.ADMIN and self.instance.is_active
        stays_admin = cleaned.get("role") == Role.ADMIN and cleaned.get("is_active")
        if was_admin and not stays_admin and not other_active_admins(self.instance).exists():
            raise forms.ValidationError("An organization needs at least one active admin.")
        return cleaned


class TeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ["name", "description", "lead", "members"]
        widgets = {"members": forms.CheckboxSelectMultiple}

    def __init__(self, *args, org, **kwargs):
        self.org = org
        super().__init__(*args, **kwargs)
        memberships = Membership.objects.filter(org=org, is_active=True).select_related("user")
        self.fields["lead"].queryset = memberships
        self.fields["members"].queryset = memberships
        self.fields["lead"].label_from_instance = lambda m: m.user.display_name
        self.fields["members"].label_from_instance = lambda m: m.user.display_name

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        clash = Team.objects.for_org(self.org).filter(name__iexact=name).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("A team with this name already exists.")
        return name

    def save(self, commit=True):
        self.instance.org = self.org
        return super().save(commit)
