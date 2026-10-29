from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, FormView, ListView, UpdateView

from .forms import InviteMemberForm, MembershipForm, OrganizationForm, TeamForm
from .middleware import SESSION_KEY, active_memberships
from .mixins import AdminRequiredMixin, ManagerRequiredMixin, OrgMemberRequiredMixin
from .models import Membership, Team
from .services import create_organization, invite_member


class OrganizationCreateView(LoginRequiredMixin, FormView):
    template_name = "orgs/org_create.html"
    form_class = OrganizationForm

    def form_valid(self, form):
        org = create_organization(self.request.user, **form.cleaned_data)
        self.request.session[SESSION_KEY] = org.pk
        messages.success(self.request, f"Workspace “{org.name}” created.")
        return redirect("core:dashboard")


class OrganizationSettingsView(AdminRequiredMixin, UpdateView):
    template_name = "orgs/org_settings.html"
    form_class = OrganizationForm
    success_url = reverse_lazy("orgs:settings")

    def get_object(self, queryset=None):
        return self.request.org

    def form_valid(self, form):
        messages.success(self.request, "Organization settings saved.")
        return super().form_valid(form)


class SwitchOrganizationView(LoginRequiredMixin, View):
    def post(self, request, org_id):
        membership = get_object_or_404(active_memberships(request.user), org_id=org_id)
        request.session[SESSION_KEY] = membership.org_id
        messages.info(request, f"Switched to {membership.org.name}.")
        return redirect("core:dashboard")


class MemberListView(ManagerRequiredMixin, ListView):
    template_name = "orgs/member_list.html"
    context_object_name = "memberships"

    def get_queryset(self):
        return (
            Membership.objects.filter(org=self.request.org)
            .select_related("user")
            .prefetch_related("teams")
            .order_by("-is_active", "user__first_name", "user__email")
        )


class MemberInviteView(AdminRequiredMixin, FormView):
    template_name = "orgs/member_invite.html"
    form_class = InviteMemberForm
    success_url = reverse_lazy("orgs:members")

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "org": self.request.org}

    def form_valid(self, form):
        membership = invite_member(org=self.request.org, invited_by=self.request.user, request=self.request, **form.cleaned_data)
        messages.success(self.request, f"Invitation sent to {membership.user.email}.")
        return super().form_valid(form)


class MemberUpdateView(AdminRequiredMixin, UpdateView):
    template_name = "orgs/member_form.html"
    form_class = MembershipForm
    context_object_name = "membership"
    success_url = reverse_lazy("orgs:members")

    def get_queryset(self):
        return Membership.objects.filter(org=self.request.org).select_related("user")

    def form_valid(self, form):
        messages.success(self.request, f"Updated {self.object.user.display_name}.")
        return super().form_valid(form)


class TeamListView(OrgMemberRequiredMixin, ListView):
    template_name = "orgs/team_list.html"
    context_object_name = "teams"

    def get_queryset(self):
        return Team.objects.for_org(self.request.org).select_related("lead__user").prefetch_related("members__user")


class TeamFormMixin(ManagerRequiredMixin):
    model = Team
    form_class = TeamForm
    template_name = "orgs/team_form.html"
    success_url = reverse_lazy("orgs:teams")

    def get_queryset(self):
        return Team.objects.for_org(self.request.org)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "org": self.request.org}

    def form_valid(self, form):
        messages.success(self.request, f"Team “{form.instance.name}” saved.")
        return super().form_valid(form)


class TeamCreateView(TeamFormMixin, CreateView):
    pass


class TeamUpdateView(TeamFormMixin, UpdateView):
    pass


class TeamDeleteView(ManagerRequiredMixin, DeleteView):
    template_name = "orgs/team_confirm_delete.html"
    success_url = reverse_lazy("orgs:teams")

    def get_queryset(self):
        return Team.objects.for_org(self.request.org)

    def form_valid(self, form):
        messages.success(self.request, f"Team “{self.object.name}” deleted.")
        return super().form_valid(form)
