from django.core import signing
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.activities.models import ActivityKind
from apps.activities.services import record
from apps.automation.models import WorkflowRun
from apps.automation.tests.base import AutomationTestCase
from apps.contacts.models import Contact, CustomFieldDefinition, Entity, FieldType

from ..capture import TIMESTAMP_SALT
from ..models import FormSubmission, LeadSettings, ScoreLog, ScoringRule, WebForm


def old_ts(seconds=30):
    return signing.dumps(timezone.now().timestamp() - seconds, salt=TIMESTAMP_SALT)


class WebFormTests(AutomationTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        CustomFieldDefinition.objects.create(org=self.org, entity=Entity.CONTACT, key="budget", label="Budget", field_type=FieldType.NUMBER)
        self.form = WebForm.objects.create(org=self.org, name="Website enquiry", default_owner=self.agent.user, fields=[
            {"key": "first_name", "label": "Name", "required": True}, {"key": "email", "label": "Email", "required": True},
            {"key": "company", "label": "Company", "required": False}, {"key": "cf:budget", "label": "Budget", "required": False},
            {"key": "message", "label": "Message", "required": False}])
        self.form.add_tags.add(self.tag("Website"))
        self.url = reverse("leads:public_form", args=[self.form.token])

    def submit(self, **data):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self.url, {"first_name": "Nadia", "email": "Nadia@Shop.test", "company": "Shop BD",
                                               "cf__budget": "50000", "message": "Need a CRM", "ts": old_ts(), **data})

    def test_public_form_creates_contact_with_everything(self):
        page = self.client.get(self.url + "?embed=1&utm_source=facebook")
        self.assertContains(page, "Website enquiry".replace("Website enquiry", self.form.title))
        self.assertNotIn("X-Frame-Options", page.headers)  # embeddable
        response = self.submit(utm_source="facebook", utm_campaign="eid")
        self.assertContains(response, "Thank you!")
        contact = Contact.objects.get(email="nadia@shop.test")
        self.assertEqual((contact.first_name, contact.source, contact.owner, contact.company.name), ("Nadia", "web_form", self.agent.user, "Shop BD"))
        self.assertEqual(contact.custom_fields, {"budget": 50000.0})
        self.assertTrue(contact.tags.filter(name="Website").exists())
        self.assertTrue(contact.notes.filter(body__contains="Need a CRM").exists())
        submission = FormSubmission.objects.get()
        self.assertEqual((submission.created_contact, submission.utm), (True, {"utm_source": "facebook", "utm_campaign": "eid"}))
        activity = contact.activities.get(kind=ActivityKind.FORM_SUBMITTED)
        self.assertIn("facebook", activity.summary)
        self.form.refresh_from_db()
        self.assertEqual(self.form.submission_count, 1)

    def test_existing_contact_only_gets_blanks_filled(self):
        existing = self.contact(first_name="Original", email="nadia@shop.test")
        self.submit(first_name="Changed")
        existing.refresh_from_db()
        self.assertEqual(existing.first_name, "Original")
        self.assertEqual(existing.company.name, "Shop BD")
        self.assertFalse(FormSubmission.objects.get().created_contact)

    def test_spam_is_silently_dropped(self):
        for extra in ({"website": "http://spam.test"}, {"ts": signing.dumps(timezone.now().timestamp(), salt=TIMESTAMP_SALT)},
                      {"ts": "forged"}):
            with self.subTest(extra=list(extra)):
                response = self.submit(**extra)
                self.assertContains(response, "Thank you!")
        self.assertFalse(Contact.objects.exists())

    def test_validation_and_consent(self):
        self.form.consent_text = "I agree to be contacted"
        self.form.save()
        response = self.submit(email="not-an-email")
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Enter a valid email", status_code=400)
        self.assertEqual(self.submit().status_code, 400)  # consent not ticked
        self.assertEqual(self.submit(consent="on").status_code, 200)
        self.assertTrue(FormSubmission.objects.get().data["consent"])

    def test_rate_limit_closed_and_unknown(self):
        for _ in range(10):
            self.submit(website="bot")
        self.assertEqual(self.submit().status_code, 429)
        cache.clear()
        self.form.is_active = False
        self.form.save()
        self.assertContains(self.submit(), "closed")
        self.assertEqual(self.client.get(reverse("leads:public_form", args=["nope"])).status_code, 404)

    def test_redirect_after_submit(self):
        self.form.redirect_url = "https://thingbd.com/thanks"
        self.form.save()
        self.assertRedirects(self.submit(), "https://thingbd.com/thanks", fetch_redirect_response=False)

    def test_form_submitted_trigger_filters_by_form(self):
        other = WebForm.objects.create(org=self.org, name="Other", fields=[{"key": "email", "required": True}])
        self.workflow("form_submitted", [("add_note", {"body": "From the website form"})], trigger_config={"form": self.form.pk})
        self.submit()
        self.assertEqual(WorkflowRun.objects.count(), 1)
        cache.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("leads:public_form", args=[other.token]), {"email": "x@y.test", "ts": old_ts()})
        self.assertEqual(WorkflowRun.objects.count(), 1)

    def test_builder_requires_email_or_phone_and_manager(self):
        self.login(self.agent)
        self.assertEqual(self.client.get(reverse("leads:form_create")).status_code, 403)
        self.login(self.manager)
        base = {"name": "Expo", "title": "Visit us", "submit_label": "Send", "success_message": "Thanks", "lifecycle_stage": "lead",
                "is_active": "on"}
        response = self.client.post(reverse("leads:form_create"), {**base, "include__first_name": "on"})
        self.assertContains(response, "Include an Email or Phone field")
        self.client.post(reverse("leads:form_create"), {**base, "include__phone": "on", "required__phone": "on", "label__phone": "Mobile",
                                                        "position__phone": "1", "include__first_name": "on", "position__first_name": "0"})
        created = WebForm.objects.get(name="Expo")
        self.assertEqual(created.fields, [{"key": "first_name", "label": "First name", "required": False},
                                          {"key": "phone", "label": "Mobile", "required": True}])
        detail = self.client.get(reverse("leads:form_detail", args=[created.pk]))
        self.assertContains(detail, f"thingbd-form-{created.token}")
        self.login(self.outsider)
        self.assertEqual(self.client.get(reverse("leads:form_detail", args=[created.pk])).status_code, 404)


