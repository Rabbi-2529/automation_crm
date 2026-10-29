import base64
import io
import hashlib
import hmac
from unittest import mock

from django.core import mail
from django.db import connection
from django.test import override_settings
from django.urls import reverse

from apps.activities.models import ActivityKind
from apps.contacts.tests.base import CrmTestCase
from apps.pipeline.models import Deal
from apps.pipeline.services import default_pipeline

from ..inbound import fetch_inbox, twilio_signature_valid
from ..models import ChannelSettings, Conversation, Message, MessageStatus, MessageTemplate, TrackedLink
from ..rendering import build_context, render
from ..services import ingest_email, ingest_sms
from ..sms import normalize_phone


class MessagingTestCase(CrmTestCase):
    def setUp(self):
        super().setUp()
        self.rahim = self.contact(first_name="Rahim", last_name="Uddin", email="rahim@daraz.test", phone="01711000001",
                                  company=self.company("Daraz"), owner=self.agent.user)
        self.settings = ChannelSettings.for_org(self.org)

    def compose(self, channel="email", data=None, **params):
        query = {"channel": channel, "contact": self.rahim.pk, **params}
        url = reverse("messaging:compose") + "?" + "&".join(f"{k}={v}" for k, v in query.items())
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(url, data or {})


class RenderingTests(MessagingTestCase):
    def test_variables_defaults_and_unknowns(self):
        values = build_context(org=self.org, user=self.agent.user, contact=self.rahim)
        text = "Hi {{ contact.first_name }} from {{company.name}} / {{ deal.title|default:\"your deal\" }} {{ contact.secret }}"
        self.assertEqual(render(text, values), "Hi Rahim from Daraz / your deal ")

    def test_no_access_beyond_whitelist(self):
        self.assertEqual(render("{{ contact.__class__ }}{{ user.password }}", build_context(user=self.agent.user)), "")


