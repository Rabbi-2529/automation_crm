import re

from django.urls import reverse

from apps.activities.models import ActivityKind

from ..models import Company, Contact, CustomFieldDefinition, Entity, FieldType, LifecycleStage, Source, Tag
from .base import CrmTestCase


class ContactCrudTests(CrmTestCase):
    def test_agent_creates_contact_with_new_company_and_tags(self):
        tag = self.tag()
        self.login(self.agent)
        response = self.client.post(reverse("contacts:contact_create"), {
            "first_name": "Rahim", "last_name": "Uddin", "email": "Rahim@Example.com", "phone": "+880 1711-000000",
            "new_company": "Daraz", "owner": self.agent.user.pk, "lifecycle_stage": "lead", "tags": [tag.pk],
        })
        contact = Contact.objects.get()
        self.assertRedirects(response, reverse("contacts:contact_detail", args=[contact.pk]))
        self.assertEqual(contact.email, "rahim@example.com")
        self.assertEqual(contact.phone_digits, "8801711000000")
        self.assertEqual(contact.company.name, "Daraz")
        self.assertEqual(contact.company.org, self.org)
        self.assertEqual(contact.source, Source.MANUAL)
        self.assertEqual(contact.created_by, self.agent.user)
        kinds = set(contact.activities.values_list("kind", flat=True))
        self.assertEqual(kinds, {ActivityKind.CREATED, ActivityKind.TAG_ADDED})
        self.assertIsNotNone(Contact.objects.get().last_activity_at)

    def test_requires_name_email_or_phone(self):
        self.login(self.agent)
        response = self.client.post(reverse("contacts:contact_create"), {"lifecycle_stage": "lead", "job_title": "CEO"})
        self.assertContains(response, "at least a name, an email or a phone")

    def test_email_is_unique_per_org_only(self):
        self.contact(email="same@example.com", first_name="Existing")
        self.contact(org=self.other_org, email="other@example.com")
        self.login(self.agent)
        clash = self.client.post(reverse("contacts:contact_create"), {"email": "SAME@example.com", "lifecycle_stage": "lead"})
        self.assertContains(clash, "Existing already uses this email")
        ok = self.client.post(reverse("contacts:contact_create"), {"email": "other@example.com", "lifecycle_stage": "lead"})
        self.assertEqual(ok.status_code, 302)

    def test_possible_duplicate_by_phone_needs_confirmation(self):
        self.contact(first_name="Karim", phone="01711000000")
        self.login(self.agent)
        data = {"first_name": "K.", "phone": "+880 1711 000000", "lifecycle_stage": "lead"}
        first = self.client.post(reverse("contacts:contact_create"), data)
        self.assertContains(first, "Possible duplicates")
        self.assertContains(first, "Save anyway")
        self.assertEqual(Contact.objects.count(), 1)
        second = self.client.post(reverse("contacts:contact_create"), {**data, "confirm_duplicate": "1"})
        self.assertEqual(second.status_code, 302)
        self.assertEqual(Contact.objects.count(), 2)

    def test_update_logs_stage_owner_field_and_tag_changes(self):
        tag = self.tag()
        contact = self.contact(first_name="Rahim", lifecycle_stage=LifecycleStage.LEAD)
        self.login(self.agent)
        self.client.post(reverse("contacts:contact_edit", args=[contact.pk]), {
            "first_name": "Rahim", "job_title": "CTO", "lifecycle_stage": "customer", "owner": self.manager.user.pk,
            "tags": [tag.pk],
        })
        summaries = {a.kind: a.summary for a in contact.activities.all()}
        self.assertEqual(summaries[ActivityKind.STAGE_CHANGED], "Lifecycle stage: Lead → Customer")
        self.assertIn(self.manager.user.display_name, summaries[ActivityKind.OWNER_CHANGED])
        self.assertEqual(summaries[ActivityKind.UPDATED], "Updated Job title")
        self.assertIn("VIP", summaries[ActivityKind.TAG_ADDED])
        stage_event = contact.activities.get(kind=ActivityKind.STAGE_CHANGED)
        self.assertEqual(stage_event.data, {"from": "lead", "to": "customer"})

    def test_unchanged_save_logs_nothing(self):
        contact = self.contact(first_name="Rahim")
        self.login(self.agent)
        self.client.post(reverse("contacts:contact_edit", args=[contact.pk]), {"first_name": "Rahim", "lifecycle_stage": "lead"})
        self.assertFalse(contact.activities.exists())

    def test_cannot_pick_company_owner_or_tag_from_another_org(self):
        foreign_company = self.company(org=self.other_org)
        foreign_tag = self.tag(org=self.other_org)
        self.login(self.agent)
        for field, value in (("company", foreign_company.pk), ("owner", self.outsider.user.pk), ("tags", [foreign_tag.pk])):
            with self.subTest(field=field):
                response = self.client.post(reverse("contacts:contact_create"), {"first_name": "X", "lifecycle_stage": "lead", field: value})
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context["form"].errors)
        self.assertFalse(Contact.objects.exists())

    def test_delete_needs_manager(self):
        contact = self.contact(first_name="Rahim")
        self.login(self.agent)
        self.assertEqual(self.client.post(reverse("contacts:contact_delete", args=[contact.pk])).status_code, 403)
        self.login(self.manager)
        self.client.post(reverse("contacts:contact_delete", args=[contact.pk]))
        self.assertFalse(Contact.objects.exists())


