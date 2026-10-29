"""API key authentication, scope/role permissions and per-key rate limiting."""

from datetime import timedelta

from django.utils import timezone
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework import exceptions, permissions
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.throttling import SimpleRateThrottle

from apps.orgs.models import Membership, Role

from ..models import ApiKey, ApiScope

TOUCH_EVERY = timedelta(minutes=1)


class ApiKeyAuthentication(BaseAuthentication):
    """`Authorization: Bearer tbd_…` (or `X-Api-Key: tbd_…`). Sets request.org / request.membership like the web app."""

    def authenticate(self, request):
        header = get_authorization_header(request).decode(errors="ignore")
        raw = header[7:].strip() if header.lower().startswith("bearer ") else request.headers.get("X-Api-Key", "").strip()
        if not raw:
            return None
        key = ApiKey.lookup(raw)
        if key is None:
            raise exceptions.AuthenticationFailed("Invalid or revoked API key.")
        if not key.org.is_active:
            raise exceptions.AuthenticationFailed("This workspace is inactive.")
        membership = Membership.objects.filter(org=key.org, user=key.created_by, is_active=True).first()
        if membership is None:
            raise exceptions.AuthenticationFailed("The person who created this key is no longer in the workspace.")
        now = timezone.now()
        if key.last_used_at is None or now - key.last_used_at > TOUCH_EVERY:
            ApiKey.objects.filter(pk=key.pk).update(last_used_at=now)
        for target in (request, request._request):
            target.org, target.membership = key.org, membership
        return key.created_by, key

    def authenticate_header(self, request):
        return 'Bearer realm="api"'


class ApiKeyPermission(permissions.BasePermission):
    """Read-only keys can only read; deleting needs a manager's key (as in the web app)."""

    message = "This API key is read-only."

    def has_permission(self, request, view):
        key = request.auth
        if not isinstance(key, ApiKey):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        if key.scope != ApiScope.WRITE:
            return False
        if request.method == "DELETE" and not request.membership.has_role(Role.MANAGER):
            self.message = "Deleting needs a key created by a manager or admin."
            return False
        return True


class ApiKeyRateThrottle(SimpleRateThrottle):
    scope = "api_key"

    def get_cache_key(self, request, view):
        if isinstance(request.auth, ApiKey):
            return self.cache_format % {"scope": self.scope, "ident": request.auth.pk}
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class ApiKeyScheme(OpenApiAuthenticationExtension):
    target_class = "apps.integrations.api.auth.ApiKeyAuthentication"
    name = "ApiKey"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer", "description": "A workspace API key: `Authorization: Bearer tbd_…`"}
