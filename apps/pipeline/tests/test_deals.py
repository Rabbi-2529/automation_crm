import json
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from apps.activities.models import Activity, ActivityKind
from apps.contacts.models import CustomFieldDefinition, Entity, FieldType, LifecycleStage
from apps.contacts.tests.base import CrmTestCase
from apps.core.testing import make_org

from ..models import Deal, DealStatus, LostReason, Pipeline, Stage
from ..services import default_pipeline


class PipelineTestCase(CrmTestCase):
    def setUp(self):
        super().setUp()
        self.pipeline = default_pipeline(self.org)
        self.stages = list(self.pipeline.stages.all())
        self.new, self.contacted, self.proposal, self.negotiation = self.stages

    def deal(self, title="Website redesign", stage=None, **fields):
        stage = stage or self.new
        return Deal.objects.create(org=stage.org, pipeline=stage.pipeline, stage=stage, title=title, **fields)


class DefaultsTests(CrmTestCase):
    def test_new_org_gets_default_pipeline_and_lost_reasons(self):
        org = make_org("Fresh")
        pipeline = Pipeline.objects.get(org=org)
        self.assertTrue(pipeline.is_default)
        self.assertEqual([s.name for s in pipeline.stages.all()], ["New lead", "Contacted", "Proposal sent", "Negotiation"])
        self.assertEqual([s.probability for s in pipeline.stages.all()], [10, 20, 50, 75])
        self.assertEqual(LostReason.objects.filter(org=org).count(), 5)
        self.assertTrue(all(s.org == org for s in pipeline.stages.all()))


