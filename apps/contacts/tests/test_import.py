from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.activities.models import ActivityKind

from ..models import Company, Contact, CustomFieldDefinition, Entity, FieldType, ImportJob, ImportStatus, Source, Tag
from .base import CrmTestCase

CSV = (
    "Full First,Surname,E-mail,Mobile,Organisation,Stage,Labels,Budget\n"
    "Rahim,Uddin,rahim@acme.com,01711000000,Acme Ltd,Customer,\"vip, dhaka\",50000\n"
    "Karim,Hasan,KARIM@acme.com,,acme ltd,lead,,\n"
    ",,not-an-email,,,,,\n"
    "Nadia,,nadia@x.com,,,,,lots\n"
    "\n"
    "Sara,,sara@x.com,,,unknown-stage,,\n"
)


def upload(content=CSV, name="leads.csv"):
    return SimpleUploadedFile(name, content.encode("utf-8"), content_type="text/csv")


class ImportFlowTests(CrmTestCase):
    def setUp(self):
        super().setUp()
        CustomFieldDefinition.objects.create(org=self.org, entity=Entity.CONTACT, key="budget", label="Budget", field_type=FieldType.NUMBER)
        self.login(self.manager)

    def start(self, content=CSV, **mapping_overrides):
        response = self.client.post(reverse("contacts:import_upload"), {"file": upload(content)})
        job = ImportJob.objects.latest("pk")
        self.assertRedirects(response, reverse("contacts:import_map", args=[job.pk]))
        form = self.client.get(reverse("contacts:import_map", args=[job.pk])).context["form"]
        data = {name: form[name].initial or "" for name in form.column_names}
        data.update(mapping_overrides)
        data.setdefault("duplicate_strategy", "skip")
        return job, data

    def run_import(self, job, data):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("contacts:import_map", args=[job.pk]), data)
        self.assertRedirects(response, reverse("contacts:import_detail", args=[job.pk]))
        job.refresh_from_db()
        return job

    def test_headers_are_guessed(self):
        job, data = self.start()
        self.assertEqual(job.total_rows, 5)  # the blank line isn't counted
        # col_0 "Full First" has no confident match; the rest map by alias or label.
        self.assertEqual(data, {
            "col_0": "", "col_1": "last_name", "col_2": "email", "col_3": "phone", "col_4": "company",
            "col_5": "lifecycle_stage", "col_6": "", "col_7": "cf:budget", "duplicate_strategy": "skip",
        })

    def test_full_import(self):
        job, data = self.start(col_0="first_name", col_6="tags", default_owner=self.agent.user.pk)
        job = self.run_import(job, data)

        self.assertEqual(job.status, ImportStatus.DONE)
        self.assertEqual((job.created_count, job.updated_count, job.skipped_count, job.error_count), (2, 0, 0, 3))
        self.assertEqual(job.processed_rows, 5)
        self.assertEqual([e["row"] for e in job.errors], [4, 5, 7])
        self.assertIn("not a valid email", job.errors[0]["message"])
        self.assertIn("Budget", job.errors[1]["message"])
        self.assertIn("unknown-stage", job.errors[2]["message"])

        rahim = Contact.objects.get(email="rahim@acme.com")
        self.assertEqual(rahim.lifecycle_stage, "customer")
        self.assertEqual(rahim.custom_fields, {"budget": 50000})
        self.assertEqual(rahim.source, Source.IMPORT)
        self.assertEqual(rahim.owner, self.agent.user)
        self.assertEqual(sorted(t.name for t in rahim.tags.all()), ["dhaka", "vip"])
        karim = Contact.objects.get(email="karim@acme.com")
        self.assertEqual(karim.company, rahim.company)  # "acme ltd" matched "Acme Ltd"
        self.assertEqual(Company.objects.filter(org=self.org).count(), 1)
        self.assertTrue(rahim.activities.filter(kind=ActivityKind.IMPORTED).exists())

        detail = self.client.get(reverse("contacts:import_detail", args=[job.pk]))
        self.assertContains(detail, "not a valid email")

    def test_skip_vs_update_existing_email(self):
        existing = self.contact(email="rahim@acme.com", first_name="Old", job_title="Keep me")
        content = "First name,Email,Phone\nRahim,RAHIM@acme.com,01711000000\nNew,new@x.com,\n"
        job, data = self.start(content)
        job = self.run_import(job, data)
        existing.refresh_from_db()
        self.assertEqual((existing.first_name, job.skipped_count, job.created_count), ("Old", 1, 1))

        job, data = self.start(content, duplicate_strategy="update")
        job = self.run_import(job, data)
        existing.refresh_from_db()
        self.assertEqual(existing.first_name, "Rahim")
        self.assertEqual(existing.phone, "01711000000")
        self.assertEqual(existing.job_title, "Keep me")  # unmapped columns don't wipe data
        self.assertEqual((job.updated_count, job.created_count), (2, 0))

    def test_extra_tags_applied_to_every_row(self):
        tag = self.tag("Expo 2026")
        job, data = self.start(col_0="first_name", add_tags=[tag.pk])
        self.run_import(job, data)
        self.assertEqual(tag.contacts.count(), 2)

    def test_owner_column_must_be_member(self):
        content = "Email,Owner\na@x.com,{}\nb@x.com,{}\n".format(self.agent.user.email.upper(), self.outsider.user.email)
        job, data = self.start(content)
        job = self.run_import(job, data | {"col_1": "owner"})
        self.assertEqual(Contact.objects.get(email="a@x.com").owner, self.agent.user)
        self.assertEqual(job.error_count, 1)
        self.assertIn("not a member", job.errors[0]["message"])

    def test_mapping_requires_an_identity_column_and_unique_targets(self):
        job, _ = self.start()
        response = self.client.post(reverse("contacts:import_map", args=[job.pk]), {"col_4": "company", "duplicate_strategy": "skip"})
        self.assertContains(response, "Map at least one of")
        response = self.client.post(reverse("contacts:import_map", args=[job.pk]),
                                    {"col_0": "email", "col_2": "email", "duplicate_strategy": "skip"})
        self.assertContains(response, "one column only")

    def test_rejects_non_csv_and_empty_files(self):
        response = self.client.post(reverse("contacts:import_upload"), {"file": upload(name="leads.xlsx")})
        self.assertContains(response, "Upload a .csv file")
        response = self.client.post(reverse("contacts:import_upload"), {"file": upload("Email\n")})
        self.assertContains(response, "no data rows")

    def test_windows_1252_file_is_read(self):
        content = "Name,Email\nJos\xe9,jose@x.com\n".encode("cp1252")
        self.client.post(reverse("contacts:import_upload"), {"file": SimpleUploadedFile("w.csv", content)})
        job = ImportJob.objects.get()
        self.run_import(job, {"col_0": "first_name", "col_1": "email", "duplicate_strategy": "skip"})
        self.assertEqual(Contact.objects.get().first_name, "José")

    def test_import_pages_need_manager_and_are_org_scoped(self):
        job, _ = self.start()
        self.login(self.agent)
        self.assertEqual(self.client.get(reverse("contacts:import_upload")).status_code, 403)
        self.login(self.outsider)
        self.assertEqual(self.client.get(reverse("contacts:import_detail", args=[job.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("contacts:import_map", args=[job.pk])).status_code, 404)

    def test_tags_reuse_existing_case_insensitively(self):
        Tag.objects.create(org=self.org, name="VIP")
        job, data = self.start(col_0="first_name", col_6="tags")
        self.run_import(job, data)
        self.assertEqual(Tag.objects.filter(org=self.org, name__iexact="vip").count(), 1)
