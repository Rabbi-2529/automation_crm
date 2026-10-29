from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

DEBUG = False
SECRET_KEY = env("SECRET_KEY")  # noqa: F405 - required in production
if len(SECRET_KEY) < 40 or SECRET_KEY.startswith("change-me"):
    raise ImproperlyConfigured("Set a long random SECRET_KEY (python -c \"import secrets; print(secrets.token_urlsafe(50))\").")
if not ALLOWED_HOSTS:  # noqa: F405
    raise ImproperlyConfigured("Set ALLOWED_HOSTS to your domain, e.g. crm.thingbd.com.")
# Saved SMTP/SMS/WhatsApp/API secrets are encrypted with this; without it they'd hang off SECRET_KEY.
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY")  # noqa: F405

DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=60)  # noqa: F405
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True  # noqa: F405

# Shared cache (Redis) so login throttling and the API rate limit count across all gunicorn workers.
CACHES = {"default": env.cache("CACHE_URL", default="redis://redis:6379/1")}  # noqa: F405

# HTTPS: Caddy (docker-compose.prod.yml) terminates TLS and forwards X-Forwarded-Proto.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)  # noqa: F405
SECURE_REDIRECT_EXEMPT = [r"^health/$"]  # container health checks talk plain HTTP
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[f"https://{h}" for h in ALLOWED_HOSTS if h not in ("*",)])  # noqa: F405
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)  # noqa: F405
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)  # noqa: F405
SECURE_HSTS_PRELOAD = False
# Deliberate: other thingbd.com subdomains may not all be HTTPS-only, and preload is hard to undo.
SILENCED_SYSTEM_CHECKS = ["security.W021"] + ([] if SECURE_HSTS_INCLUDE_SUBDOMAINS else ["security.W005"])
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"  # public web forms opt out individually so they can be embedded
SESSION_COOKIE_AGE = env.int("SESSION_COOKIE_AGE", default=60 * 60 * 24 * 14)  # noqa: F405

# Compiled Tailwind + vendored JS/CSS built into the Docker image (no browser compiler, no CDN scripts).
COMPILED_ASSETS = env.bool("COMPILED_ASSETS", default=True)  # noqa: F405

# Errors: logged to stdout (docker compose logs) and emailed to ADMINS if set (comma-separated addresses).
ADMINS = [(address, address) for address in env.list("ADMINS", default=[])]  # noqa: F405
SERVER_EMAIL = env("SERVER_EMAIL", default=DEFAULT_FROM_EMAIL)  # noqa: F405
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
        "mail_admins": {"class": "django.utils.log.AdminEmailHandler", "level": "ERROR"},
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},  # noqa: F405
    "loggers": {
        "django.request": {"handlers": ["console", "mail_admins"], "level": "ERROR", "propagate": False},
        "django.security": {"handlers": ["console", "mail_admins"], "level": "WARNING", "propagate": False},
    },
}