class DealCrudTests(PipelineTestCase):
    def test_create_deal_from_contact_bumps_lead_to_opportunity(self):
        company = self.company("Daraz")
        contact = self.contact(first_name="Rahim", company=company, lifecycle_stage=LifecycleStage.LEAD)
        self.login(self.agent)
        page = self.client.get(reverse("pipeline:deal_create"), {"contact": contact.pk})
        self.assertEqual(page.context["form"].initial["title"], "Daraz deal")
        self.assertContains(page, "Rahim")  # lookup widget shows the picked contact's name

        response = self.client.post(reverse("pipeline:deal_create"), {
            "title": "Daraz CRM rollout", "value": "250000", "stage": self.proposal.pk, "contact": contact.pk,
            "owner": self.agent.user.pk,
        })
        deal = Deal.objects.get()
        self.assertRedirects(response, reverse("pipeline:deal_detail", args=[deal.pk]))
        self.assertEqual((deal.pipeline, deal.stage, deal.value), (self.pipeline, self.proposal, Decimal("250000")))
        self.assertEqual(deal.company, company)  # filled from the contact
        self.assertEqual(deal.currency, "BDT")
        contact.refresh_from_db()
        self.assertEqual(contact.lifecycle_stage, LifecycleStage.OPPORTUNITY)
        self.assertTrue(deal.activities.filter(kind=ActivityKind.CREATED).exists())
        # The contact's timeline also shows the deal's events.
        contact_page = self.client.get(reverse("contacts:contact_detail", args=[contact.pk]))
        self.assertContains(contact_page, "Daraz CRM rollout")

    def test_customer_contact_is_not_downgraded_by_new_deal(self):
        contact = self.contact(first_name="Rahim", lifecycle_stage=LifecycleStage.CUSTOMER)
        self.login(self.agent)
        self.client.post(reverse("pipeline:deal_create"), {"title": "Upsell", "value": "1", "stage": self.new.pk, "contact": contact.pk})
        contact.refresh_from_db()
        self.assertEqual(contact.lifecycle_stage, LifecycleStage.CUSTOMER)

    def test_cannot_use_other_orgs_stage_contact_or_company(self):
        foreign_stage = default_pipeline(self.other_org).stages.first()
        foreign_contact = self.contact(org=self.other_org, first_name="Secret")
        foreign_company = self.company(org=self.other_org)
        self.login(self.agent)
        for field, value in (("stage", foreign_stage.pk), ("contact", foreign_contact.pk), ("company", foreign_company.pk)):
            with self.subTest(field=field):
                data = {"title": "X", "value": "1", "stage": self.new.pk, field: value}
                response = self.client.post(reverse("pipeline:deal_create"), data)
                self.assertIn(field, response.context["form"].errors)
        self.assertFalse(Deal.objects.exists())

    def test_negative_value_rejected(self):
        self.login(self.agent)
        response = self.client.post(reverse("pipeline:deal_create"), {"title": "X", "value": "-5", "stage": self.new.pk})
        self.assertContains(response, "can&#x27;t be negative")

    def test_edit_logs_changes_and_stage_move_separately(self):
        deal = self.deal(value=100)
        self.login(self.agent)
        self.client.post(reverse("pipeline:deal_edit", args=[deal.pk]), {
            "title": deal.title, "value": "500", "stage": self.negotiation.pk, "owner": self.manager.user.pk,
        })
        kinds = list(deal.activities.values_list("kind", flat=True))
        self.assertIn(ActivityKind.UPDATED, kinds)
        self.assertIn(ActivityKind.OWNER_CHANGED, kinds)
        self.assertIn(ActivityKind.DEAL_STAGE_CHANGED, kinds)
        moved = deal.activities.get(kind=ActivityKind.DEAL_STAGE_CHANGED)
        self.assertEqual(moved.data, {"from": self.new.pk, "to": self.negotiation.pk})

    def test_edit_into_another_pipeline_moves_pipeline(self):
        other = Pipeline.objects.create(org=self.org, name="Renewals")
        renewal_stage = Stage.objects.create(org=self.org, pipeline=other, name="Due")
        deal = self.deal()
        self.login(self.agent)
        self.client.post(reverse("pipeline:deal_edit", args=[deal.pk]), {"title": deal.title, "value": "0", "stage": renewal_stage.pk})
        deal.refresh_from_db()
        self.assertEqual((deal.pipeline, deal.stage), (other, renewal_stage))

    def test_deal_custom_fields(self):
        CustomFieldDefinition.objects.create(org=self.org, entity=Entity.DEAL, key="contract_months", label="Contract months",
                                             field_type=FieldType.NUMBER)
        self.login(self.agent)
        self.client.post(reverse("pipeline:deal_create"), {"title": "X", "value": "1", "stage": self.new.pk, "cf_contract_months": "12"})
        self.assertEqual(Deal.objects.get().custom_fields, {"contract_months": 12})

    def test_isolation_and_delete_permission(self):
        foreign = Deal.objects.create(org=self.other_org, pipeline=default_pipeline(self.other_org),
                                      stage=default_pipeline(self.other_org).stages.first(), title="Secret deal")
        mine = self.deal()
        self.login(self.agent)
        for name in ("pipeline:deal_detail", "pipeline:deal_edit", "pipeline:deal_lost"):
            self.assertEqual(self.client.get(reverse(name, args=[foreign.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("pipeline:deal_won", args=[foreign.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("pipeline:deal_board")), "Secret deal")
        self.assertEqual(self.client.post(reverse("pipeline:deal_delete", args=[mine.pk])).status_code, 403)
        self.login(self.manager)
        self.client.post(reverse("pipeline:deal_delete", args=[mine.pk]))
        self.assertFalse(Deal.objects.filter(pk=mine.pk).exists())


class BoardTests(PipelineTestCase):
    def move(self, deal, stage, order=None):
        return self.client.post(reverse("pipeline:deal_move", args=[deal.pk]),
                                json.dumps({"stage": stage.pk, "order": order or [deal.pk]}), content_type="application/json")

    def test_board_columns_and_totals(self):
        self.deal("A", value=1000)
        self.deal("B", value=3000, stage=self.proposal)
        self.deal("Won one", value=9999, stage=self.proposal, status=DealStatus.WON)
        self.login(self.agent)
        response = self.client.get(reverse("pipeline:deal_board"))
        columns = {c["stage"].name: c for c in response.context["columns"]}
        self.assertEqual([d.title for d in columns["New lead"]["deals"]], ["A"])
        self.assertEqual(columns["Proposal sent"]["totals"]["value"], Decimal("3000"))
        self.assertEqual(response.context["open_total"], Decimal("4000"))
        self.assertEqual(response.context["weighted_total"], Decimal("1600"))  # 1000*10% + 3000*50%
        self.assertNotContains(response, "Won one")

    def test_board_owner_filter(self):
        self.deal("Mine", owner=self.agent.user)
        self.deal("Theirs", owner=self.manager.user)
        self.login(self.agent)
        response = self.client.get(reverse("pipeline:deal_board"), {"owner": "me"})
        titles = [d.title for c in response.context["columns"] for d in c["deals"]]
        self.assertEqual(titles, ["Mine"])

    def test_drag_to_new_stage_logs_and_reorders(self):
        a, b = self.deal("A", stage=self.contacted), self.deal("B", stage=self.contacted)
        moving = self.deal("Moving")
        self.login(self.agent)
        response = self.move(moving, self.contacted, order=[b.pk, moving.pk, a.pk])
        self.assertEqual(response.json(), {"ok": True, "stage": self.contacted.pk})
        moving.refresh_from_db()
        self.assertEqual(moving.stage, self.contacted)
        self.assertEqual(list(Deal.objects.filter(stage=self.contacted).order_by("position").values_list("title", flat=True)),
                         ["B", "Moving", "A"])
        self.assertEqual(moving.activities.get().kind, ActivityKind.DEAL_STAGE_CHANGED)

    def test_reorder_within_column_logs_nothing(self):
        a, b = self.deal("A"), self.deal("B")
        self.login(self.agent)
        self.move(a, self.new, order=[b.pk, a.pk])
        self.assertFalse(Activity.objects.exists())

    def test_move_rejects_other_pipeline_stage_and_closed_deals(self):
        other_stage = Stage.objects.create(org=self.org, pipeline=Pipeline.objects.create(org=self.org, name="Other"), name="X")
        deal = self.deal()
        self.login(self.agent)
        self.assertEqual(self.move(deal, other_stage).status_code, 400)
        foreign_stage = default_pipeline(self.other_org).stages.first()
        self.assertEqual(self.move(deal, foreign_stage).status_code, 400)
        deal.status = DealStatus.WON
        deal.save()
        self.assertEqual(self.move(deal, self.proposal).status_code, 400)

    def test_order_ignores_foreign_ids(self):
        foreign = Deal.objects.create(org=self.other_org, pipeline=default_pipeline(self.other_org),
                                      stage=default_pipeline(self.other_org).stages.first(), title="Secret", position=7)
        deal = self.deal()
        self.login(self.agent)
        self.move(deal, self.new, order=[foreign.pk, deal.pk])
        foreign.refresh_from_db()
        self.assertEqual(foreign.position, 7)

    def test_stage_bar_form_post_redirects(self):
        deal = self.deal()
        self.login(self.agent)
        response = self.client.post(reverse("pipeline:deal_move", args=[deal.pk]), {"stage": self.proposal.pk})
        self.assertRedirects(response, reverse("pipeline:deal_detail", args=[deal.pk]))
        deal.refresh_from_db()
        self.assertEqual(deal.stage, self.proposal)

    def test_rotting_flag(self):
        deal = self.deal()
        self.assertFalse(deal.is_rotting)
        Deal.objects.filter(pk=deal.pk).update(stage_changed_at=timezone.now() - timedelta(days=8))
        deal.refresh_from_db()
        self.assertTrue(deal.is_rotting)  # "New lead" flags after 7 idle days


class WinLossTests(PipelineTestCase):
    def test_won_makes_contact_customer(self):
        contact = self.contact(first_name="Rahim", lifecycle_stage=LifecycleStage.OPPORTUNITY)
        deal = self.deal(contact=contact, value=5000)
        self.login(self.agent)
        self.client.post(reverse("pipeline:deal_won", args=[deal.pk]))
        deal.refresh_from_db()
        contact.refresh_from_db()
        self.assertEqual(deal.status, DealStatus.WON)
        self.assertIsNotNone(deal.closed_at)
        self.assertEqual(contact.lifecycle_stage, LifecycleStage.CUSTOMER)
        self.assertTrue(deal.activities.filter(kind=ActivityKind.DEAL_WON).exists())

    def test_lost_requires_reason_and_reopen_clears_close(self):
        deal = self.deal()
        reason = LostReason.objects.for_org(self.org).first()
        self.login(self.agent)
        response = self.client.post(reverse("pipeline:deal_lost", args=[deal.pk]), {"note": "silence"})
        self.assertIn("reason", response.context["form"].errors)
        self.client.post(reverse("pipeline:deal_lost", args=[deal.pk]), {"reason": reason.pk, "note": "Went with Zoho"})
        deal.refresh_from_db()
        self.assertEqual((deal.status, deal.lost_reason, deal.lost_note), (DealStatus.LOST, reason, "Went with Zoho"))
        self.client.post(reverse("pipeline:deal_reopen", args=[deal.pk]))
        deal.refresh_from_db()
        self.assertEqual(deal.status, DealStatus.OPEN)
        self.assertIsNone(deal.closed_at)
        kinds = list(deal.activities.values_list("kind", flat=True))
        self.assertIn(ActivityKind.DEAL_LOST, kinds)
        self.assertIn(ActivityKind.DEAL_REOPENED, kinds)

    def test_foreign_lost_reason_rejected(self):
        deal = self.deal()
        foreign_reason = LostReason.objects.for_org(self.other_org).first()
        self.login(self.agent)
        response = self.client.post(reverse("pipeline:deal_lost", args=[deal.pk]), {"reason": foreign_reason.pk})
        self.assertIn("reason", response.context["form"].errors)

    def test_won_this_month_on_dashboard(self):
        deal = self.deal(value=7500)
        self.login(self.agent)
        self.client.post(reverse("pipeline:deal_won", args=[deal.pk]))
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.context["won_month_value"], Decimal("7500"))
        self.assertEqual(response.context["won_month_count"], 1)


class PipelineSettingsTests(PipelineTestCase):
    def formset_data(self, pipeline, rows, **extra):
        data = {"name": pipeline.name if pipeline else "New", "position": 0, "stages-TOTAL_FORMS": len(rows),
                "stages-INITIAL_FORMS": sum(1 for r in rows if r.get("id")), "stages-MIN_NUM_FORMS": 0,
                "stages-MAX_NUM_FORMS": 1000, **extra}
        for i, row in enumerate(rows):
            for key, value in row.items():
                data[f"stages-{i}-{key}"] = value
        return data

    def existing_rows(self):
        return [{"id": s.pk, "name": s.name, "probability": s.probability, "rot_days": s.rot_days or "", "position": s.position}
                for s in self.stages]

    def test_agents_cannot_manage_pipelines(self):
        self.login(self.agent)
        self.assertEqual(self.client.get(reverse("pipeline:pipeline_list")).status_code, 403)

    def test_add_rename_and_delete_empty_stage(self):
        rows = self.existing_rows()
        rows[0]["name"] = "Fresh lead"
        rows[1]["DELETE"] = "on"
        rows.append({"name": "Contract sent", "probability": 90, "position": 9})
        self.login(self.manager)
        response = self.client.post(reverse("pipeline:pipeline_edit", args=[self.pipeline.pk]), self.formset_data(self.pipeline, rows, is_default="on"))
        self.assertRedirects(response, reverse("pipeline:pipeline_list"))
        names = list(self.pipeline.stages.values_list("name", flat=True))
        self.assertEqual(names, ["Fresh lead", "Proposal sent", "Negotiation", "Contract sent"])
        self.assertEqual(self.pipeline.stages.get(name="Contract sent").org, self.org)

    def test_cannot_delete_stage_with_deals(self):
        self.deal(stage=self.contacted)
        rows = self.existing_rows()
        rows[1]["DELETE"] = "on"
        self.login(self.manager)
        response = self.client.post(reverse("pipeline:pipeline_edit", args=[self.pipeline.pk]), self.formset_data(self.pipeline, rows))
        self.assertContains(response, "still has 1 deal")
        self.assertTrue(Stage.objects.filter(pk=self.contacted.pk).exists())

    def test_new_default_pipeline_unsets_old_default(self):
        self.login(self.manager)
        self.client.post(reverse("pipeline:pipeline_create"),
                         self.formset_data(None, [{"name": "Only stage", "probability": 50, "position": 0}], name="Renewals", is_default="on"))
        self.assertEqual(Pipeline.objects.get(org=self.org, is_default=True).name, "Renewals")

    def test_pipeline_with_deals_cannot_be_deleted(self):
        self.deal()
        Pipeline.objects.create(org=self.org, name="Spare")
        self.login(self.manager)
        self.client.post(reverse("pipeline:pipeline_delete", args=[self.pipeline.pk]))
        self.assertTrue(Pipeline.objects.filter(pk=self.pipeline.pk).exists())


class DealNotesAndFilesTests(PipelineTestCase):
    def test_note_on_deal(self):
        deal = self.deal()
        self.login(self.agent)
        response = self.client.post(reverse("activities:note_create", args=["deal", deal.pk]), {"body": "Sent the quote"})
        self.assertRedirects(response, reverse("pipeline:deal_detail", args=[deal.pk]))
        self.assertEqual(deal.notes.get().body, "Sent the quote")
