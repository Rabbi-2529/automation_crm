import datetime
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.activities.models import ActivityKind
from apps.automation.conditions import matches
from apps.automation.models import RunStatus, WorkflowRun
from apps.automation.tests.base import AutomationTestCase
from apps.contacts.models import Contact, CustomFieldDefinition, Entity, FieldType
from apps.messaging.consent import next_send_time, unsubscribe_url
from apps.messaging.models import ChannelSettings, Message
from apps.messaging.services import ingest_email, ingest_sms
from apps.pipeline.models import Deal

from .. import engine
from ..models import (
    Broadcast,
    BroadcastStatus,
    EnrollmentStatus,
    RecipientStatus,
    Segment,
    Sequence,
    SequenceEnrollment,
    SequenceStep,
)


class SegmentTests(AutomationTestCase):
    def setUp(self):
        super().setUp()
        CustomFieldDefinition.objects.create(org=self.org, entity=Entity.CONTACT, key="budget", label="Budget", field_type=FieldType.NUMBER)
        vip = self.vip = self.tag("VIP")
        self.people = [
            self.contact(first_name="Rahim", email="rahim@x.test", job_title="Sales Manager", lifecycle_stage="lead", score=40,
                         grade="hot", owner=self.agent.user, custom_fields={"budget": 90000}),
            self.contact(first_name="Karim", email="", phone="0171", lifecycle_stage="customer", score=5, email_opt_out=True),
            self.contact(first_name="Nadia", email="nadia@y.test", job_title="Intern", lifecycle_stage="lead", custom_fields={"budget": 1000}),
        ]
        self.people[0].tags.add(vip)

    def test_database_query_matches_python_conditions(self):
        """Segments are evaluated in SQL; they must agree with the engine's Python evaluator."""
        rules = [
            {"field": "contact.first_name", "op": "starts_with", "value": "ra"},
            {"field": "contact.email", "op": "is_empty", "value": ""},
            {"field": "contact.email", "op": "is_not_empty", "value": ""},
            {"field": "contact.job_title", "op": "contains", "value": "manager"},
            {"field": "contact.job_title", "op": "not_contains", "value": "manager"},
            {"field": "contact.lifecycle_stage", "op": "is", "value": "lead"},
            {"field": "contact.lifecycle_stage", "op": "is_not", "value": "lead"},
            {"field": "contact.owner", "op": "is", "value": str(self.agent.user.pk)},
            {"field": "contact.owner", "op": "is_empty", "value": ""},
            {"field": "contact.tags", "op": "has", "value": str(self.vip.pk)},
            {"field": "contact.tags", "op": "not_has", "value": str(self.vip.pk)},
            {"field": "contact.score", "op": "gte", "value": "10"},
            {"field": "contact.score", "op": "lt", "value": "10"},
            {"field": "contact.grade", "op": "is", "value": "hot"},
            {"field": "contact.email_opt_out", "op": "is", "value": "yes"},
            {"field": "contact.cf.budget", "op": "gt", "value": "5000"},
            {"field": "contact.days_since_created", "op": "lt", "value": "1"},
        ]
        for rule in rules:
            with self.subTest(rule=rule):
                segment = Segment(org=self.org, conditions={"match": "all", "rules": [rule]})
                in_sql = set(segment.contacts().values_list("first_name", flat=True))
                in_python = {c.first_name for c in Contact.objects.filter(org=self.org) if matches({"rules": [rule]}, contact=c)}
                self.assertEqual(in_sql, in_python)

    def test_any_vs_all_and_contacts_table_filter(self):
        segment = Segment.objects.create(org=self.org, name="Leads or VIP", conditions={"match": "any", "rules": [
            {"field": "contact.lifecycle_stage", "op": "is", "value": "lead"}, {"field": "contact.tags", "op": "has", "value": str(self.vip.pk)}]})
        self.assertEqual(segment.contacts().count(), 2)
        self.login(self.agent)
        payload = self.client.get(reverse("contacts:contact_table"), {"segment": segment.pk}).json()
        self.assertEqual(payload["recordsFiltered"], 2)
        foreign = Segment.objects.create(org=self.other_org, name="Theirs")
        self.assertEqual(self.client.get(reverse("contacts:contact_table"), {"segment": foreign.pk}).json()["recordsFiltered"], 0)

    def test_segment_condition_in_workflows_and_views(self):
        segment = Segment.objects.create(org=self.org, name="Hot", conditions={"rules": [{"field": "contact.grade", "op": "is", "value": "hot"}]})
        self.assertTrue(matches({"rules": [{"field": "contact.segment", "op": "is", "value": str(segment.pk)}]}, contact=self.people[0]))
        self.assertFalse(matches({"rules": [{"field": "contact.segment", "op": "is", "value": str(segment.pk)}]}, contact=self.people[1]))
        self.login(self.manager)
        response = self.client.post(reverse("campaigns:segment_create"), {"name": "Dhaka leads", "conditions_json":
                                    '{"match": "all", "rules": [{"field": "contact.lifecycle_stage", "op": "is", "value": "lead"}]}'})
        created = Segment.objects.get(name="Dhaka leads")
        self.assertRedirects(response, reverse("contacts:contact_list") + f"?segment={created.pk}")
        response = self.client.post(reverse("campaigns:segment_create"), {"name": "Bad", "conditions_json":
                                    '{"rules": [{"field": "contact.tags", "op": "has", "value": "999999"}]}'})
        self.assertContains(response, "no longer exists")