class ScoringTests(AutomationTestCase):
    def setUp(self):
        super().setUp()
        ScoringRule.objects.create(org=self.org, name="Decision maker", kind="profile", points=15,
                                   conditions={"match": "all", "rules": [{"field": "contact.job_title", "op": "contains", "value": "manager"}]})
        ScoringRule.objects.create(org=self.org, name="Opens email", kind="event", event="email_opened", points=5, max_times=2)
        ScoringRule.objects.create(org=self.org, name="Unsubscribes", kind="event", event="unsubscribed", points=-20)
        LeadSettings.objects.create(org=self.org, warm_threshold=20, hot_threshold=30)

    def event(self, contact, kind):
        with self.captureOnCommitCallbacks(execute=True):
            record(org=self.org, kind=kind, contact=contact, summary=kind)
        contact.refresh_from_db()

    def test_profile_and_capped_behaviour_points_and_grades(self):
        contact = self.contact(first_name="Rahim", job_title="Sales Manager")
        self.event(contact, ActivityKind.NOTE_ADDED)  # any activity recalculates
        self.assertEqual((contact.score, contact.grade), (15, "cold"))
        self.event(contact, ActivityKind.EMAIL_OPENED)
        self.assertEqual((contact.score, contact.grade), (20, "warm"))
        self.event(contact, ActivityKind.EMAIL_OPENED)
        self.event(contact, ActivityKind.EMAIL_OPENED)  # capped at 2
        self.assertEqual((contact.score, contact.grade), (25, "warm"))
        grade_events = contact.activities.filter(kind=ActivityKind.LEAD_GRADE_CHANGED)
        self.assertEqual(list(grade_events.values_list("summary", flat=True)), ["Lead grade: Cold → Warm (score 20)"])
        self.event(contact, ActivityKind.UNSUBSCRIBED)
        self.assertEqual((contact.score, contact.grade), (5, "cold"))

    def test_profile_points_go_away_when_no_longer_matching(self):
        contact = self.contact(first_name="Rahim", job_title="Manager")
        self.event(contact, ActivityKind.NOTE_ADDED)
        self.assertEqual(contact.score, 15)
        Contact.objects.filter(pk=contact.pk).update(job_title="Intern")
        self.event(contact, ActivityKind.NOTE_ADDED)
        self.assertEqual(contact.score, 0)

    def test_hot_grade_triggers_workflow_and_adjust_score_action(self):
        self.workflow("lead_grade_changed", [("create_task", {"title": "Call hot lead {{ contact.first_name }}", "due_in": 0})],
                      trigger_config={"to_grade": "hot"})
        self.workflow("form_submitted", [("adjust_score", {"points": 30, "reason": "Asked for a demo"})], name="Demo boost")
        contact = self.contact(first_name="Rahim")
        with self.captureOnCommitCallbacks(execute=True):
            record(org=self.org, kind=ActivityKind.FORM_SUBMITTED, contact=contact, summary="form", data={"form": 1})
        contact.refresh_from_db()
        self.assertEqual((contact.score, contact.grade), (30, "hot"))
        self.assertEqual(ScoreLog.objects.get(rule=None).reason, "Asked for a demo")
        self.assertTrue(contact.tasks.filter(title="Call hot lead Rahim").exists())

    def test_rule_views(self):
        self.login(self.agent)
        self.assertEqual(self.client.get(reverse("leads:scoring")).status_code, 403)
        self.login(self.manager)
        response = self.client.post(reverse("leads:rule_create"), {"name": "Empty", "kind": "profile", "points": 5, "conditions_json": "{}"})
        self.assertContains(response, "need at least one condition")
        response = self.client.post(reverse("leads:rule_create"), {"name": "Ev", "kind": "event", "points": 5, "conditions_json": "{}"})
        self.assertContains(response, "Pick the behaviour")
        self.client.post(reverse("leads:rule_create"), {
            "name": "VIP", "kind": "profile", "points": 25, "is_active": "on",
            "conditions_json": '{"match": "all", "rules": [{"field": "contact.lifecycle_stage", "op": "is", "value": "customer"}]}'})
        self.assertTrue(ScoringRule.objects.filter(org=self.org, name="VIP").exists())
        response = self.client.post(reverse("leads:scoring"), {"warm_threshold": 50, "hot_threshold": 40})
        self.assertContains(response, "Hot must need more points")
