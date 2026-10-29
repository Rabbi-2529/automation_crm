import json

from django.urls import reverse

from apps.contacts.models import Contact

from ..models import RunStatus, Workflow, WorkflowAction, WorkflowRun
from .base import AutomationTestCase


def definition(**overrides):
    base = {"name": "Welcome", "description": "", "is_active": True, "allow_reenroll": False, "trigger_type": "contact_created",
            "trigger_config": {"include_imports": True}, "conditions": {"match": "all", "rules": []},
            "actions": [{"type": "add_note", "config": {"body": "Hello"}}]}
    return json.dumps({**base, **overrides})


class BuilderTests(AutomationTestCase):
    def test_agents_can_view_but_not_build(self):
        self.login(self.agent)
        self.assertEqual(self.client.get(reverse("automation:workflow_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("automation:workflow_create")).status_code, 403)

    def test_create_and_edit_workflow(self):
        self.login(self.manager)
        page = self.client.get(reverse("automation:workflow_create"))
        self.assertContains(page, 'id="builder-schema"')
        response = self.client.post(reverse("automation:workflow_create"), {"definition": definition()})
        workflow = Workflow.objects.get()
        self.assertRedirects(response, reverse("automation:workflow_detail", args=[workflow.pk]))
        self.assertEqual((workflow.org, workflow.created_by, workflow.is_active), (self.org, self.manager.user, True))
        self.assertEqual(list(workflow.actions.values_list("action_type", flat=True)), ["add_note"])
        self.client.post(reverse("automation:workflow_edit", args=[workflow.pk]), {"definition": definition(
            name="Renamed", actions=[{"type": "wait", "config": {"amount": 1, "unit": "hours"}},
                                     {"type": "add_note", "config": {"body": "Later"}}])})
        workflow.refresh_from_db()
        self.assertEqual(workflow.name, "Renamed")
        self.assertEqual(list(workflow.actions.values_list("action_type", flat=True)), ["wait", "add_note"])

    def test_validation_errors(self):
        foreign_tag = self.tag("Theirs", org=self.other_org)
        self.login(self.manager)
        cases = [
            (definition(name=""), "Give the workflow a name"),
            (definition(trigger_type="nope"), "Pick a trigger"),
            (definition(actions=[]), "Add at least one action"),
            (definition(actions=[{"type": "add_tag", "config": {"tag": foreign_tag.pk}}]), "no longer exists"),
            (definition(actions=[{"type": "move_deal", "config": {"stage": self.stages[1].pk}}]), "needs a deal trigger"),
            (definition(conditions={"match": "all", "rules": [{"field": "contact.owner", "op": "is", "value": self.outsider.user.pk}]}),
             "isn&#x27;t an active member"),
            (definition(actions=[{"type": "webhook", "config": {"url": "ftp://x"}}]), "must start with https://"),
            ("not json", "couldn&#x27;t be read"),
        ]
        for payload, message in cases:
            with self.subTest(message=message):
                response = self.client.post(reverse("automation:workflow_create"), {"definition": payload})
                self.assertContains(response, message)
        self.assertFalse(Workflow.objects.exists())

    def test_recipe_prefill(self):
        self.login(self.manager)
        response = self.client.get(reverse("automation:workflow_create"), {"recipe": "welcome_lead"})
        self.assertContains(response, "Welcome new leads")

    def test_edit_keeps_round_robin_pointer(self):
        self.login(self.manager)
        rr = {"type": "assign_owner", "config": {"mode": "round_robin", "users": [self.agent.user.pk, self.manager.user.pk]}}
        self.client.post(reverse("automation:workflow_create"), {"definition": definition(actions=[rr])})
        action = WorkflowAction.objects.get()
        action.state = {"next": 5}
        action.save()
        workflow = Workflow.objects.get()
        self.client.post(reverse("automation:workflow_edit", args=[workflow.pk]), {"definition": definition(name="X", actions=[rr])})
        self.assertEqual(WorkflowAction.objects.get().state, {"next": 5})


class DetailAndRunTests(AutomationTestCase):
    def test_detail_toggle_test_run_and_isolation(self):
        workflow = self.workflow("contact_created", [("set_lifecycle", {"stage": "qualified"})], active=False,
                                 conditions={"match": "all", "rules": [{"field": "contact.email", "op": "is_not_empty", "value": ""}]})
        contact = self.contact(first_name="Rahim", email="r@x.test")
        no_email = self.contact(first_name="Nobody")
        self.login(self.manager)
        page = self.client.get(reverse("automation:workflow_detail", args=[workflow.pk]))
        self.assertContains(page, "Contact is created")
        self.assertContains(page, "Contact · Email is not empty")
        self.assertContains(page, "Set lifecycle stage — stage: Qualified")

        self.client.post(reverse("automation:workflow_toggle", args=[workflow.pk]))
        workflow.refresh_from_db()
        self.assertTrue(workflow.is_active)

        response = self.client.post(reverse("automation:workflow_test", args=[workflow.pk]), {"contact": no_email.pk})
        self.assertFalse(WorkflowRun.objects.exists())
        response = self.client.post(reverse("automation:workflow_test", args=[workflow.pk]), {"contact": contact.pk})
        run = WorkflowRun.objects.get()
        self.assertRedirects(response, reverse("automation:run_detail", args=[run.pk]))
        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertContains(self.client.get(reverse("automation:run_detail", args=[run.pk])), "Lifecycle stage → Qualified")

        self.login(self.outsider)
        self.assertEqual(self.client.get(reverse("automation:workflow_detail", args=[workflow.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("automation:run_detail", args=[run.pk])).status_code, 404)

    def test_cancel_waiting_run(self):
        workflow = self.workflow("contact_created", [("wait", {"amount": 1, "unit": "days"})])
        from .. import engine

        run = engine.enroll(workflow, contact=self.contact(first_name="R"))
        self.login(self.manager)
        self.client.post(reverse("automation:run_cancel", args=[run.pk]))
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.CANCELLED)


class WebhookTriggerTests(AutomationTestCase):
    def test_incoming_webhook_creates_contact_and_runs(self):
        workflow = self.workflow("webhook", [("set_lifecycle", {"stage": "qualified"})])
        url = reverse("automation:webhook", args=[workflow.webhook_token])
        response = self.client.post(url, json.dumps({"email": "Lead@Shop.test", "first_name": "Nadia"}), content_type="application/json")
        self.assertEqual(response.status_code, 201)
        contact = Contact.objects.get(email="lead@shop.test")
        self.assertEqual((contact.first_name, contact.source, contact.lifecycle_stage, contact.org), ("Nadia", "api", "qualified", self.org))
        again = self.client.post(url, json.dumps({"email": "lead@shop.test"}), content_type="application/json")
        self.assertEqual((again.status_code, again.json()["created"]), (200, False))

    def test_webhook_rejections(self):
        workflow = self.workflow("webhook", [("add_note", {"body": "x"})], active=False)
        url = reverse("automation:webhook", args=[workflow.webhook_token])
        self.assertEqual(self.client.post(url, "{}", content_type="application/json").status_code, 409)
        workflow.is_active = True
        workflow.save()
        self.assertEqual(self.client.post(url, "[1]", content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post(url, "{}", content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post(reverse("automation:webhook", args=["nope"]), "{}", content_type="application/json").status_code, 404)
