from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    name = "apps.integrations"
    label = "integrations"

    def ready(self):
        from .api import auth  # noqa: F401 - registers the OpenAPI security scheme for API keys