class EmailSendTests(MessagingTestCase):
    def test_send_email_with_tracking_and_threading_headers(self):
        self.login(self.agent)
        response = self.compose(data={"to": "Rahim@Daraz.test", "subject": "Hello {{ contact.first_name }}",
                                      "body": "See https://thingbd.com/pricing.\n\nThanks"})
        self.assertRedirects(response, reverse("contacts:contact_detail", args=[self.rahim.pk]))
        message = Message.objects.get()
        self.assertEqual((message.status, message.subject, message.to_address), (MessageStatus.SENT, "Hello Rahim", "rahim@daraz.test"))
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ["rahim@daraz.test"])
        self.assertEqual(sent.extra_headers["Message-ID"], message.message_id)
        self.assertIn("ThingBD", sent.from_email)  # workspace name as sender name
        html = sent.alternatives[0][0]
        link = TrackedLink.objects.get(message=message)
        self.assertEqual(link.url, "https://thingbd.com/pricing")
        self.assertIn(reverse("messaging:track_click", args=[link.token]), html)
        self.assertIn(reverse("messaging:track_open", args=[message.tracking_id]), html)
        self.assertTrue(self.rahim.activities.filter(kind=ActivityKind.EMAIL_SENT, message=message).exists())
        self.assertEqual(Conversation.objects.get().contact, self.rahim)

    def test_tracking_off_and_signature(self):
        self.settings.track_opens = self.settings.track_clicks = False
        self.settings.email_signature = "Rabbi, ThingBD"
        self.settings.save()
        self.login(self.agent)
        self.compose(data={"to": "rahim@daraz.test", "subject": "Hi", "body": "Visit https://thingbd.com"})
        sent = mail.outbox[0]
        self.assertIn("Rabbi, ThingBD", sent.body)
        self.assertNotIn("/t/", sent.alternatives[0][0])
        self.assertFalse(TrackedLink.objects.exists())

    def test_html_is_escaped(self):
        self.login(self.agent)
        self.compose(data={"to": "rahim@daraz.test", "subject": "Hi", "body": "<script>alert(1)</script>"})
        self.assertIn("&lt;script&gt;", mail.outbox[0].alternatives[0][0])

    def test_custom_smtp_connection_used(self):
        self.settings.smtp_host, self.settings.smtp_username = "smtp.thingbd.test", "crm@thingbd.com"
        self.settings.smtp_password, self.settings.email_from_address = "s3cret", "crm@thingbd.com"
        self.settings.save()
        self.login(self.agent)
        with mock.patch("apps.messaging.email.get_connection", wraps=mail.get_connection) as get_connection:
            with override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
                self.compose(data={"to": "rahim@daraz.test", "subject": "Hi", "body": "x"})
        kwargs = get_connection.call_args.kwargs
        self.assertEqual((kwargs["host"], kwargs["password"], kwargs["use_tls"]), ("smtp.thingbd.test", "s3cret", True))

    def test_smtp_failure_is_recorded_not_raised(self):
        self.login(self.agent)
        with mock.patch("apps.messaging.email.EmailMultiAlternatives.send", side_effect=OSError("Connection refused")):
            self.compose(data={"to": "rahim@daraz.test", "subject": "Hi", "body": "x"})
        message = Message.objects.get()
        self.assertEqual(message.status, MessageStatus.FAILED)
        self.assertIn("Connection refused", message.error)
        failed = self.rahim.activities.get(kind=ActivityKind.MESSAGE_FAILED)
        self.assertIn("Connection refused", failed.summary)

    def test_template_preview_renders_for_contact_and_deal(self):
        pipeline = default_pipeline(self.org)
        deal = Deal.objects.create(org=self.org, pipeline=pipeline, stage=pipeline.stages.first(), title="CRM rollout", value=250000,
                                   contact=self.rahim)
        template = MessageTemplate.objects.create(org=self.org, name="Follow up", subject="About {{ deal.title }}",
                                                  body="Hi {{ contact.first_name }}, {{ deal.value }}")
        self.login(self.agent)
        data = self.client.get(reverse("messaging:template_preview", args=[template.pk]), {"contact": self.rahim.pk, "deal": deal.pk}).json()
        self.assertEqual(data, {"subject": "About CRM rollout", "body": "Hi Rahim, BDT 250,000"})
        foreign = MessageTemplate.objects.create(org=self.other_org, name="x", body="x")
        self.assertEqual(self.client.get(reverse("messaging:template_preview", args=[foreign.pk])).status_code, 404)

    def test_compose_rejects_bad_address_and_foreign_contact(self):
        self.login(self.agent)
        response = self.compose(data={"to": "not-an-email", "subject": "Hi", "body": "x"})
        self.assertIn("to", response.context["form"].errors)
        foreign = self.contact(org=self.other_org, email="x@y.test")
        self.assertEqual(self.client.get(reverse("messaging:compose"), {"contact": foreign.pk}).status_code, 404)


class TrackingTests(MessagingTestCase):
    def sent_message(self):
        self.login(self.agent)
        self.compose(data={"to": "rahim@daraz.test", "subject": "Pricing", "body": "https://thingbd.com/pricing"})
        self.client.logout()
        return Message.objects.get()

    def test_open_pixel_counts_and_logs_first_open_only(self):
        message = self.sent_message()
        url = reverse("messaging:track_open", args=[message.tracking_id])
        for _ in range(2):
            response = self.client.get(url)
        self.assertEqual(response["Content-Type"], "image/gif")
        message.refresh_from_db()
        self.assertEqual(message.open_count, 2)
        self.assertEqual(self.rahim.activities.filter(kind=ActivityKind.EMAIL_OPENED).count(), 1)

    def test_click_redirects_only_to_stored_url(self):
        message = self.sent_message()
        link = message.links.get()
        response = self.client.get(reverse("messaging:track_click", args=[link.token]))
        self.assertRedirects(response, "https://thingbd.com/pricing", fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("messaging:track_click", args=["made-up"])).status_code, 404)
        message.refresh_from_db()
        self.assertEqual(message.click_count, 1)
        self.assertTrue(self.rahim.activities.filter(kind=ActivityKind.EMAIL_CLICKED).exists())


RAW_REPLY = (
    "From: Rahim Uddin <RAHIM@daraz.test>\r\nTo: crm@thingbd.com\r\nSubject: Re: Pricing\r\n"
    "Message-ID: <reply-1@daraz.test>\r\nIn-Reply-To: {parent}\r\nReferences: {parent}\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n\r\nSounds good, send the contract.\r\n"
)