class SequenceTests(AutomationTestCase):
    def setUp(self):
        super().setUp()
        self.sequence = Sequence.objects.create(org=self.org, name="Onboarding", created_by=self.manager.user)
        SequenceStep.objects.create(sequence=self.sequence, position=0, channel="email", subject="Welcome {{ contact.first_name }}",
                                    body="Hi {{ contact.first_name }}!")
        SequenceStep.objects.create(sequence=self.sequence, position=1, delay_days=2, channel="sms", body="Any questions?")
        self.contact_ = self.contact(first_name="Rahim", email="rahim@x.test", phone="01711000001", owner=self.agent.user)

    def run_due(self, at=None):
        with self.captureOnCommitCallbacks(execute=True):
            return engine.process_sequences(now=at)

    def test_steps_send_in_order_with_marketing_extras(self):
        ChannelSettings.objects.filter(org=self.org).update(sms_provider="console")
        enrollment = engine.enroll(self.sequence, self.contact_, by=self.manager.user)
        self.assertIsNone(engine.enroll(self.sequence, self.contact_))  # one active enrollment only
        self.assertEqual(self.run_due(), 1)
        sent = mail.outbox[-1]
        self.assertEqual((sent.subject, sent.to), ("Welcome Rahim", ["rahim@x.test"]))
        self.assertIn("Unsubscribe:", sent.body)
        self.assertIn("List-Unsubscribe", sent.extra_headers)
        self.assertEqual(Message.objects.get().sent_by, self.agent.user)  # sends as the contact's owner
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.current_step, 1)
        self.assertEqual(self.run_due(), 0)  # SMS waits 2 days
        with mock.patch("sys.stdout"):
            self.run_due(timezone.now() + timedelta(days=2, minutes=1))
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.status, EnrollmentStatus.COMPLETED)
        self.assertTrue(Message.objects.get(channel="sms").body_text.endswith("Reply STOP to opt out"))
        steps, counts = engine.sequence_stats(self.sequence)
        self.assertEqual([s["sent"] for s in steps], [1, 1])

    def test_reply_stops_sequence(self):
        engine.enroll(self.sequence, self.contact_)
        self.run_due()
        sent = Message.objects.get()
        raw = (f"From: rahim@x.test\r\nSubject: Re: Welcome\r\nMessage-ID: <r1@x>\r\nIn-Reply-To: {sent.message_id}\r\n"
               "Content-Type: text/plain\r\n\r\nThanks!").encode()
        with self.captureOnCommitCallbacks(execute=True):
            ingest_email(self.org, raw)
        self.assertEqual(SequenceEnrollment.objects.get().status, EnrollmentStatus.REPLIED)

    def test_exit_condition_opt_out_and_quiet_hours(self):
        self.sequence.exit_conditions = {"match": "all", "rules": [{"field": "contact.lifecycle_stage", "op": "is", "value": "customer"}]}
        self.sequence.save()
        enrollment = engine.enroll(self.sequence, self.contact_)
        ChannelSettings.objects.filter(org=self.org).update(quiet_hours_enabled=True, send_window_start=datetime.time(9),
                                                            send_window_end=datetime.time(17))
        night = timezone.make_aware(datetime.datetime.combine(timezone.localdate(), datetime.time(22, 0)))
        self.run_due(night)
        enrollment.refresh_from_db()
        self.assertEqual(timezone.localtime(enrollment.next_run_at).time(), datetime.time(9, 0))
        self.assertFalse(Message.objects.exists())
        ChannelSettings.objects.filter(org=self.org).update(quiet_hours_enabled=False)
        Contact.objects.filter(pk=self.contact_.pk).update(email_opt_out=True)
        self.run_due(enrollment.next_run_at + timedelta(minutes=1))
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.deliveries.get().skipped_reason, "opted out")
        Contact.objects.filter(pk=self.contact_.pk).update(lifecycle_stage="customer")
        self.run_due(timezone.now() + timedelta(days=3))
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.status, EnrollmentStatus.EXITED)

    def test_workflow_enrolls_and_segment_bulk_enroll(self):
        self.workflow("contact_created", [("enroll_sequence", {"sequence": self.sequence.pk})])
        created = self.create_contact_via_ui(first_name="Nadia", email="n@x.test")
        self.assertTrue(SequenceEnrollment.objects.filter(contact=created, status="active").exists())
        segment = Segment.objects.create(org=self.org, name="All")
        self.login(self.manager)
        self.client.post(reverse("campaigns:sequence_enroll_segment", args=[self.sequence.pk]), {"segment": segment.pk})
        self.assertEqual(SequenceEnrollment.objects.filter(status="active").count(), Contact.objects.filter(org=self.org).count())

    def test_sequence_builder_and_contact_enroll_views(self):
        self.login(self.manager)
        data = {"name": "Follow-up", "is_active": "on", "exit_on_reply": "on", "send_as": "owner", "conditions_json": "{}",
                "steps-TOTAL_FORMS": 1, "steps-INITIAL_FORMS": 0, "steps-MIN_NUM_FORMS": 0, "steps-MAX_NUM_FORMS": 1000,
                "steps-0-position": 0, "steps-0-delay_days": 0, "steps-0-delay_hours": 0, "steps-0-channel": "email",
                "steps-0-subject": "", "steps-0-body": "Hello"}
        response = self.client.post(reverse("campaigns:sequence_create"), data)
        self.assertContains(response, "Emails need a subject")
        self.client.post(reverse("campaigns:sequence_create"), {**data, "steps-0-subject": "Hi"})
        follow_up = Sequence.objects.get(name="Follow-up")
        self.assertEqual(follow_up.steps.count(), 1)
        self.login(self.agent)
        self.client.post(reverse("campaigns:contact_enroll", args=[self.contact_.pk]), {"sequence": follow_up.pk})
        self.assertTrue(SequenceEnrollment.objects.filter(sequence=follow_up, contact=self.contact_).exists())
        self.client.post(reverse("campaigns:contact_enroll", args=[self.contact_.pk]), {"remove": follow_up.pk})
        self.assertEqual(SequenceEnrollment.objects.get(sequence=follow_up).status, EnrollmentStatus.REMOVED)


