"""Brute-force protection for logins and password-reset requests, counted in the cache (Redis in production)."""

import hashlib

from django.conf import settings
from django.core.cache import cache


def client_ip(request):
    # Behind Caddy the real address is the first X-Forwarded-For hop; Caddy overwrites it, so it can't be spoofed.
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (forwarded.split(",")[0].strip() if forwarded else "") or request.META.get("REMOTE_ADDR", "")


def _key(kind, *parts):
    digest = hashlib.sha256("|".join(p.lower() for p in parts).encode()).hexdigest()[:32]
    return f"throttle:{kind}:{digest}"


def _window():
    return settings.LOGIN_LOCKOUT_MINUTES * 60


def _limits(request, email):
    ip = client_ip(request)
    return [(_key("login", email, ip), settings.LOGIN_MAX_FAILURES), (_key("login-ip", ip), settings.LOGIN_MAX_FAILURES * 4)]


def login_locked(request, email):
    return any(cache.get(key, 0) >= limit for key, limit in _limits(request, email))


def record_failure(request, email):
    for key, _limit in _limits(request, email):
        if cache.add(key, 1, _window()):
            continue
        try:
            cache.incr(key)
        except ValueError:  # expired between add() and incr()
            cache.set(key, 1, _window())


def clear(request, email):
    cache.delete(_limits(request, email)[0][0])


def reset_allowed(request, per_hour=5):
    """Password-reset emails: at most `per_hour` requests per IP."""
    key = _key("reset", client_ip(request))
    if cache.add(key, 1, 3600):
        return True
    try:
        return cache.incr(key) <= per_hour
    except ValueError:
        return True
