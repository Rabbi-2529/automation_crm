from .base import *  # noqa: F403

DEBUG = True
ALLOWED_HOSTS = ALLOWED_HOSTS or ["localhost", "127.0.0.1"]  # noqa: F405
STORAGES["staticfiles"]["BACKEND"] = "django.contrib.staticfiles.storage.StaticFilesStorage"  # noqa: F405
if EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend":  # noqa: F405
    EMAIL_BACKEND = "apps.core.mail.SafeConsoleEmailBackend"
