from django.urls import reverse

from apps.contacts.tests.base import CrmTestCase
from apps.pipeline.services import default_pipeline

from ..models import Workflow
from ..views import clean_definition, save_definition


class AutomationTestCase(CrmTestCase):
    def setUp(self):
        super().setUp()
        self.pipeline = default_pipeline(self.org)
        self.stages = list(self.pipeline.stages.all())

    def workflow(self, trigger_type, actions, trigger_config=None, conditions=None, active=True, **fields):
        """Builds a workflow through the same validation the builder uses."""
        import json

        definition = {"name": fields.pop("name", f"WF {trigger_type}"), "trigger_type": trigger_type,
                      "trigger_config": trigger_config or {}, "conditions": conditions or {"match": "all", "rules": []},
                      "actions": [{"type": t, "config": c} for t, c in actions], "is_active": active, **fields}
        clean, errors = clean_definition(json.dumps(definition), self.org)
        assert not errors, errors
        workflow = Workflow(org=self.org, created_by=self.admin.user)
        save_definition(workflow, clean)
        return workflow

    def create_contact_via_ui(self, **data):
        """Creates a contact through the real view so the activity -> dispatch -> engine chain runs."""
        self.login(self.agent)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("contacts:contact_create"), {"lifecycle_stage": "lead", "owner": self.agent.user.pk, **data})
        from apps.contacts.models import Contact

        return Contact.objects.latest("pk")