class BroadcastTests(AutomationTestCase):
    def setUp(self):
        super().setUp()
        self.segment = Segment.objects.create(org=self.org, name="Everyone")
        self.contact(first_name="A", email="a@x.test")
        self.contact(first_name="B", email="b@x.test", email_opt_out=True)
        self.contact(first_name="C", phone="01711")

    def test_send_now_skips_unreachable_and_reports(self):
        self.login(self.manager)
        self.client.post(reverse("campaigns:broadcast_create"), {"name": "Eid offer", "channel": "email", "segment": self.segment.pk,
                                                                 "subject": "Eid offer for {{ contact.first_name }}", "body": "20% off", "send_as": "creator"})
        broadcast = Broadcast.objects.get()
        page = self.client.get(reverse("campaigns:broadcast_detail", args=[broadcast.pk]))
        self.assertEqual((page.context["audience"], page.context["reachable"]), (3, 1))
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("campaigns:broadcast_detail", args=[broadcast.pk]), {"when": ""})
        broadcast.refresh_from_db()
        self.assertEqual(broadcast.status, BroadcastStatus.SENT)
        self.assertEqual([m.subject for m in mail.outbox], ["Eid offer for A"])
        stats = engine.broadcast_stats(broadcast)
        self.assertEqual((stats["total"], stats["sent"], stats["skipped"]), (3, 1, 2))
        reasons = set(broadcast.recipients.filter(status=RecipientStatus.SKIPPED).values_list("skipped_reason", flat=True))
        self.assertEqual(reasons, {"opted out", "no address"})

    def test_scheduled_quiet_hours_and_cancel(self):
        broadcast = Broadcast.objects.create(org=self.org, name="Later", channel="email", segment=self.segment, subject="Hi", body="x",
                                             status=BroadcastStatus.SCHEDULED, scheduled_at=timezone.now() + timedelta(hours=1),
                                             created_by=self.manager.user)
        self.assertEqual(engine.process_broadcasts(), 0)
        ChannelSettings.objects.filter(org=self.org).update(quiet_hours_enabled=True, send_window_start=datetime.time(9),
                                                            send_window_end=datetime.time(17))
        night = timezone.make_aware(datetime.datetime.combine(timezone.localdate() + timedelta(days=1), datetime.time(23)))
        self.assertEqual(engine.process_broadcasts(now=night), 0)
        broadcast.refresh_from_db()
        self.assertEqual((broadcast.status, broadcast.recipients.count()), (BroadcastStatus.SENDING, 3))  # snapshotted, waiting
        self.login(self.manager)
        self.client.post(reverse("campaigns:broadcast_detail", args=[broadcast.pk]), {"action": "cancel"})
        broadcast.refresh_from_db()
        self.assertEqual(broadcast.status, BroadcastStatus.CANCELLED)

    def test_agents_cannot_send(self):
        broadcast = Broadcast.objects.create(org=self.org, name="X", channel="email", segment=self.segment, subject="Hi", body="x")
        self.login(self.agent)
        self.client.post(reverse("campaigns:broadcast_detail", args=[broadcast.pk]), {"when": ""})
        broadcast.refresh_from_db()
        self.assertEqual(broadcast.status, BroadcastStatus.DRAFT)
        self.assertEqual(self.client.get(reverse("campaigns:broadcast_create")).status_code, 403)


