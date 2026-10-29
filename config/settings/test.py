import tempfile
from pathlib import Path

from .base import *  # noqa: F403

DEBUG = False
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CELERY_TASK_ALWAYS_EAGER = True
CELERY_RESULT_BACKEND = "cache+memory://"
STORAGES["staticfiles"]["BACKEND"] = "django.contrib.staticfiles.storage.StaticFilesStorage"  # noqa: F405
MEDIA_ROOT = Path(tempfile.mkdtemp(prefix="crm-test-media-"))  # uploads in tests never touch real media/
