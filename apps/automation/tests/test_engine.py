import hashlib
import hmac
import json
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.activities.models import Activity, ActivityKind
from apps.activities.services import record
from apps.contacts.models import Contact, LifecycleStage
from apps.pipeline import services as pipeline_services
from apps.pipeline.models import Deal, Pipeline, Stage
from apps.tasks.models import Task

from .. import engine
from ..models import RunStatus, StepStatus, WorkflowRun
from ..registry import OWNER
from .base import AutomationTestCase

LEAD_ONLY = {"match": "all", "rules": [{"field": "contact.lifecycle_stage", "op": "is", "value": "lead"}]}


class TriggerAndConditionTests(AutomationTestCase):
    def test_new_lead_gets_tag_task_and_stage(self):
        vip = self.tag("Hot")
        workflow = self.workflow("contact_created", [
            ("add_tag", {"tag": vip.pk}),
            ("create_task", {"title": "Call {{ contact.first_name }}", "kind": "call", "due_in": 2, "due_unit": "hours", "assignee": OWNER}),
            ("set_lifecycle", {"stage": "qualified"}),
        ], conditions=LEAD_ONLY)
        contact = self.create_contact_via_ui(first_name="Rahim", email="rahim@x.test")

        run = WorkflowRun.objects.get()
        self.assertEqual((run.workflow, run.contact, run.status), (workflow, contact, RunStatus.COMPLETED))
        self.assertEqual([s.status for s in run.steps.all()], [StepStatus.DONE] * 3)
        contact.refresh_from_db()
        self.assertEqual(contact.lifecycle_stage, LifecycleStage.QUALIFIED)
        self.assertTrue(contact.tags.filter(pk=vip.pk).exists())
        task = Task.objects.get()
        self.assertEqual((task.title, task.assignee, task.contact), ("Call Rahim", self.agent.user, contact))
        self.assertAlmostEqual((task.due_at - timezone.now()).total_seconds(), 7200, delta=60)
        # Timeline shows what the automation did, labelled with the workflow.
        tagged = contact.activities.get(kind=ActivityKind.TAG_ADDED)
        self.assertEqual(tagged.automation_name, workflow.name)
        page = self.client.get(reverse("contacts:contact_detail", args=[contact.pk]))
        self.assertContains(page, f"⚡ {workflow.name}")
        workflow.refresh_from_db()
        self.assertEqual(workflow.run_count, 1)

    def test_conditions_keep_records_out(self):
        self.workflow("contact_created", [("set_lifecycle", {"stage": "qualified"})], conditions=LEAD_ONLY)
        self.create_contact_via_ui(first_name="Big", lifecycle_stage="customer")
        self.assertFalse(WorkflowRun.objects.exists())

    def test_inactive_workflow_does_nothing(self):
        self.workflow("contact_created", [("set_lifecycle", {"stage": "qualified"})], active=False)
        self.create_contact_via_ui(first_name="Rahim")
        self.assertFalse(WorkflowRun.objects.exists())

    def test_trigger_config_filters(self):
        stage = self.stages[2]
        self.workflow("deal_stage_changed", [("add_note", {"body": "Proposal stage reached"})], trigger_config={"stage": stage.pk})
        deal = Deal.objects.create(org=self.org, pipeline=self.pipeline, stage=self.stages[0], title="D")
        with self.captureOnCommitCallbacks(execute=True):
            pipeline_services.move_deal(deal, self.stages[1], self.agent.user)
        self.assertFalse(WorkflowRun.objects.exists())
        with self.captureOnCommitCallbacks(execute=True):
            pipeline_services.move_deal(deal, stage, self.agent.user)
        self.assertEqual(WorkflowRun.objects.get().deal, deal)
        self.assertTrue(deal.notes.filter(body="Proposal stage reached").exists())

    def test_reenroll_rules(self):
        workflow = self.workflow("tag_added", [("add_note", {"body": "tagged"})])
        contact = self.contact(first_name="R")
        for tag in (self.tag("A"), self.tag("B")):
            with self.captureOnCommitCallbacks(execute=True):
                record(org=self.org, kind=ActivityKind.TAG_ADDED, contact=contact, summary="t", data={"tag": tag.pk})
        self.assertEqual(WorkflowRun.objects.count(), 1)
        workflow.allow_reenroll = True
        workflow.save()
        with self.captureOnCommitCallbacks(execute=True):
            record(org=self.org, kind=ActivityKind.TAG_ADDED, contact=contact, summary="t", data={"tag": 1})
        self.assertEqual(WorkflowRun.objects.count(), 2)

    def test_imports_trigger_contact_created_only_for_new_contacts(self):
        self.workflow("contact_created", [("add_note", {"body": "hi"})], trigger_config={"include_imports": True})
        contact = self.contact(first_name="Imported")
        with self.captureOnCommitCallbacks(execute=True):
            record(org=self.org, kind=ActivityKind.IMPORTED, contact=contact, summary="Updated from x.csv", data={"created": False})
        self.assertFalse(WorkflowRun.objects.exists())
        with self.captureOnCommitCallbacks(execute=True):
            record(org=self.org, kind=ActivityKind.IMPORTED, contact=contact, summary="Imported from x.csv", data={"created": True})
        self.assertTrue(WorkflowRun.objects.exists())

    def test_other_orgs_workflows_never_run(self):
        from apps.automation.models import Workflow

        Workflow.objects.create(org=self.other_org, name="theirs", trigger_type="contact_created", is_active=True)
        self.create_contact_via_ui(first_name="Rahim")
        self.assertFalse(WorkflowRun.objects.exists())


