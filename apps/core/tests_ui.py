from django.urls import reverse

from apps.contacts.tests.base import CrmTestCase
from apps.core.fields import decrypt, encrypt
from apps.pipeline.models import Deal, DealStatus
from apps.pipeline.services import default_pipeline


class LookupTests(CrmTestCase):
    def test_select2_lookup_format_and_isolation(self):
        company = self.company("Daraz")
        self.contact(first_name="Rahim", email="rahim@daraz.test", company=company)
        self.contact(org=self.other_org, first_name="Rahim Secret")
        self.login(self.agent)
        data = self.client.get(reverse("contacts:contact_lookup"), {"q": "rahim"}).json()
        self.assertEqual(data["results"], [{"id": data["results"][0]["id"], "text": "Rahim", "hint": "rahim@daraz.test · Daraz"}])
        companies = self.client.get(reverse("contacts:company_lookup"), {"q": "dar"}).json()["results"]
        self.assertEqual([c["text"] for c in companies], ["Daraz"])

    def test_lookup_widget_renders_only_current_choice(self):
        contact = self.contact(first_name="Rahim")
        self.contact(first_name="Other person")
        self.login(self.agent)
        page = self.client.get(reverse("pipeline:deal_create"), {"contact": contact.pk})
        self.assertContains(page, 'data-select2-ajax="/contacts/lookup/"')
        self.assertContains(page, f'<option value="{contact.pk}" selected>Rahim</option>', html=True)
        self.assertNotContains(page, "Other person")


class DealTableTests(CrmTestCase):
    def test_deal_table_filters_total_and_isolation(self):
        pipeline = default_pipeline(self.org)
        stage = pipeline.stages.first()
        Deal.objects.create(org=self.org, pipeline=pipeline, stage=stage, title="Open big", value=5000)
        Deal.objects.create(org=self.org, pipeline=pipeline, stage=stage, title="Won one", value=100, status=DealStatus.WON)
        other = default_pipeline(self.other_org)
        Deal.objects.create(org=self.other_org, pipeline=other, stage=other.stages.first(), title="Secret", value=1)
        self.login(self.agent)
        payload = self.client.get(reverse("pipeline:deal_table"), {"draw": 1, "order[0][column]": 1, "order[0][dir]": "desc"}).json()
        self.assertEqual((payload["recordsTotal"], payload["recordsFiltered"], payload["filteredValue"]), (2, 1, 5000.0))
        self.assertIn("Open big", payload["data"][0][0])
        everything = self.client.get(reverse("pipeline:deal_table"), {"status": "all"}).json()
        self.assertEqual(everything["recordsFiltered"], 2)
        self.assertNotIn("Secret", str(everything))


class EncryptionTests(CrmTestCase):
    def test_roundtrip_and_legacy_plaintext(self):
        token = encrypt("hunter2")
        self.assertTrue(token.startswith("enc:"))
        self.assertNotIn("hunter2", token)
        self.assertEqual(decrypt(token), "hunter2")
        self.assertEqual(decrypt("plain-old"), "plain-old")
        self.assertEqual(decrypt("enc:garbage"), "")


class PagesRenderTests(CrmTestCase):
    def test_new_pages_render_for_their_roles(self):
        contact = self.contact(first_name="Rahim", email="r@x.test", phone="01711000001")
        self.login(self.agent)
        for url in [reverse("messaging:inbox"), reverse("tasks:calendar"), reverse("messaging:template_list"),
                    reverse("contacts:contact_list"), reverse("contacts:company_list"), reverse("pipeline:deal_list"),
                    reverse("messaging:compose") + f"?channel=email&contact={contact.pk}",
                    reverse("messaging:compose") + f"?channel=sms&contact={contact.pk}",
                    reverse("messaging:call_create") + f"?contact={contact.pk}"]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.login(self.admin)
        for tab in ("email", "imap", "sms"):
            self.assertEqual(self.client.get(reverse("messaging:channel_settings"), {"tab": tab}).status_code, 200)
