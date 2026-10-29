from .middleware import active_memberships


def current_organization(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    return {
        "current_org": getattr(request, "org", None),
        "current_membership": getattr(request, "membership", None),
        "user_memberships": active_memberships(user),
    }
