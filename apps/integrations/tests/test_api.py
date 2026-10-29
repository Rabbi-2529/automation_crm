from unittest import mock

from django.core.cache import cache
from rest_framework.test import APIClient

from apps.activities.models import Activity, ActivityKind
from apps.contacts.models import Contact, CustomFieldDefinition, Entity, Source
from apps.contacts.tests.base import CrmTestCase
from apps.integrations.api.auth import ApiKeyRateThrottle
from apps.integrations.models import ApiKey, ApiScope
from apps.pipeline.models import Deal, DealStatus, LostReason, Pipeline
from apps.tasks.models import Task


class ApiTestCase(CrmTestCase):
    def setUp(self):
        super().setUp()
        self.key, self.raw = ApiKey.issue(org=self.org, name="Website", scope=ApiScope.WRITE, user=self.manager.user)
        self.api = self.client_for(self.raw)

    def client_for(self, raw):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        return client


class AuthTests(ApiTestCase):
    def test_key_is_hashed_and_identifies_workspace(self):
        self.assertNotIn(self.raw, ApiKey.objects.values_list("key_hash", flat=True))
        response = self.api.get("/api/v1/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "ThingBD")
        self.key.refresh_from_db()
        self.assertIsNotNone(self.key.last_used_at)

    def test_missing_bad_and_revoked_keys_are_rejected(self):
        self.assertEqual(APIClient().get("/api/v1/contacts/").status_code, 401)
        self.assertEqual(self.client_for("tbd_nope_nope").get("/api/v1/contacts/").status_code, 401)
        self.key.revoke()
        self.assertEqual(self.api.get("/api/v1/contacts/").status_code, 401)

    def test_x_api_key_header_works(self):
        client = APIClient()
        client.credentials(HTTP_X_API_KEY=self.raw)
        self.assertEqual(client.get("/api/v1/me/").status_code, 200)

    def test_key_stops_working_when_creator_leaves(self):
        self.manager.is_active = False
        self.manager.save()
        self.assertEqual(self.api.get("/api/v1/me/").status_code, 401)

    def test_read_only_key_cannot_write(self):
        _, raw = ApiKey.issue(org=self.org, name="Reports", scope=ApiScope.READ, user=self.manager.user)
        client = self.client_for(raw)
        self.assertEqual(client.get("/api/v1/contacts/").status_code, 200)
        self.assertEqual(client.post("/api/v1/contacts/", {"email": "a@example.com"}, format="json").status_code, 403)

    def test_agent_key_cannot_delete(self):
        _, raw = ApiKey.issue(org=self.org, name="Agent", scope=ApiScope.WRITE, user=self.agent.user)
        contact = self.contact(email="x@example.com")
        self.assertEqual(self.client_for(raw).delete(f"/api/v1/contacts/{contact.pk}/").status_code, 403)
        self.assertEqual(self.api.delete(f"/api/v1/contacts/{contact.pk}/").status_code, 204)

    def test_docs_and_schema_are_public(self):
        self.assertEqual(self.client.get("/api/v1/schema/").status_code, 200)
        self.assertEqual(self.client.get("/api/docs/").status_code, 200)

    def test_rate_limited_per_key(self):
        cache.clear()
        with mock.patch.object(ApiKeyRateThrottle, "THROTTLE_RATES", {"api_key": "2/min"}):
            codes = [self.api.get("/api/v1/me/").status_code for _ in range(3)]
            _, other = ApiKey.issue(org=self.org, name="Other", scope=ApiScope.READ, user=self.manager.user)
            other_code = self.client_for(other).get("/api/v1/me/").status_code
        cache.clear()
        self.assertEqual(codes, [200, 200, 429])
        self.assertEqual(other_code, 200)


class ContactApiTests(ApiTestCase):
    def test_workspace_isolation(self):
        mine = self.contact(email="mine@example.com")
        theirs = self.contact(org=self.other_org, email="theirs@example.com")
        response = self.api.get("/api/v1/contacts/")
        emails = [c["email"] for c in response.json()["results"]]
        self.assertEqual(emails, ["mine@example.com"])
        self.assertEqual(self.api.get(f"/api/v1/contacts/{theirs.pk}/").status_code, 404)
        self.assertEqual(self.api.get(f"/api/v1/contacts/{mine.pk}/").status_code, 200)

    def test_create_records_timeline_and_tags(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.api.post("/api/v1/contacts/", {"first_name": "Rahim", "email": "RAHIM@example.com", "tags": ["Website", "VIP"]},
                                     format="json")
        self.assertEqual(response.status_code, 201, response.content)
        contact = Contact.objects.get(pk=response.json()["id"])
        self.assertEqual((contact.email, contact.source, contact.owner), ("rahim@example.com", Source.API, self.manager.user))
        self.assertEqual(sorted(response.json()["tags"]), ["VIP", "Website"])
        kinds = list(Activity.objects.filter(contact=contact).values_list("kind", flat=True))
        self.assertIn(ActivityKind.CREATED, kinds)
        self.assertEqual(kinds.count(ActivityKind.TAG_ADDED), 2)
        self.assertEqual(Activity.objects.get(contact=contact, kind=ActivityKind.CREATED).actor, self.manager.user)

    def test_cannot_use_foreign_ids(self):
        foreign_company = self.company(org=self.other_org)
        response = self.api.post("/api/v1/contacts/", {"email": "a@example.com", "company": foreign_company.pk,
                                                       "owner": self.outsider.user.pk}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("company", response.json())
        self.assertIn("owner", response.json())

    def test_update_records_changes(self):
        contact = self.contact(email="a@example.com", job_title="Clerk")
        response = self.api.patch(f"/api/v1/contacts/{contact.pk}/", {"job_title": "Director"}, format="json")
        self.assertEqual(response.status_code, 200)
        activity = Activity.objects.get(contact=contact, kind=ActivityKind.UPDATED)
        self.assertEqual(activity.data["changes"]["job_title"], {"from": "Clerk", "to": "Director"})

    def test_custom_fields_are_validated(self):
        CustomFieldDefinition.objects.create(org=self.org, entity=Entity.CONTACT, key="budget", label="Budget", field_type="number")
        bad = self.api.post("/api/v1/contacts/", {"email": "a@example.com", "custom_fields": {"budget": "lots", "nope": 1}}, format="json")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(set(bad.json()["custom_fields"]), {"budget", "nope"})
        good = self.api.post("/api/v1/contacts/", {"email": "b@example.com", "custom_fields": {"budget": "5,000"}}, format="json")
        self.assertEqual(good.json()["custom_fields"], {"budget": 5000})

    def test_upsert_matches_email_then_phone(self):
        existing = self.contact(email="a@example.com", phone="01711-000000")
        by_email = self.api.post("/api/v1/contacts/upsert/", {"email": "A@example.com", "job_title": "CEO"}, format="json")
        self.assertEqual((by_email.status_code, by_email.json()["id"]), (200, existing.pk))
        by_phone = self.api.post("/api/v1/contacts/upsert/", {"phone": "+8801711000000", "first_name": "Karim"}, format="json")
        self.assertEqual((by_phone.status_code, by_phone.json()["id"]), (200, existing.pk))
        new = self.api.post("/api/v1/contacts/upsert/", {"email": "new@example.com"}, format="json")
        self.assertEqual(new.status_code, 201)
        self.assertEqual(Contact.objects.for_org(self.org).count(), 2)

    def test_filters(self):
        self.contact(email="a@example.com", phone="01711000000", lifecycle_stage="customer")
        self.contact(email="b@example.com")
        self.assertEqual(self.api.get("/api/v1/contacts/?email=a@example.com").json()["count"], 1)
        self.assertEqual(self.api.get("/api/v1/contacts/?phone=8801711000000").json()["count"], 1)
        self.assertEqual(self.api.get("/api/v1/contacts/?lifecycle_stage=customer").json()["count"], 1)
        self.assertEqual(self.api.get("/api/v1/contacts/?updated_since=not-a-date").status_code, 400)


class DealApiTests(ApiTestCase):
    def test_create_defaults_to_first_stage_and_moves_through_services(self):
        pipeline = Pipeline.objects.for_org(self.org).get(is_default=True)
        first, second = pipeline.stages.order_by("position")[:2]
        contact = self.contact(email="a@example.com")
        response = self.api.post("/api/v1/deals/", {"title": "Website", "value": "50000", "contact": contact.pk}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        deal = Deal.objects.get(pk=response.json()["id"])
        self.assertEqual((deal.pipeline, deal.stage), (pipeline, first))
        contact.refresh_from_db()
        self.assertEqual(contact.lifecycle_stage, "opportunity")

        self.api.patch(f"/api/v1/deals/{deal.pk}/", {"stage": second.pk}, format="json")
        self.assertTrue(Activity.objects.filter(deal=deal, kind=ActivityKind.DEAL_STAGE_CHANGED).exists())

        won = self.api.post(f"/api/v1/deals/{deal.pk}/won/")
        self.assertEqual(won.json()["status"], DealStatus.WON)
        reason = LostReason.objects.create(org=self.org, name="Price")
        self.api.post(f"/api/v1/deals/{deal.pk}/reopen/")
        lost = self.api.post(f"/api/v1/deals/{deal.pk}/lost/", {"reason": "price", "note": "Too dear"}, format="json")
        self.assertEqual((lost.json()["status"], lost.json()["lost_reason"]), (DealStatus.LOST, "Price"))
        self.assertEqual(Deal.objects.get(pk=deal.pk).lost_reason, reason)

    def test_stage_must_belong_to_pipeline(self):
        default = Pipeline.objects.for_org(self.org).get(is_default=True)
        other = Pipeline.objects.create(org=self.org, name="Rentals")
        other_stage = other.stages.create(org=self.org, name="Ask", position=0)
        mismatch = self.api.post("/api/v1/deals/", {"title": "X", "pipeline": default.pk, "stage": other_stage.pk}, format="json")
        self.assertEqual(mismatch.status_code, 400)
        # A stage on its own implies its pipeline.
        implied = self.api.post("/api/v1/deals/", {"title": "Y", "stage": other_stage.pk}, format="json")
        self.assertEqual((implied.status_code, implied.json()["pipeline"]), (201, other.pk))


class TaskAndNoteApiTests(ApiTestCase):
    def test_task_complete_and_note(self):
        contact = self.contact(email="a@example.com")
        created = self.api.post("/api/v1/tasks/", {"title": "Call back", "contact": contact.pk, "due_at": "2026-11-01T10:00:00Z"},
                                format="json")
        self.assertEqual(created.status_code, 201, created.content)
        task = Task.objects.get(pk=created.json()["id"])
        self.assertEqual(task.assignee, self.manager.user)
        done = self.api.post(f"/api/v1/tasks/{task.pk}/complete/")
        self.assertTrue(done.json()["is_done"])
        self.assertEqual(self.api.get("/api/v1/tasks/?open=true").json()["count"], 0)

        note = self.api.post("/api/v1/notes/", {"body": "Met at the fair", "contact": contact.pk}, format="json")
        self.assertEqual(note.status_code, 201)
        self.assertTrue(Activity.objects.filter(contact=contact, kind=ActivityKind.NOTE_ADDED).exists())
        self.assertEqual(self.api.post("/api/v1/notes/", {"body": "Orphan"}, format="json").status_code, 400)

    def test_timeline_and_reference_lists(self):
        contact = self.contact(email="a@example.com")
        self.api.patch(f"/api/v1/contacts/{contact.pk}/", {"job_title": "Boss"}, format="json")
        timeline = self.api.get(f"/api/v1/activities/?contact={contact.pk}").json()
        self.assertEqual(timeline["results"][0]["kind"], ActivityKind.UPDATED)
        self.assertEqual(len(self.api.get("/api/v1/members/").json()["results"]), 3)
        self.assertTrue(self.api.get("/api/v1/pipelines/").json()["results"][0]["stages"])