class InboundEmailTests(MessagingTestCase):
    def test_reply_threads_into_original_conversation(self):
        self.login(self.agent)
        self.compose(data={"to": "rahim@daraz.test", "subject": "Pricing", "body": "Here you go"})
        sent = Message.objects.get()
        reply = ingest_email(self.org, RAW_REPLY.format(parent=sent.message_id).encode())
        self.assertEqual(reply.conversation, sent.conversation)
        self.assertEqual((reply.direction, reply.contact, reply.body_text), ("in", self.rahim, "Sounds good, send the contract."))
        conversation = sent.conversation
        conversation.refresh_from_db()
        self.assertTrue(conversation.is_unread)
        self.assertTrue(self.rahim.activities.filter(kind=ActivityKind.EMAIL_RECEIVED).exists())
        self.assertIsNone(ingest_email(self.org, RAW_REPLY.format(parent=sent.message_id).encode()))  # duplicate

    def test_unknown_sender_html_only_and_save_as_contact(self):
        raw = ("From: New Lead <lead@shop.test>\r\nSubject: Need a quote\r\nMessage-ID: <n1@shop.test>\r\n"
               "Content-Type: text/html; charset=utf-8\r\n\r\n<p>Hello<br>Price?</p><script>x()</script>")
        message = ingest_email(self.org, raw.encode())
        self.assertIsNone(message.contact)
        self.assertEqual(message.body_text, "Hello\nPrice?")
        self.login(self.agent)
        self.client.post(reverse("messaging:create_contact", args=[message.conversation.pk]), {"name": "Shop owner"})
        message.refresh_from_db()
        self.assertEqual((message.contact.email, message.contact.first_name), ("lead@shop.test", "Shop owner"))

    def test_imap_fetch_first_run_bookmarks_then_imports(self):
        self.settings.imap_enabled, self.settings.imap_host = True, "imap.thingbd.test"
        self.settings.imap_username, self.settings.imap_password = "crm", "pw"
        self.settings.save()
        raw = RAW_REPLY.format(parent="<none@x>").encode()
        client = mock.MagicMock()
        client.select.return_value = ("OK", [b"3"])
        client.uid.side_effect = lambda cmd, *args: {
            "search": ("OK", [b"5 6"] if args[-1] == "ALL" else [b"6 7"]),
            "fetch": ("OK", [(b"7 (BODY[] {100}", raw), b")"]),
        }[cmd]
        with mock.patch("apps.messaging.inbound.imaplib.IMAP4_SSL", return_value=client):
            self.assertEqual(fetch_inbox(self.settings), 0)  # first run only remembers UID 6
            self.assertEqual(self.settings.imap_last_uid, 6)
            self.assertEqual(fetch_inbox(self.settings), 1)
        self.assertEqual(self.settings.imap_last_uid, 7)
        self.assertEqual(Message.objects.get().contact, self.rahim)

    def test_imap_error_is_saved(self):
        with mock.patch("apps.messaging.inbound.imaplib.IMAP4_SSL", side_effect=OSError("timed out")):
            fetch_inbox(self.settings)
        self.settings.refresh_from_db()
        self.assertIn("timed out", self.settings.imap_last_error)


