from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    CELERY_TASK_ALWAYS_EAGER=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")

# Dev/test fallback only; prod.py re-reads SECRET_KEY without a default.
SECRET_KEY = env("SECRET_KEY", default="insecure-dev-only-key")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "rest_framework",
    "drf_spectacular",
    "apps.core",
    "apps.accounts",
    "apps.orgs",
    "apps.contacts",
    "apps.activities",
    "apps.pipeline",
    "apps.tasks",
    "apps.messaging",
    "apps.automation",
    "apps.leads",
    "apps.campaigns",
    "apps.reports",
    "apps.integrations",
    "apps.assistant",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.orgs.middleware.CurrentOrganizationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.orgs.context_processors.current_organization",
                "apps.tasks.context_processors.task_counts",
                "apps.messaging.context_processors.inbox_counts",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}
DATABASES["default"]["ATOMIC_REQUESTS"] = True

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["apps.accounts.backends.EmailBackend"]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Dhaka"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

vars().update(env.email_url("EMAIL_URL", default="consolemail://"))
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="ThingBD CRM <no-reply@thingbd.com>")

# Celery
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=CELERY_BROKER_URL)
CELERY_TASK_ALWAYS_EAGER = env("CELERY_TASK_ALWAYS_EAGER")
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "send-task-reminders": {"task": "apps.tasks.tasks.send_task_reminders", "schedule": 60.0},
    "fetch-inbound-email": {"task": "apps.messaging.tasks.fetch_all_inboxes", "schedule": 120.0},
    "resume-workflow-waits": {"task": "apps.automation.tasks.resume_waiting_runs", "schedule": 60.0},
    "scan-scheduled-triggers": {"task": "apps.automation.tasks.scan_scheduled_triggers", "schedule": 900.0},
    "process-sequences": {"task": "apps.campaigns.tasks.process_sequences", "schedule": 60.0},
    "process-broadcasts": {"task": "apps.campaigns.tasks.process_broadcasts", "schedule": 60.0},
    "send-scheduled-reports": {"task": "apps.reports.tasks.send_scheduled_reports", "schedule": 900.0},
    "retry-webhook-deliveries": {"task": "apps.integrations.tasks.retry_webhook_deliveries", "schedule": 60.0},
    "prune-webhook-deliveries": {"task": "apps.integrations.tasks.prune_webhook_deliveries", "schedule": 86400.0},
    "retry-facebook-leads": {"task": "apps.integrations.tasks.retry_facebook_leads", "schedule": 900.0},
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

# Uploads (Phase 1)
ATTACHMENT_MAX_MB = env.int("ATTACHMENT_MAX_MB", default=10)
IMPORT_MAX_MB = env.int("IMPORT_MAX_MB", default=10)

# Absolute links in emails sent outside a request (task reminders).
SITE_URL = env("SITE_URL", default="http://localhost:8000")

# Encrypts stored SMTP/IMAP passwords and SMS API keys. Generate once per environment with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Changing it later makes saved secrets unreadable (they must be re-entered).
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", default="")

# REST API (Phase 7): workspace API keys only (no session auth), JSON in and out, rate limited per key.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.integrations.api.auth.ApiKeyAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["apps.integrations.api.auth.ApiKeyPermission"],
    "DEFAULT_THROTTLE_CLASSES": ["apps.integrations.api.auth.ApiKeyRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"api_key": env("API_RATE_LIMIT", default="120/min")},
    "DEFAULT_PAGINATION_CLASS": "apps.integrations.api.pagination.Pagination",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "UNAUTHENTICATED_USER": None,
}
SPECTACULAR_SETTINGS = {
    "TITLE": "ThingBD CRM API",
    "DESCRIPTION": "Contacts, companies, deals, tasks, notes and the timeline of one workspace. "
                   "Create a key under Settings → Integrations and send it as `Authorization: Bearer tbd_…`.",
    "VERSION": "1.0.0",
    "SERVE_AUTHENTICATION": [],
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"],
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api/v1",
}

# AI assistant platform default. Workspaces can pick their own under Workspace → AI assistant.
# Any OpenAI-compatible API works: Ollama (free, open-source, self-hosted), Groq, OpenRouter, LM Studio, OpenAI…
# AI_PROVIDER: builtin | ollama | groq | openrouter | openai_compatible. Empty base URL/model = the provider's defaults.
AI_PROVIDER = env("AI_PROVIDER", default="ollama")
AI_BASE_URL = env("AI_BASE_URL", default="")
AI_MODEL = env("AI_MODEL", default="")
AI_API_KEY = env("AI_API_KEY", default="")

# Outgoing webhooks and workflow webhooks refuse private/localhost addresses unless this is on (local development).
ALLOW_PRIVATE_WEBHOOKS = env.bool("ALLOW_PRIVATE_WEBHOOKS", default=False)
