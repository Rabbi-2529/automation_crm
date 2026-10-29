from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from .models import Role


class OrgMemberRequiredMixin(LoginRequiredMixin):
    """Requires login and an active org membership. Raise `min_role` to restrict further."""

    min_role = Role.AGENT

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.membership is None:
            return redirect("orgs:create")
        if not request.membership.has_role(self.min_role):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class ManagerRequiredMixin(OrgMemberRequiredMixin):
    min_role = Role.MANAGER


class AdminRequiredMixin(OrgMemberRequiredMixin):
    min_role = Role.ADMIN


class OrgScopedQuerysetMixin:
    """For generic views on OrgScopedModel subclasses: objects from other orgs 404."""

    def get_queryset(self):
        return self.model.objects.for_org(self.request.org)


class OrgFormKwargsMixin:
    """Passes `org=request.org` to forms built on OrgModelForm."""

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "org": self.request.org}