class ContactIsolationTests(CrmTestCase):
    def test_foreign_contact_pages_404(self):
        foreign = self.contact(org=self.other_org, first_name="Secret")
        self.login(self.admin)
        for name in ("contacts:contact_detail", "contacts:contact_edit", "contacts:contact_delete"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name, args=[foreign.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("contacts:contact_list")), "Secret")

    def test_bulk_action_ignores_foreign_ids(self):
        foreign = self.contact(org=self.other_org, first_name="Secret")
        tag = self.tag()
        self.login(self.agent)
        self.client.post(reverse("contacts:contact_bulk"), {"action": "add_tag", "tag": tag.pk, "ids": [foreign.pk]})
        self.assertFalse(foreign.tags.exists())


class ContactListTests(CrmTestCase):
    def setUp(self):
        super().setUp()
        self.acme = self.company("Acme Ltd")
        self.vip = self.tag("VIP")
        self.rahim = self.contact(first_name="Rahim", email="rahim@acme.com", phone="01711-223344", company=self.acme,
                                  lifecycle_stage="customer", owner=self.agent.user)
        self.rahim.tags.add(self.vip)
        self.karim = self.contact(first_name="Karim", email="karim@other.com", lifecycle_stage="lead")
        self.login(self.agent)

    def table(self, search="", **params):
        """Calls the DataTables endpoint the way the browser does; ordered by name (column 1) unless overridden."""
        query = {"draw": 3, "start": 0, "length": 25, "search[value]": search, "order[0][column]": 1, "order[0][dir]": "asc",
                 **params}
        return self.client.get(reverse("contacts:contact_table"), query).json()

    def names(self, search="", **params):
        return [re.search(r"<span>([^<]+)<span", row[1]).group(1) for row in self.table(search, **params)["data"]]

    def test_search_by_name_email_company_and_phone_digits(self):
        self.assertEqual(self.names("rahim"), ["Rahim"])
        self.assertEqual(self.names("other.com"), ["Karim"])
        self.assertEqual(self.names("acme"), ["Rahim"])
        self.assertEqual(self.names("223344"), ["Rahim"])

    def test_filters(self):
        self.assertEqual(self.names(stage="lead"), ["Karim"])
        self.assertEqual(self.names(tag=self.vip.pk), ["Rahim"])
        self.assertEqual(self.names(owner=self.agent.user.pk), ["Rahim"])
        self.assertEqual(self.names(owner="none"), ["Karim"])

    def test_datatables_protocol(self):
        payload = self.table(stage="lead")
        self.assertEqual((payload["draw"], payload["recordsTotal"], payload["recordsFiltered"]), (3, 2, 1))
        self.assertEqual(len(payload["data"][0]), 9)
        self.assertIn('name="ids"', payload["data"][0][0])
        # Ordering by column 1 desc, and paging.
        self.assertEqual(self.names(**{"order[0][dir]": "desc"}), ["Rahim", "Karim"])
        self.assertEqual(self.names(length=1, start=1), ["Rahim"])
        # Non-sortable column (tags) or junk input falls back to default ordering instead of erroring.
        self.assertEqual(len(self.table(**{"order[0][column]": 6})["data"]), 2)
        self.assertEqual(self.client.get(reverse("contacts:contact_table"), {"start": "x", "length": "999"}).status_code, 200)

    def test_cells_are_escaped(self):
        self.contact(first_name="<script>alert(1)</script>")
        row = next(r for r in self.table()["data"] if "script" in r[1])
        self.assertIn("&lt;script&gt;", row[1])
        self.assertNotIn("<script>", row[1])

    def test_table_is_org_scoped(self):
        self.contact(org=self.other_org, first_name="Secret")
        payload = self.table()
        self.assertEqual(payload["recordsTotal"], 2)
        self.assertNotIn("Secret", str(payload["data"]))

    def test_bulk_assign_owner_and_stage(self):
        ids = [self.rahim.pk, self.karim.pk]
        self.client.post(reverse("contacts:contact_bulk"), {"action": "assign_owner", "owner": self.manager.user.pk, "ids": ids})
        self.client.post(reverse("contacts:contact_bulk"), {"action": "set_stage", "stage": "qualified", "ids": ids})
        for c in Contact.objects.all():
            self.assertEqual(c.owner, self.manager.user)
            self.assertEqual(c.lifecycle_stage, "qualified")
            self.assertTrue(c.activities.filter(kind=ActivityKind.STAGE_CHANGED).exists())

    def test_bulk_delete_needs_manager(self):
        response = self.client.post(reverse("contacts:contact_bulk"), {"action": "delete", "ids": [self.karim.pk]})
        self.assertEqual(response.status_code, 403)
        self.login(self.manager)
        self.client.post(reverse("contacts:contact_bulk"), {"action": "delete", "ids": [self.karim.pk]})
        self.assertFalse(Contact.objects.filter(pk=self.karim.pk).exists())

    def test_export_respects_filters_and_role(self):
        self.assertEqual(self.client.get(reverse("contacts:contact_export")).status_code, 403)
        self.login(self.manager)
        response = self.client.get(reverse("contacts:contact_export"), {"stage": "customer"})
        body = b"".join(response.streaming_content).decode("utf-8-sig")
        self.assertIn("rahim@acme.com", body)
        self.assertNotIn("karim@other.com", body)
        self.assertIn("Acme Ltd", body)
        self.assertIn("VIP", body)

    def test_export_neutralises_formula_cells(self):
        self.contact(first_name="=HYPERLINK(\"http://evil\")", email="x@y.com")
        self.login(self.manager)
        body = b"".join(self.client.get(reverse("contacts:contact_export")).streaming_content).decode("utf-8-sig")
        self.assertIn("'=HYPERLINK", body)


