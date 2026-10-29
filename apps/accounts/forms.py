from django import forms
from django.contrib.auth.forms import UserCreationForm

from apps.orgs.forms import TIMEZONE_CHOICES

from .models import User


class SignupForm(UserCreationForm):
    """Creates the user and their first workspace in one step."""

    organization_name = forms.CharField(max_length=150, label="Company / workspace name")
    field_order = ["first_name", "last_name", "email", "organization_name", "password1", "password2"]

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].required = True

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email


class ProfileForm(forms.ModelForm):
    timezone = forms.ChoiceField(choices=TIMEZONE_CHOICES)

    class Meta:
        model = User
        fields = ["first_name", "last_name", "phone", "timezone"]