class ConsentTests(AutomationTestCase):
    def test_unsubscribe_page_one_click_and_resubscribe(self):
        contact = self.contact(first_name="R", email="r@x.test")
        url = unsubscribe_url(contact)
        path = url.split("testserver")[-1].replace("http://localhost:8000", "")
        self.assertContains(self.client.get(path), "Unsubscribe?")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(path, {"List-Unsubscribe": "One-Click"})
        contact.refresh_from_db()
        self.assertTrue(contact.email_opt_out)
        self.assertIn("one-click", contact.activities.get(kind=ActivityKind.UNSUBSCRIBED).summary)
        self.client.post(path, {"action": "resubscribe"})
        contact.refresh_from_db()
        self.assertFalse(contact.email_opt_out)
        self.assertEqual(self.client.get(path[:-5] + "XXXX/").status_code, 404)

    def test_sms_stop_and_start(self):
        contact = self.contact(first_name="R", phone="01711000001")
        ingest_sms(self.org, "+8801711000001", "STOP", "SM1")
        contact.refresh_from_db()
        self.assertTrue(contact.sms_opt_out)
        ingest_sms(self.org, "+8801711000001", "start", "SM2")
        contact.refresh_from_db()
        self.assertFalse(contact.sms_opt_out)

    def test_workflow_sends_skip_unsubscribed_and_respect_quiet_hours(self):
        from apps.automation import engine as automation

        workflow = self.workflow("contact_created", [("send_email", {"subject": "Hi", "body": "x"}), ("add_note", {"body": "after"})])
        opted_out = self.contact(first_name="Opted", email="o@x.test", email_opt_out=True)
        run = automation.enroll(workflow, contact=opted_out)
        self.assertEqual(run.steps.first().status, "skipped")
        self.assertIn("unsubscribed", run.steps.first().message)
        self.assertEqual(run.status, RunStatus.COMPLETED)  # the note step still ran

        fresh = self.contact(first_name="Fresh", email="f@x.test")
        with mock.patch("apps.automation.actions.next_send_time", return_value=timezone.now() + timedelta(hours=3)):
            run = automation.enroll(workflow, contact=fresh)
        self.assertEqual((run.status, run.current_step), (RunStatus.WAITING, 0))  # same step retries later
        self.assertIn("Outside sending hours", run.steps.get().message)

    def test_manual_consent_toggle(self):
        contact = self.contact(first_name="R", email="r@x.test")
        self.login(self.agent)
        self.client.post(reverse("contacts:contact_consent", args=[contact.pk]), {"channel": "email", "opt": "out"})
        contact.refresh_from_db()
        self.assertTrue(contact.email_opt_out)

    def test_quiet_hours_window_math(self):
        cs = ChannelSettings(quiet_hours_enabled=True, send_window_start=datetime.time(9), send_window_end=datetime.time(21))
        at = lambda h: timezone.make_aware(datetime.datetime.combine(datetime.date(2026, 11, 2), datetime.time(h)))  # noqa: E731
        self.assertIsNone(next_send_time(cs, at(10)))
        self.assertEqual(next_send_time(cs, at(7)), at(9))
        self.assertEqual(next_send_time(cs, at(22)), at(9) + timedelta(days=1))
        overnight = ChannelSettings(quiet_hours_enabled=True, send_window_start=datetime.time(20), send_window_end=datetime.time(2))
        self.assertIsNone(next_send_time(overnight, at(23)))
        self.assertIsNone(next_send_time(overnight, at(1)))
        self.assertEqual(next_send_time(overnight, at(10)), at(20))
        self.assertIsNone(next_send_time(ChannelSettings(quiet_hours_enabled=False), at(3)))


