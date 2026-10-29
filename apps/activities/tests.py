from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from apps.contacts.tests.base import CrmTestCase

from .models import ActivityKind, Attachment, Note


class NoteTests(CrmTestCase):
    def test_add_note_to_contact_and_company(self):
        contact = self.contact(first_name="Rahim")
        company = self.company()
        self.login(self.agent)
        self.client.post(reverse("activities:note_create", args=["contact", contact.pk]), {"body": "Called, wants a demo"})
        self.client.post(reverse("activities:note_create", args=["company", company.pk]), {"body": "Office in Gulshan"})
        self.assertEqual(contact.notes.get().body, "Called, wants a demo")
        self.assertEqual(company.notes.get().author, self.agent.user)
        page = self.client.get(reverse("contacts:contact_detail", args=[contact.pk]))
        self.assertContains(page, "Called, wants a demo")
        self.assertTrue(contact.activities.filter(kind=ActivityKind.NOTE_ADDED).exists())

    def test_cannot_note_on_foreign_or_unknown_target(self):
        foreign = self.contact(org=self.other_org)
        self.login(self.agent)
        self.assertEqual(self.client.post(reverse("activities:note_create", args=["contact", foreign.pk]), {"body": "x"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("activities:note_create", args=["user", 1]), {"body": "x"}).status_code, 404)
        self.assertFalse(Note.objects.exists())

    def test_only_author_or_manager_deletes_note(self):
        contact = self.contact(first_name="Rahim")
        note = Note.objects.create(org=self.org, contact=contact, author=self.manager.user, body="mine")
        self.login(self.agent)
        self.assertEqual(self.client.post(reverse("activities:note_delete", args=[note.pk])).status_code, 403)
        self.login(self.admin)
        self.client.post(reverse("activities:note_delete", args=[note.pk]))
        self.assertFalse(Note.objects.exists())


class AttachmentTests(CrmTestCase):
    def upload(self, contact, name="quote.pdf", content=b"%PDF-1.4 test"):
        return self.client.post(reverse("activities:attachment_upload", args=["contact", contact.pk]),
                                {"file": SimpleUploadedFile(name, content)})

    def test_upload_and_download_within_org(self):
        contact = self.contact(first_name="Rahim")
        self.login(self.agent)
        self.upload(contact)
        attachment = Attachment.objects.get()
        self.assertEqual((attachment.original_name, attachment.size), ("quote.pdf", 13))
        self.assertIn(f"org_{self.org.pk}/attachments/", attachment.file.name)
        response = self.client.get(reverse("activities:attachment_download", args=[attachment.pk]))
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4 test")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.login(self.outsider)
        self.assertEqual(self.client.get(reverse("activities:attachment_download", args=[attachment.pk])).status_code, 404)

    def test_html_is_forced_to_download(self):
        contact = self.contact(first_name="Rahim")
        self.login(self.agent)
        self.upload(contact, name="x.html", content=b"<script>alert(1)</script>")
        response = self.client.get(reverse("activities:attachment_download", args=[Attachment.objects.get().pk]))
        self.assertIn("attachment;", response["Content-Disposition"])

    @override_settings(ATTACHMENT_MAX_MB=0)
    def test_size_limit(self):
        contact = self.contact(first_name="Rahim")
        self.login(self.agent)
        self.upload(contact)
        self.assertFalse(Attachment.objects.exists())

    def test_delete_removes_file(self):
        contact = self.contact(first_name="Rahim")
        self.login(self.agent)
        self.upload(contact)
        attachment = Attachment.objects.get()
        storage, path = attachment.file.storage, attachment.file.name
        self.client.post(reverse("activities:attachment_delete", args=[attachment.pk]))
        self.assertFalse(Attachment.objects.exists())
        self.assertFalse(storage.exists(path))