class CustomFieldTests(CrmTestCase):
    def setUp(self):
        super().setUp()
        CustomFieldDefinition.objects.create(org=self.org, entity=Entity.CONTACT, key="budget", label="Budget", field_type=FieldType.NUMBER)
        CustomFieldDefinition.objects.create(org=self.org, entity=Entity.CONTACT, key="district", label="District",
                                             field_type=FieldType.SELECT, options=["Dhaka", "Chattogram"], required=True)
        CustomFieldDefinition.objects.create(org=self.org, entity=Entity.CONTACT, key="met_on", label="Met on", field_type=FieldType.DATE)

    def test_values_are_validated_and_stored_as_json(self):
        self.login(self.agent)
        self.client.post(reverse("contacts:contact_create"), {
            "first_name": "Rahim", "lifecycle_stage": "lead", "cf_budget": "50000", "cf_district": "Dhaka", "cf_met_on": "2026-10-01",
        })
        contact = Contact.objects.get()
        self.assertEqual(contact.custom_fields, {"budget": 50000, "district": "Dhaka", "met_on": "2026-10-01"})
        page = self.client.get(reverse("contacts:contact_detail", args=[contact.pk]))
        self.assertContains(page, "01 Oct 2026")

    def test_required_and_invalid_values_rejected(self):
        self.login(self.agent)
        response = self.client.post(reverse("contacts:contact_create"), {"first_name": "Rahim", "lifecycle_stage": "lead",
                                                                          "cf_budget": "lots", "cf_district": "Sylhet"})
        errors = response.context["form"].errors
        self.assertIn("cf_budget", errors)
        self.assertIn("cf_district", errors)

    def test_custom_field_change_is_logged(self):
        contact = self.contact(first_name="Rahim", custom_fields={"district": "Dhaka"})
        self.login(self.agent)
        self.client.post(reverse("contacts:contact_edit", args=[contact.pk]), {
            "first_name": "Rahim", "lifecycle_stage": "lead", "cf_district": "Chattogram",
        })
        update = contact.activities.get(kind=ActivityKind.UPDATED)
        self.assertEqual(update.summary, "Updated District")
        self.assertEqual(update.data["changes"]["custom_fields.district"], {"from": "Dhaka", "to": "Chattogram"})

    def test_only_admin_manages_definitions_and_key_is_generated(self):
        self.login(self.manager)
        self.assertEqual(self.client.get(reverse("contacts:custom_field_list")).status_code, 403)
        self.login(self.admin)
        self.client.post(reverse("contacts:custom_field_create"), {"entity": "company", "label": "Annual revenue",
                                                                     "field_type": "number", "position": 0})
        self.assertTrue(CustomFieldDefinition.objects.filter(org=self.org, entity="company", key="annual_revenue").exists())

    def test_dropdown_needs_options(self):
        self.login(self.admin)
        response = self.client.post(reverse("contacts:custom_field_create"), {"entity": "contact", "label": "Tier",
                                                                                "field_type": "select", "position": 0})
        self.assertContains(response, "Add at least one option")


