from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, UserCreationForm

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


class ThrottledLoginForm(AuthenticationForm):
    """Refuses to check the password while this email/IP is locked out after too many failures."""

    error_messages = {
        **AuthenticationForm.error_messages,
        "locked": "Too many failed attempts. Wait %(minutes)s minutes and try again, or reset your password.",
    }

    def clean(self):
        from django.conf import settings

        from . import throttle

        email = (self.data.get("username") or "").strip().lower()
        if throttle.login_locked(self.request, email):
            raise forms.ValidationError(self.error_messages["locked"], code="locked",
                                        params={"minutes": settings.LOGIN_LOCKOUT_MINUTES})
        try:
            cleaned = super().clean()
        except forms.ValidationError:
            throttle.record_failure(self.request, email)
            raise
        throttle.clear(self.request, email)
        return cleaned


class ThrottledPasswordResetForm(PasswordResetForm):
    """Silently stops sending reset emails after a few requests per hour from one IP (the page looks the same)."""

    def save(self, *args, request=None, **kwargs):
        from . import throttle

        if request is not None and not throttle.reset_allowed(request):
            return
        return super().save(*args, request=request, **kwargs)