class SmsTests(MessagingTestCase):
    def test_normalize_bangladesh_numbers(self):
        self.assertEqual(normalize_phone("01711-000001"), "8801711000001")
        self.assertEqual(normalize_phone("+880 1711 000001"), "8801711000001")
        self.assertEqual(normalize_phone("00880 1711000001"), "8801711000001")

    def test_console_provider_sends_and_logs(self):
        self.settings.sms_provider = "console"
        self.settings.save()
        self.login(self.agent)
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            self.compose("sms", {"to": "01711000001", "body": "Hi {{ contact.first_name }}"})
        message = Message.objects.get()
        self.assertEqual((message.status, message.body_text), (MessageStatus.SENT, "Hi Rahim"))
        self.assertTrue(self.rahim.activities.filter(kind=ActivityKind.SMS_SENT).exists())

    def test_console_provider_survives_non_utf8_console(self):
        """Windows consoles are often cp1252; Bangla text must not make the send fail."""
        self.settings.sms_provider = "console"
        self.settings.save()
        self.login(self.agent)
        console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        with mock.patch("sys.stdout", console):
            self.compose("sms", {"to": "01711000001", "body": "হ্যালো"})
        self.assertEqual(Message.objects.get().status, MessageStatus.SENT)
        console.seek(0)
        self.assertIn(r"\u09b9", console.read())  # escaped, not crashed

    def test_http_gateway_builds_url_and_checks_success(self):
        self.settings.sms_provider, self.settings.sms_sender_id, self.settings.http_api_key = "http", "ThingBD", "KEY 1"
        self.settings.http_url_template = "https://gw.test/send?api_key={api_key}&to={to}&from={sender}&msg={message}"
        self.settings.http_success_text = "SUCCESS"
        self.settings.save()
        self.login(self.agent)
        with mock.patch("apps.messaging.sms.requests.get") as get:
            get.return_value = mock.Mock(status_code=200, text="1001 SUCCESS")
            self.compose("sms", {"to": "01711000001", "body": "দাম কত?"})
        url = get.call_args.args[0]
        self.assertIn("api_key=KEY%201", url)
        self.assertIn("to=8801711000001", url)
        self.assertIn("msg=%E0%A6%A6", url)  # Bangla is percent-encoded
        self.assertEqual(Message.objects.get().status, MessageStatus.SENT)
        with mock.patch("apps.messaging.sms.requests.get", return_value=mock.Mock(status_code=200, text="1002 INVALID")):
            self.compose("sms", {"to": "01711000001", "body": "again"})
        self.assertEqual(Message.objects.latest("pk").status, MessageStatus.FAILED)

    def test_sms_not_configured_fails_cleanly(self):
        self.login(self.agent)
        self.compose("sms", {"to": "01711000001", "body": "x"})
        self.assertIn("isn't set up", Message.objects.get().error)

    def test_twilio_webhook_signature(self):
        self.settings.sms_provider, self.settings.twilio_auth_token = "twilio", "tok"
        self.settings.save()
        url = reverse("messaging:twilio_sms", args=[self.settings.webhook_token])
        params = {"From": "+8801711000001", "Body": "Yes please", "MessageSid": "SM1"}
        payload = "http://testserver" + url + "".join(f"{k}{params[k]}" for k in sorted(params))
        signature = base64.b64encode(hmac.new(b"tok", payload.encode(), hashlib.sha1).digest()).decode()
        self.assertEqual(self.client.post(url, params, HTTP_X_TWILIO_SIGNATURE="bad").status_code, 403)
        response = self.client.post(url, params, HTTP_X_TWILIO_SIGNATURE=signature)
        self.assertEqual(response.status_code, 200)
        message = Message.objects.get()
        self.assertEqual((message.contact, message.body_text), (self.rahim, "Yes please"))
        self.assertTrue(twilio_signature_valid("tok", "http://testserver" + url, params, signature))
        self.assertEqual(self.client.post(reverse("messaging:twilio_sms", args=["nope"]), params).status_code, 404)

    def test_inbound_sms_from_unknown_number(self):
        message = ingest_sms(self.org, "+8801999999999", "Who is this?", "SM9")
        self.assertIsNone(message.contact)
        self.assertIsNone(ingest_sms(self.org, "+8801999999999", "Who is this?", "SM9"))


class InboxTests(MessagingTestCase):
    def test_inbox_filters_reply_and_isolation(self):
        self.login(self.agent)
        self.compose(data={"to": "rahim@daraz.test", "subject": "Pricing", "body": "x"})
        conversation = Conversation.objects.get()
        ingest_email(self.org, RAW_REPLY.format(parent=Message.objects.get().message_id).encode())
        ingest_email(self.other_org, RAW_REPLY.format(parent="<z@z>").replace("reply-1", "other-1").encode())
        page = self.client.get(reverse("messaging:inbox"), {"box": "unread"})
        self.assertEqual([c.pk for c in page.context["conversations"]], [conversation.pk])
        self.assertEqual(page.context["inbox_unread_count"], 1)
        opened = self.client.get(reverse("messaging:inbox"), {"c": conversation.pk})
        self.assertContains(opened, "send the contract")
        conversation.refresh_from_db()
        self.assertFalse(conversation.is_unread)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("messaging:reply", args=[conversation.pk]), {"body": "Contract attached."})
        reply = Message.objects.latest("pk")
        self.assertEqual((reply.subject, reply.in_reply_to), ("Re: Pricing", "<reply-1@daraz.test>"))
        self.assertEqual(mail.outbox[-1].extra_headers["In-Reply-To"], "<reply-1@daraz.test>")
        foreign = Conversation.objects.get(org=self.other_org)
        self.assertEqual(self.client.get(reverse("messaging:inbox"), {"c": foreign.pk}).status_code, 404)
        self.assertEqual(self.client.post(reverse("messaging:reply", args=[foreign.pk]), {"body": "x"}).status_code, 404)