class CompanyTests(CrmTestCase):
    def test_create_company_normalises_domain_and_lists_people(self):
        self.login(self.agent)
        self.client.post(reverse("contacts:company_create"), {"name": "ThingBD", "domain": "https://www.ThingBD.com/"})
        company = Company.objects.get(org=self.org)
        self.assertEqual(company.domain, "thingbd.com")
        self.contact(first_name="Rabbi", company=company)
        page = self.client.get(reverse("contacts:company_detail", args=[company.pk]))
        self.assertContains(page, "Rabbi")
        self.assertTrue(company.activities.filter(kind=ActivityKind.CREATED).exists())

    def test_duplicate_company_name_rejected(self):
        self.company("ThingBD")
        self.login(self.agent)
        response = self.client.post(reverse("contacts:company_create"), {"name": "thingbd"})
        self.assertContains(response, "already exists")

    def test_deleting_company_keeps_contacts(self):
        company = self.company()
        contact = self.contact(first_name="Rahim", company=company)
        self.login(self.manager)
        self.client.post(reverse("contacts:company_delete", args=[company.pk]))
        contact.refresh_from_db()
        self.assertIsNone(contact.company)

    def test_company_list_counts_and_isolation(self):
        acme = self.company("Acme")
        self.contact(first_name="A", company=acme)
        self.contact(first_name="B", company=acme)
        self.company("Hidden Co", org=self.other_org)
        self.login(self.agent)
        payload = self.client.get(reverse("contacts:company_table"), {"draw": 1, "order[0][column]": 3, "order[0][dir]": "desc"}).json()
        self.assertEqual(payload["recordsTotal"], 1)
        self.assertIn("Acme", payload["data"][0][0])
        self.assertEqual(payload["data"][0][3], 2)


class TagTests(CrmTestCase):
    def test_manager_manages_tags_agent_cannot(self):
        self.login(self.agent)
        self.assertEqual(self.client.get(reverse("contacts:tag_list")).status_code, 403)
        self.login(self.manager)
        self.client.post(reverse("contacts:tag_create"), {"name": "Hot lead", "color": "red"})
        self.assertTrue(Tag.objects.filter(org=self.org, name="Hot lead", color="red").exists())
        response = self.client.post(reverse("contacts:tag_create"), {"name": "hot LEAD", "color": "red"})
        self.assertContains(response, "already exists")
