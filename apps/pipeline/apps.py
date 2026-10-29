from django.apps import AppConfig


class PipelineConfig(AppConfig):
    name = "apps.pipeline"
    label = "pipeline"

    def ready(self):
        from django.db.models.signals import post_save

        from apps.orgs.models import Organization

        from .services import create_defaults_for_new_org

        post_save.connect(create_defaults_for_new_org, sender=Organization, dispatch_uid="pipeline_org_defaults")
