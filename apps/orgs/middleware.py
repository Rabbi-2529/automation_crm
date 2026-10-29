from .models import Membership

SESSION_KEY = "current_org_id"


def active_memberships(user):
    return Membership.objects.filter(user=user, is_active=True, org__is_active=True).select_related("org")


class CurrentOrganizationMiddleware:
    """Sets `request.org` and `request.membership` from the session, falling back to the user's first org."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.org = None
        request.membership = None
        if request.user.is_authenticated:
            memberships = active_memberships(request.user)
            wanted = request.session.get(SESSION_KEY)
            membership = (wanted and memberships.filter(org_id=wanted).first()) or memberships.first()
            if membership:
                request.org = membership.org
                request.membership = membership
                if wanted != membership.org_id:
                    request.session[SESSION_KEY] = membership.org_id
            elif wanted:
                request.session.pop(SESSION_KEY, None)
        return self.get_response(request)