class NewTriggerTests(AutomationTestCase):
    def test_lead_not_contacted(self):
        from apps.automation import engine as automation

        self.workflow("lead_not_contacted", [("add_note", {"body": "Nobody called!"})], trigger_config={"hours": 2})
        slow = self.contact(first_name="Slow", email="s@x.test")
        called = self.contact(first_name="Called")
        Contact.objects.filter(pk__in=[slow.pk, called.pk]).update(created_at=timezone.now() - timedelta(hours=3))
        from apps.messaging.models import CallLog

        CallLog.objects.create(org=self.org, contact=called, called_at=timezone.now())
        self.assertEqual(automation.scan_scheduled(), 1)
        self.assertEqual(WorkflowRun.objects.get().contact, slow)
        self.assertEqual(automation.scan_scheduled(), 0)

    def test_birthday_every_year(self):
        from apps.automation import engine as automation

        CustomFieldDefinition.objects.create(org=self.org, entity=Entity.CONTACT, key="birthday", label="Birthday", field_type=FieldType.DATE)
        self.workflow("date_field", [("add_note", {"body": "Happy birthday!"})], trigger_config={"field": "birthday", "days_before": 0, "yearly": True})
        today = timezone.localdate()
        self.contact(first_name="Today", custom_fields={"birthday": today.replace(year=1990).isoformat()})
        self.contact(first_name="Tomorrow", custom_fields={"birthday": (today + timedelta(days=1)).replace(year=1990).isoformat()})
        self.assertEqual(automation.scan_scheduled(), 1)
        self.assertEqual(automation.scan_scheduled(), 0)  # once a day

    def test_deal_idle(self):
        from apps.automation import engine as automation

        self.workflow("deal_idle", [("add_note", {"body": "Stale"})], trigger_config={"days": 7})
        stale = Deal.objects.create(org=self.org, pipeline=self.pipeline, stage=self.stages[0], title="Stale")
        Deal.objects.filter(pk=stale.pk).update(stage_changed_at=timezone.now() - timedelta(days=8))
        Deal.objects.create(org=self.org, pipeline=self.pipeline, stage=self.stages[0], title="Fresh")
        self.assertEqual(automation.scan_scheduled(), 1)
        self.assertEqual(WorkflowRun.objects.get().deal, stale)