class SettingsAndSecretsTests(MessagingTestCase):
    def test_only_admin_and_secrets_encrypted_and_kept(self):
        self.login(self.manager)
        self.assertEqual(self.client.get(reverse("messaging:channel_settings")).status_code, 403)
        self.login(self.admin)
        data = {"section": "imap", "imap-imap_enabled": "on", "imap-imap_host": "imap.thingbd.test", "imap-imap_port": 993,
                "imap-imap_username": "crm", "imap-imap_password": "Sup3r$ecret", "imap-imap_folder": "INBOX"}
        self.client.post(reverse("messaging:channel_settings"), data)
        with connection.cursor() as cursor:
            cursor.execute("SELECT imap_password FROM messaging_channelsettings WHERE org_id = %s", [self.org.pk])
            stored = cursor.fetchone()[0]
        self.assertTrue(stored.startswith("enc:"))
        self.assertNotIn("Sup3r", stored)
        page = self.client.get(reverse("messaging:channel_settings"), {"tab": "imap"})
        self.assertNotContains(page, "Sup3r$ecret")  # never echoed back
        self.client.post(reverse("messaging:channel_settings"), {**data, "imap-imap_password": "", "imap-imap_folder": "CRM"})
        self.settings.refresh_from_db()
        self.assertEqual((self.settings.imap_password, self.settings.imap_folder), ("Sup3r$ecret", "CRM"))

    def test_http_gateway_template_validated(self):
        self.login(self.admin)
        response = self.client.post(reverse("messaging:channel_settings"), {"section": "sms", "sms-sms_provider": "http",
                                                                             "sms-http_url_template": "https://x.test", "sms-http_method": "GET"})
        self.assertContains(response, "{to} and {message}")

    def test_send_test_email(self):
        self.login(self.admin)
        self.client.post(reverse("messaging:channel_test"), {"action": "email"})
        self.assertEqual(mail.outbox[0].to, [self.admin.user.email])


class CallLogTests(MessagingTestCase):
    def test_log_call_on_timeline(self):
        self.login(self.agent)
        response = self.client.post(reverse("messaging:call_create") + f"?contact={self.rahim.pk}", {
            "direction": "out", "outcome": "connected", "duration_minutes": 12, "called_at": "2026-10-29T10:30", "notes": "Wants demo",
        })
        self.assertRedirects(response, reverse("contacts:contact_detail", args=[self.rahim.pk]))
        activity = self.rahim.activities.get(kind=ActivityKind.CALL_LOGGED)
        self.assertEqual(activity.summary, "Outgoing call: Connected, 12 min")
        self.assertContains(self.client.get(reverse("contacts:contact_detail", args=[self.rahim.pk])), "Wants demo")


class TemplateManagementTests(MessagingTestCase):
    def test_manager_creates_template_agent_cannot(self):
        self.login(self.agent)
        self.assertEqual(self.client.get(reverse("messaging:template_create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("messaging:template_list")).status_code, 200)
        self.login(self.manager)
        response = self.client.post(reverse("messaging:template_create"), {"name": "Intro", "channel": "email", "body": "Hi"})
        self.assertContains(response, "Emails need a subject")
        self.client.post(reverse("messaging:template_create"), {"name": "Intro", "channel": "sms", "body": "Hi {{ contact.first_name }}"})
        self.assertTrue(MessageTemplate.objects.filter(org=self.org, name="Intro", channel="sms").exists())
