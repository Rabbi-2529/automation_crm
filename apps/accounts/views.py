from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import FormView, UpdateView

from apps.orgs.middleware import SESSION_KEY
from apps.orgs.services import create_organization

from .forms import ProfileForm, SignupForm


class SignupView(FormView):
    template_name = "accounts/signup.html"
    form_class = SignupForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("core:dashboard")
        return super().dispatch(request, *args, **kwargs)

    @transaction.atomic
    def form_valid(self, form):
        user = form.save()
        org = create_organization(user, form.cleaned_data["organization_name"])
        login(self.request, user, backend="apps.accounts.backends.EmailBackend")
        self.request.session[SESSION_KEY] = org.pk
        messages.success(self.request, f"Welcome! Your workspace “{org.name}” is ready.")
        return redirect("core:dashboard")


class ProfileView(LoginRequiredMixin, UpdateView):
    template_name = "accounts/profile.html"
    form_class = ProfileForm
    success_url = reverse_lazy("accounts:profile")

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        messages.success(self.request, "Profile saved.")
        return super().form_valid(form)