class FlowTests(AutomationTestCase):
    def test_wait_then_resume(self):
        workflow = self.workflow("contact_created", [("wait", {"amount": 2, "unit": "days"}), ("set_lifecycle", {"stage": "qualified"})])
        contact = self.create_contact_via_ui(first_name="Rahim")
        run = WorkflowRun.objects.get()
        self.assertEqual((run.status, run.current_step), (RunStatus.WAITING, 1))
        self.assertEqual(engine.resume_due_runs(), 0)  # not yet
        later = timezone.now() + timedelta(days=2, minutes=1)
        workflow.is_active = False
        workflow.save()
        self.assertEqual(engine.resume_due_runs(now=later), 0)  # paused workflows don't resume
        workflow.is_active = True
        workflow.save()
        self.assertEqual(engine.resume_due_runs(now=later), 1)
        run.refresh_from_db()
        contact.refresh_from_db()
        self.assertEqual((run.status, contact.lifecycle_stage), (RunStatus.COMPLETED, "qualified"))

    def test_condition_gate_stops_run(self):
        self.workflow("contact_created", [
            ("condition_gate", {"conditions": {"match": "all", "rules": [{"field": "contact.email", "op": "is_not_empty", "value": ""}]}}),
            ("set_lifecycle", {"stage": "qualified"}),
        ])
        contact = self.create_contact_via_ui(first_name="NoEmail")
        run = WorkflowRun.objects.get()
        self.assertEqual(run.status, RunStatus.STOPPED)
        contact.refresh_from_db()
        self.assertEqual(contact.lifecycle_stage, "lead")

    def test_failed_step_stops_run_with_reason(self):
        other = Pipeline.objects.create(org=self.org, name="Other")
        foreign_stage = Stage.objects.create(org=self.org, pipeline=other, name="Elsewhere")
        self.workflow("deal_created", [("move_deal", {"stage": foreign_stage.pk}), ("add_note", {"body": "never"})])
        deal = Deal.objects.create(org=self.org, pipeline=self.pipeline, stage=self.stages[0], title="D")
        with self.captureOnCommitCallbacks(execute=True):
            pipeline_services.deal_created(deal, self.agent.user)
        run = WorkflowRun.objects.get()
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertIn("different pipeline", run.error)
        self.assertFalse(deal.notes.exists())

    def test_crashing_step_is_contained(self):
        self.workflow("contact_created", [("add_note", {"body": "x"})])
        with mock.patch("apps.automation.actions.add_note", side_effect=RuntimeError("boom")), \
             mock.patch.dict("apps.automation.actions.HANDLERS", {"add_note": mock.Mock(side_effect=RuntimeError("boom"))}):
            self.create_contact_via_ui(first_name="Rahim")
        run = WorkflowRun.objects.get()
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertIn("RuntimeError: boom", run.error)

    def test_loop_protection(self):
        a, b = self.tag("A"), self.tag("B")
        # Two workflows that keep re-tagging each other.
        self.workflow("tag_added", [("remove_tag", {"tag": a.pk}), ("add_tag", {"tag": b.pk})], trigger_config={"tag": a.pk},
                      allow_reenroll=True, name="A to B")
        self.workflow("tag_added", [("remove_tag", {"tag": b.pk}), ("add_tag", {"tag": a.pk})], trigger_config={"tag": b.pk},
                      allow_reenroll=True, name="B to A")
        contact = self.contact(first_name="Loopy")
        contact.tags.add(a)
        with self.captureOnCommitCallbacks(execute=True):
            record(org=self.org, kind=ActivityKind.TAG_ADDED, contact=contact, summary="t", data={"tag": a.pk})
        self.assertEqual(WorkflowRun.objects.count(), engine.MAX_DEPTH + 1)
        self.assertEqual(max(WorkflowRun.objects.values_list("depth", flat=True)), engine.MAX_DEPTH)

    def test_workflow_does_not_retrigger_itself(self):
        tag = self.tag("Any")
        self.workflow("tag_added", [("add_tag", {"tag": tag.pk})], allow_reenroll=True)
        contact = self.contact(first_name="R")
        with self.captureOnCommitCallbacks(execute=True):
            record(org=self.org, kind=ActivityKind.TAG_ADDED, contact=contact, summary="t", data={"tag": 999})
        self.assertEqual(WorkflowRun.objects.count(), 1)

    def test_automation_bookkeeping_does_not_count_as_activity(self):
        self.workflow("contact_created", [("wait", {"amount": 1, "unit": "days"})])
        contact = self.create_contact_via_ui(first_name="R")
        created_activity = contact.activities.get(kind=ActivityKind.CREATED)
        contact.refresh_from_db()
        self.assertEqual(contact.last_activity_at, created_activity.created_at)


