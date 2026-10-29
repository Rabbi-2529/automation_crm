import hashlib
import hmac
import json
from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.activities.models import Activity, ActivityKind
from apps.activities.services import record
from apps.automation.models import Workflow
from apps.contacts.models import Source
from apps.contacts.tests.base import CrmTestCase
from apps.integrations import facebook, webhooks
from apps.integrations.calendar import build_feed
from apps.integrations.models import (
    CalendarFeed,
    DeliveryStatus,
    FacebookLead,
    FacebookLeadSettings,
    FacebookPage,
    LeadStatus,
    WebhookDelivery,
    WebhookEndpoint,
)
from apps.messaging.models import Channel, ChannelSettings, Message, MessageStatus, MessageTemplate, WhatsAppProvider
from apps.messaging.services import compose
from apps.pipeline.models import Deal, Pipeline
from apps.tasks.models import Task


def response(status=200, text="ok", payload=None):
    r = mock.Mock(status_code=status, text=text)
    r.json.return_value = payload or {}
    return r


def meta_signature(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@override_settings(ALLOW_PRIVATE_WEBHOOKS=True)
class WebhookTests(CrmTestCase):
    def setUp(self):
        super().setUp()
        self.endpoint = WebhookEndpoint.objects.create(org=self.org, url="https://hooks.example.com/crm", events=[ActivityKind.DEAL_WON])

    def fire(self, kind=ActivityKind.DEAL_WON, **kw):
        contact = self.contact(email=f"a{Activity.objects.count()}@example.com", first_name="Rahim")
        with self.captureOnCommitCallbacks(execute=True):
            record(org=self.org, kind=kind, contact=contact, summary="Something", **kw)
        return contact

    def test_matching_event_is_signed_and_delivered(self):
        with mock.patch("apps.integrations.webhooks.requests.post", return_value=response(204)) as post:
            contact = self.fire()
        delivery = WebhookDelivery.objects.get()
        self.assertEqual((delivery.status, delivery.attempts, delivery.response_status), (DeliveryStatus.SUCCESS, 1, 204))
        args, kwargs = post.call_args
        body, headers = kwargs["data"], kwargs["headers"]
        payload = json.loads(body)
        self.assertEqual(payload["event"], "deal_won")
        self.assertEqual(payload["data"]["contact"]["email"], contact.email)
        ts, v1 = (part.split("=", 1)[1] for part in headers["X-ThingBD-Signature"].split(","))
        expected = hmac.new(self.endpoint.secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        self.assertEqual(v1, expected)
        self.assertFalse(kwargs["allow_redirects"])

    def test_other_events_and_other_orgs_are_not_sent(self):
        WebhookEndpoint.objects.create(org=self.other_org, url="https://rival.example.com/")
        with mock.patch("apps.integrations.webhooks.requests.post", return_value=response()) as post:
            self.fire(kind=ActivityKind.NOTE_ADDED)
            self.fire()
        self.assertEqual(post.call_count, 1)
        self.assertEqual(WebhookDelivery.objects.get().endpoint, self.endpoint)

    def test_failures_back_off_then_give_up(self):
        with mock.patch("apps.integrations.webhooks.requests.post", return_value=response(500, "boom")):
            self.fire()
            delivery = WebhookDelivery.objects.get()
            self.assertEqual((delivery.status, delivery.error), (DeliveryStatus.PENDING, "HTTP 500"))
            self.assertAlmostEqual((delivery.next_attempt_at - timezone.now()).total_seconds(), 60, delta=5)
            for _ in range(len(webhooks.RETRY_DELAYS)):
                webhooks.retry_due(now=timezone.now() + timedelta(days=1))
        delivery.refresh_from_db()
        self.assertEqual((delivery.status, delivery.attempts), (DeliveryStatus.FAILED, len(webhooks.RETRY_DELAYS) + 1))
        self.endpoint.refresh_from_db()
        self.assertEqual(self.endpoint.consecutive_failures, len(webhooks.RETRY_DELAYS) + 1)

    def test_claim_prevents_double_send(self):
        with mock.patch("apps.integrations.webhooks.requests.post", return_value=response(500)):
            self.fire()
        delivery = WebhookDelivery.objects.get()
        with mock.patch("apps.integrations.webhooks.requests.post", return_value=response()) as post:
            webhooks.retry_due(now=timezone.now())  # not due yet
            self.assertEqual(post.call_count, 0)

    def test_endpoint_switches_off_after_repeated_failures(self):
        WebhookEndpoint.objects.filter(pk=self.endpoint.pk).update(consecutive_failures=webhooks.DISABLE_AFTER - 1)
        with mock.patch("apps.integrations.webhooks.requests.post", return_value=response(503)):
            self.fire()
        self.endpoint.refresh_from_db()
        self.assertFalse(self.endpoint.is_active)
        self.assertIn("Switched off", self.endpoint.disabled_reason)
        webhooks.retry_due(now=timezone.now() + timedelta(days=1))
        self.assertEqual(WebhookDelivery.objects.get().status, DeliveryStatus.FAILED)

    @override_settings(ALLOW_PRIVATE_WEBHOOKS=False)
    def test_private_addresses_are_blocked(self):
        self.endpoint.url = "https://internal.example.com/"
        self.endpoint.save()
        with mock.patch("apps.core.net.socket.getaddrinfo", return_value=[(None, None, None, None, ("10.0.0.5", 0))]), \
             mock.patch("apps.integrations.webhooks.requests.post") as post:
            delivery = webhooks.send_ping(self.endpoint)
        post.assert_not_called()
        self.assertIn("private address", delivery.error)

    def test_settings_pages_are_admin_only(self):
        self.login(self.manager)
        self.assertEqual(self.client.get(reverse("integrations:overview")).status_code, 403)
        self.login(self.admin)
        for tab in ("api", "webhooks", "facebook"):
            self.assertEqual(self.client.get(reverse("integrations:overview") + f"?tab={tab}").status_code, 200)
        self.login(self.outsider)
        self.assertEqual(self.client.get(reverse("integrations:webhook_detail", args=[self.endpoint.pk])).status_code, 404)

    def test_create_api_key_shows_it_once(self):
        self.login(self.admin)
        page = self.client.post(reverse("integrations:api_key_create"), {"name": "Zapier", "scope": "read"})
        raw = page.context["new_key"]
        self.assertContains(page, raw)
        self.assertNotContains(self.client.get(reverse("integrations:overview")), raw)


@override_settings(ALLOW_PRIVATE_WEBHOOKS=False)
class WebhookFormTests(CrmTestCase):
    def test_https_required(self):
        from apps.integrations.forms import WebhookEndpointForm

        form = WebhookEndpointForm({"url": "http://example.com/hook", "is_active": True}, org=self.org)
        self.assertFalse(form.is_valid())
        self.assertIn("https", form.errors["url"][0])


class WhatsAppTests(CrmTestCase):
    def setUp(self):
        super().setUp()
        self.cs = ChannelSettings.for_org(self.org)
        self.cs.whatsapp_provider = WhatsAppProvider.CLOUD
        self.cs.whatsapp_phone_number_id = "1234567890"
        self.cs.whatsapp_access_token = "EAAtoken"
        self.cs.whatsapp_app_secret = "app-secret"
        self.cs.save()
        self.person = self.contact(first_name="Karim", phone="01711-000000")

    def send(self, **kw):
        with self.captureOnCommitCallbacks(execute=True):
            return compose(org=self.org, user=self.admin.user, channel=Channel.WHATSAPP, to_address=self.person.phone,
                           body="Hi {{ contact.first_name }}", contact=self.person, **kw)

    def test_free_text_send(self):
        ok = response(200, payload={"messages": [{"id": "wamid.ABC"}]})
        with mock.patch("apps.messaging.whatsapp.requests.post", return_value=ok) as post:
            message = self.send()
        message.refresh_from_db()
        self.assertEqual((message.status, message.provider_ref, message.body_text), (MessageStatus.SENT, "wamid.ABC", "Hi Karim"))
        payload = post.call_args.kwargs["json"]
        self.assertEqual((payload["to"], payload["type"], payload["text"]["body"]), ("8801711000000", "text", "Hi Karim"))
        self.assertIn("/1234567890/messages", post.call_args.args[0])
        self.assertTrue(Activity.objects.filter(contact=self.person, kind=ActivityKind.WHATSAPP_SENT).exists())

    def test_approved_template_with_variables(self):
        template = MessageTemplate.objects.create(org=self.org, name="Follow up", channel=Channel.WHATSAPP, body="Hello Karim",
                                                  whatsapp_template="follow_up", whatsapp_language="bn",
                                                  whatsapp_params="{{ contact.first_name }}\n{{ user.first_name }}")
        ok = response(200, payload={"messages": [{"id": "wamid.T"}]})
        with mock.patch("apps.messaging.whatsapp.requests.post", return_value=ok) as post:
            self.send(template=template)
        sent = post.call_args.kwargs["json"]
        self.assertEqual(sent["type"], "template")
        self.assertEqual((sent["template"]["name"], sent["template"]["language"]["code"]), ("follow_up", "bn"))
        self.assertEqual(sent["template"]["components"][0]["parameters"][0]["text"], "Karim")

    def test_outside_window_error_is_explained(self):
        error = response(400, payload={"error": {"code": 131047, "message": "Re-engagement message"}})
        with mock.patch("apps.messaging.whatsapp.requests.post", return_value=error):
            message = self.send()
        message.refresh_from_db()
        self.assertEqual(message.status, MessageStatus.FAILED)
        self.assertIn("approved template", message.error)

    def webhook(self, payload, secret="app-secret"):
        body = json.dumps(payload).encode()
        url = reverse("messaging:whatsapp_webhook", args=[self.cs.webhook_token])
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(url, body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=meta_signature(secret, body))

    def inbound(self, text, wamid="wamid.IN1"):
        return {"entry": [{"changes": [{"field": "messages", "value": {
            "contacts": [{"wa_id": "8801711000000", "profile": {"name": "Karim"}}],
            "messages": [{"from": "8801711000000", "id": wamid, "type": "text", "text": {"body": text}}]}}]}]}

    def test_verify_handshake(self):
        url = reverse("messaging:whatsapp_webhook", args=[self.cs.webhook_token])
        good = self.client.get(url, {"hub.mode": "subscribe", "hub.verify_token": self.cs.whatsapp_verify_token, "hub.challenge": "42"})
        self.assertEqual((good.status_code, good.content), (200, b"42"))
        self.assertEqual(self.client.get(url, {"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "42"}).status_code, 403)

    def test_inbound_message_is_matched_and_deduplicated(self):
        self.assertEqual(self.webhook(self.inbound("Hello"), secret="wrong").status_code, 403)
        self.assertEqual(self.webhook(self.inbound("Hello")).status_code, 200)
        self.webhook(self.inbound("Hello"))  # Meta retries the same message
        message = Message.objects.get(channel=Channel.WHATSAPP)
        self.assertEqual((message.contact, message.body_text, message.conversation.is_unread), (self.person, "Hello", True))
        self.assertTrue(Activity.objects.filter(contact=self.person, kind=ActivityKind.WHATSAPP_RECEIVED).exists())

    def test_stop_opts_out_of_whatsapp_only(self):
        self.webhook(self.inbound("STOP"))
        self.person.refresh_from_db()
        self.assertTrue(self.person.whatsapp_opt_out)
        self.assertFalse(self.person.sms_opt_out)

    def test_status_receipts(self):
        with mock.patch("apps.messaging.whatsapp.requests.post", return_value=response(200, payload={"messages": [{"id": "wamid.S"}]})):
            message = self.send()
        receipt = {"entry": [{"changes": [{"field": "messages", "value": {"statuses": [
            {"id": "wamid.S", "status": "failed", "errors": [{"code": 131026, "title": "Message undeliverable"}]}]}}]}]}
        self.webhook(receipt)
        message.refresh_from_db()
        self.assertEqual((message.status, message.error), (MessageStatus.FAILED, "Message undeliverable"))

    def test_workflow_action_respects_opt_out(self):
        from apps.automation.actions import send_whatsapp

        self.person.whatsapp_opt_out = True
        self.person.save()
        run = mock.Mock(contact=self.person, org=self.org, deal=None, deal_id=None, contact_id=self.person.pk)
        outcome = send_whatsapp(run, {"body": "Hi"}, None)
        self.assertEqual(outcome.status, "skipped")


class FacebookLeadTests(CrmTestCase):
    def setUp(self):
        super().setUp()
        self.fb = FacebookLeadSettings.for_org(self.org)
        self.fb.app_secret = "fb-secret"
        self.fb.default_owner = self.agent.user
        self.fb.save()
        self.fb.add_tags.add(self.tag("Facebook"))
        self.page = FacebookPage.objects.create(org=self.org, page_id="1040", name="ThingBD", access_token="PAGE_TOKEN")

    def post(self, payload, secret="fb-secret"):
        body = json.dumps(payload).encode()
        url = reverse("integrations:facebook_webhook", args=[self.fb.webhook_token])
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(url, body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=meta_signature(secret, body))

    def leadgen(self, leadgen_id="L1", page_id="1040"):
        return {"object": "page", "entry": [{"id": page_id, "changes": [{"field": "leadgen", "value": {
            "leadgen_id": leadgen_id, "page_id": page_id, "form_id": "F9", "ad_id": "A7"}}]}]}

    def graph(self, url, params=None, timeout=None):
        if url.endswith("/F9"):
            return response(200, payload={"name": "Free quote"})
        return response(200, payload={"field_data": [
            {"name": "full_name", "values": ["Nadia Islam"]}, {"name": "email", "values": ["NADIA@example.com"]},
            {"name": "phone_number", "values": ["+8801811000000"]}, {"name": "budget_range", "values": ["50k-1L"]}]})

    def test_signed_lead_creates_contact(self):
        self.assertEqual(self.post(self.leadgen(), secret="wrong").status_code, 403)
        with mock.patch("apps.integrations.facebook.requests.get", side_effect=self.graph) as get:
            self.assertEqual(self.post(self.leadgen()).status_code, 200)
            self.post(self.leadgen())  # duplicate delivery
        lead = FacebookLead.objects.get()
        self.assertEqual((lead.status, lead.form_name, lead.created_contact), (LeadStatus.IMPORTED, "Free quote", True))
        contact = lead.contact
        self.assertEqual((contact.first_name, contact.last_name, contact.email, contact.source, contact.owner),
                         ("Nadia", "Islam", "nadia@example.com", Source.FACEBOOK, self.agent.user))
        self.assertEqual(list(contact.tags.values_list("name", flat=True)), ["Facebook"])
        self.assertIn("Budget range: 50k-1L", contact.notes.get().body)
        self.assertEqual(get.call_args_list[0].kwargs["params"]["access_token"], "PAGE_TOKEN")
        activity = Activity.objects.get(kind=ActivityKind.FACEBOOK_LEAD)
        self.assertEqual(activity.data["page"], self.page.pk)

    def test_existing_contact_is_updated_not_duplicated(self):
        existing = self.contact(email="nadia@example.com", first_name="N.")
        with mock.patch("apps.integrations.facebook.requests.get", side_effect=self.graph):
            self.post(self.leadgen())
        existing.refresh_from_db()
        self.assertEqual(existing.first_name, "N.")  # only blanks are filled
        self.assertEqual(existing.phone, "+8801811000000")
        self.assertFalse(FacebookLead.objects.get().created_contact)

    def test_unknown_page_is_ignored_and_failures_are_kept(self):
        with mock.patch("apps.integrations.facebook.requests.get", side_effect=self.graph):
            self.post(self.leadgen(page_id="999"))
        self.assertFalse(FacebookLead.objects.exists())
        with mock.patch("apps.integrations.facebook.requests.get",
                        return_value=response(400, payload={"error": {"code": 190, "message": "Token expired"}})):
            self.post(self.leadgen())
        lead = FacebookLead.objects.get()
        self.assertEqual(lead.status, LeadStatus.FAILED)
        self.assertIn("Token expired", lead.error)
        FacebookLead.objects.filter(pk=lead.pk).update(updated_at=timezone.now() - timedelta(hours=1))
        with mock.patch("apps.integrations.facebook.requests.get", side_effect=self.graph):
            self.assertEqual(facebook.retry_failed(), 1)

    def test_lead_starts_facebook_workflow(self):
        workflow = Workflow.objects.create(org=self.org, name="FB", trigger_type="facebook_lead", trigger_config={"page": str(self.page.pk)},
                                           is_active=True, created_by=self.admin.user)
        workflow.actions.create(position=0, action_type="add_note", config={"body": "Call within 5 minutes"})
        with mock.patch("apps.integrations.facebook.requests.get", side_effect=self.graph):
            self.post(self.leadgen())
        self.assertEqual(workflow.runs.count(), 1)


class CalendarFeedTests(CrmTestCase):
    def test_feed_has_my_tasks_and_deals_only(self):
        feed = CalendarFeed.objects.create(org=self.org, user=self.agent.user)
        due = timezone.now() + timedelta(days=1)
        Task.objects.create(org=self.org, title="Call, then email; Rahim", assignee=self.agent.user, due_at=due)
        Task.objects.create(org=self.org, title="Someone else's", assignee=self.manager.user, due_at=due)
        pipeline = Pipeline.objects.for_org(self.org).get(is_default=True)
        Deal.objects.create(org=self.org, title="Website", value=50000, pipeline=pipeline, stage=pipeline.stages.first(),
                            owner=self.agent.user, expected_close_date=due.date())
        text = build_feed(feed)
        self.assertTrue(text.startswith("BEGIN:VCALENDAR\r\n"))
        self.assertIn("SUMMARY:Call\\, then email\\; Rahim", text)
        self.assertNotIn("Someone else", text)
        self.assertIn("Close: Website", text)
        self.assertTrue(all(len(line.encode()) <= 75 for line in text.split("\r\n")))

    def test_public_url_and_sync_page(self):
        self.login(self.agent)
        self.client.post(reverse("tasks:calendar_sync"), {"action": "create"})
        feed = CalendarFeed.objects.get(user=self.agent.user)
        self.assertContains(self.client.get(reverse("tasks:calendar_sync")), "calendar.google.com")
        self.client.logout()
        ics = self.client.get(reverse("tasks:calendar_feed", args=[feed.token]))
        self.assertEqual(ics["Content-Type"], "text/calendar; charset=utf-8")
        old = feed.token
        self.login(self.agent)
        self.client.post(reverse("tasks:calendar_sync"), {"action": "rotate"})
        self.assertEqual(self.client.get(reverse("tasks:calendar_feed", args=[old])).status_code, 404)