class ActionTests(AutomationTestCase):
    def deal_won_flow(self, actions, contact_fields=None):
        fields = {"first_name": "Rahim", "email": "rahim@x.test", "phone": "01711000001", "owner": self.manager.user,
                  **(contact_fields or {})}
        contact = self.contact(**fields)
        deal = Deal.objects.create(org=self.org, pipeline=self.pipeline, stage=self.stages[0], title="CRM rollout", value=250000,
                                   contact=contact, owner=self.manager.user)
        self.workflow("deal_won", actions)
        with self.captureOnCommitCallbacks(execute=True):
            pipeline_services.mark_won(deal, self.agent.user)
        return contact, deal, WorkflowRun.objects.get()

    def test_send_email_as_owner_with_variables(self):
        _, deal, run = self.deal_won_flow([("send_email", {"subject": "Thanks for {{ deal.title }}",
                                                           "body": "Hi {{ contact.first_name }} — {{ user.full_name }}"})])
        self.assertEqual(run.status, RunStatus.COMPLETED)
        sent = mail.outbox[-1]
        self.assertEqual((sent.subject, sent.to), ("Thanks for CRM rollout", ["rahim@x.test"]))
        self.assertIn(f"Hi Rahim — {self.manager.user.get_full_name() or self.manager.user.email}", sent.body)
        message = deal.messages.get()
        self.assertEqual(message.sent_by, self.manager.user)

    def test_email_skipped_without_address(self):
        _, _, run = self.deal_won_flow([("send_email", {"subject": "x", "body": "y"})], {"email": ""})
        self.assertEqual(run.steps.get().status, StepStatus.SKIPPED)
        self.assertEqual(run.status, RunStatus.COMPLETED)

    def test_notify_teammate(self):
        _, _, run = self.deal_won_flow([("notify_user", {"user": OWNER, "message": "{{ deal.title }} was won!"})])
        self.assertEqual(mail.outbox[-1].to, [self.manager.user.email])
        self.assertIn("CRM rollout was won!", mail.outbox[-1].body)

    def test_round_robin_alternates(self):
        self.workflow("contact_created", [("assign_owner", {"mode": "round_robin", "users": [self.agent.user.pk, self.manager.user.pk]})],
                      allow_reenroll=True)
        owners = [self.create_contact_via_ui(first_name=f"C{i}", owner="").owner for i in range(3)]
        self.assertEqual(owners, [self.agent.user, self.manager.user, self.agent.user])

    def test_create_deal_then_move_it(self):
        self.workflow("contact_created", [("create_deal", {"title": "{{ contact.first_name }} deal", "stage": self.stages[0].pk, "value": 5000}),
                                          ("move_deal", {"stage": self.stages[1].pk})])
        contact = self.create_contact_via_ui(first_name="Rahim")
        deal = Deal.objects.get()
        self.assertEqual((deal.title, deal.stage, deal.contact, deal.value), ("Rahim deal", self.stages[1], contact, 5000))
        contact.refresh_from_db()
        self.assertEqual(contact.lifecycle_stage, "opportunity")  # deal creation bumps the lifecycle as usual

    def test_webhook_signed_and_ssrf_blocked(self):
        self.workflow("contact_created", [("webhook", {"url": "https://hooks.example.com/crm"})])
        with mock.patch("apps.automation.actions.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))]), \
             mock.patch("apps.automation.actions.requests.post") as post:
            post.return_value = mock.Mock(status_code=200)
            self.create_contact_via_ui(first_name="Rahim", email="rahim@x.test")
        body = post.call_args.kwargs["data"]
        payload = json.loads(body)
        self.assertEqual(payload["contact"]["email"], "rahim@x.test")
        workflow = WorkflowRun.objects.get().workflow
        expected = hmac.new(workflow.webhook_token.encode(), body.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(post.call_args.kwargs["headers"]["X-ThingBD-Signature"], f"sha256={expected}")

        WorkflowRun.objects.all().delete()
        with mock.patch("apps.automation.actions.socket.getaddrinfo", return_value=[(None, None, None, None, ("10.0.0.5", 0))]), \
             mock.patch("apps.automation.actions.requests.post") as post:
            self.create_contact_via_ui(first_name="Karim")
        post.assert_not_called()
        self.assertIn("private address", WorkflowRun.objects.get().error)


class ScheduledTriggerTests(AutomationTestCase):
    def test_inactive_contacts_enrolled_once_per_quiet_period(self):
        self.workflow("contact_inactive", [("add_note", {"body": "quiet"})], trigger_config={"days": 7}, allow_reenroll=True)
        quiet = self.contact(first_name="Quiet")
        busy = self.contact(first_name="Busy")
        Contact.objects.filter(pk=quiet.pk).update(created_at=timezone.now() - timedelta(days=30), last_activity_at=timezone.now() - timedelta(days=10))
        Contact.objects.filter(pk=busy.pk).update(last_activity_at=timezone.now() - timedelta(days=1))
        self.assertEqual(engine.scan_scheduled(), 1)
        self.assertEqual(engine.scan_scheduled(), 0)  # same quiet period
        self.assertEqual(WorkflowRun.objects.get().contact, quiet)
        # New activity then another long silence -> eligible again.
        Contact.objects.filter(pk=quiet.pk).update(last_activity_at=timezone.now() + timedelta(seconds=1))
        # 8 days on, both are quiet: Quiet for a second period, Busy for its first.
        self.assertEqual(engine.scan_scheduled(now=timezone.now() + timedelta(days=8)), 2)
        self.assertEqual(WorkflowRun.objects.filter(contact=quiet).count(), 2)

    def test_deal_close_soon(self):
        self.workflow("deal_close_soon", [("add_note", {"body": "closing soon"})], trigger_config={"days": 3})
        due = Deal.objects.create(org=self.org, pipeline=self.pipeline, stage=self.stages[0], title="Due",
                                  expected_close_date=timezone.localdate() + timedelta(days=3))
        Deal.objects.create(org=self.org, pipeline=self.pipeline, stage=self.stages[0], title="Later",
                            expected_close_date=timezone.localdate() + timedelta(days=9))
        self.assertEqual(engine.scan_scheduled(), 1)
        self.assertEqual(WorkflowRun.objects.get().deal, due)


class DispatchTests(AutomationTestCase):
    def test_no_task_queued_without_matching_workflow(self):
        contact = self.contact(first_name="R")
        with mock.patch("apps.automation.tasks.process_activity.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                record(org=self.org, kind=ActivityKind.CREATED, contact=contact, summary="x")
        delay.assert_not_called()
        self.assertTrue(Activity.objects.exists())
