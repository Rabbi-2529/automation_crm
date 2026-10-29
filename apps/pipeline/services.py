"""Deal state changes. Views call these so every move/win/loss is logged the same way (and Phase 4 can hook in)."""

from decimal import Decimal

from django.db import transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.activities.models import ActivityKind
from apps.activities.services import record
from apps.contacts.models import LifecycleStage

from .models import Deal, DealStatus, LostReason, Pipeline, Stage

DEFAULT_STAGES = [
    # (name, probability %, flag after N idle days)
    ("New lead", 10, 7),
    ("Contacted", 20, 7),
    ("Proposal sent", 50, 10),
    ("Negotiation", 75, 10),
]
DEFAULT_LOST_REASONS = ["Price too high", "Chose a competitor", "No budget", "No response", "Not a fit"]

# Contacts move forward automatically as their deals progress; they never move backwards here.
EARLY_LIFECYCLE = {LifecycleStage.LEAD, LifecycleStage.QUALIFIED}


def create_defaults(org):
    if not Pipeline.objects.for_org(org).exists():
        pipeline = Pipeline.objects.create(org=org, name="Sales pipeline", is_default=True)
        Stage.objects.bulk_create(
            Stage(org=org, pipeline=pipeline, name=name, probability=probability, rot_days=rot, position=i)
            for i, (name, probability, rot) in enumerate(DEFAULT_STAGES)
        )
    if not LostReason.objects.for_org(org).exists():
        LostReason.objects.bulk_create(LostReason(org=org, name=name, position=i) for i, name in enumerate(DEFAULT_LOST_REASONS))


def create_defaults_for_new_org(sender, instance, created, **kwargs):
    if created and not kwargs.get("raw"):
        create_defaults(instance)


def default_pipeline(org):
    pipelines = Pipeline.objects.for_org(org)
    return pipelines.filter(is_default=True).first() or pipelines.first()


def _bump_contact(deal, to_stage, actor):
    contact = deal.contact
    if not contact:
        return
    if to_stage == LifecycleStage.OPPORTUNITY and contact.lifecycle_stage not in EARLY_LIFECYCLE:
        return
    if to_stage == LifecycleStage.CUSTOMER and contact.lifecycle_stage == LifecycleStage.CUSTOMER:
        return
    old = contact.lifecycle_stage
    contact.lifecycle_stage = to_stage
    contact.save(update_fields=["lifecycle_stage", "updated_at"])
    labels = dict(LifecycleStage.choices)
    record(org=deal.org, kind=ActivityKind.STAGE_CHANGED, actor=actor, contact=contact,
           summary=f"Lifecycle stage: {labels[old]} → {labels[to_stage]} (deal “{deal.title}”)",
           data={"from": old, "to": to_stage, "deal": deal.pk})


@transaction.atomic
def deal_created(deal, actor):
    record(org=deal.org, kind=ActivityKind.CREATED, actor=actor, deal=deal,
           summary=f"Deal “{deal.title}” created in {deal.stage.name}", data={"stage": deal.stage_id})
    _bump_contact(deal, LifecycleStage.OPPORTUNITY, actor)


@transaction.atomic
def move_deal(deal, stage, actor, ordered_ids=None):
    """Moves `deal` to `stage` (same pipeline) and optionally re-orders that column."""
    if stage.pipeline_id != deal.pipeline_id:
        raise ValueError("Stage belongs to another pipeline")
    old_stage = deal.stage
    if old_stage.pk != stage.pk:
        deal.stage = stage
        deal.stage_changed_at = timezone.now()
        deal.save(update_fields=["stage", "stage_changed_at", "updated_at"])
        record(org=deal.org, kind=ActivityKind.DEAL_STAGE_CHANGED, actor=actor, deal=deal,
               summary=f"“{deal.title}” moved: {old_stage.name} → {stage.name}",
               data={"from": old_stage.pk, "to": stage.pk})
    if ordered_ids:
        column = {d.pk: d for d in Deal.objects.filter(org=deal.org, stage=stage, pk__in=ordered_ids)}
        changed = []
        for position, pk in enumerate(ordered_ids):
            if pk in column and column[pk].position != position:
                column[pk].position = position
                changed.append(column[pk])
        Deal.objects.bulk_update(changed, ["position"])
    return deal


@transaction.atomic
def mark_won(deal, actor):
    deal.status = DealStatus.WON
    deal.closed_at = timezone.now()
    deal.lost_reason, deal.lost_note = None, ""
    deal.save(update_fields=["status", "closed_at", "lost_reason", "lost_note", "updated_at"])
    record(org=deal.org, kind=ActivityKind.DEAL_WON, actor=actor, deal=deal,
           summary=f"🎉 “{deal.title}” won ({deal.currency_code} {deal.value:,.0f})",
           data={"value": str(deal.value)})
    _bump_contact(deal, LifecycleStage.CUSTOMER, actor)


@transaction.atomic
def mark_lost(deal, actor, reason=None, note=""):
    deal.status = DealStatus.LOST
    deal.closed_at = timezone.now()
    deal.lost_reason, deal.lost_note = reason, note
    deal.save(update_fields=["status", "closed_at", "lost_reason", "lost_note", "updated_at"])
    because = f": {reason.name}" if reason else ""
    record(org=deal.org, kind=ActivityKind.DEAL_LOST, actor=actor, deal=deal,
           summary=f"“{deal.title}” lost{because}", data={"reason": reason.pk if reason else None, "note": note})


@transaction.atomic
def reopen(deal, actor):
    previous = deal.status
    deal.status = DealStatus.OPEN
    deal.closed_at = None
    deal.save(update_fields=["status", "closed_at", "updated_at"])
    record(org=deal.org, kind=ActivityKind.DEAL_REOPENED, actor=actor, deal=deal,
           summary=f"“{deal.title}” reopened (was {previous})", data={"from": previous})


def stage_totals(pipeline, deals=None):
    """{stage_id: {"count", "value", "weighted"}} for open deals."""
    deals = deals if deals is not None else Deal.objects.filter(pipeline=pipeline)
    money = DecimalField(max_digits=16, decimal_places=2)
    rows = (
        deals.filter(status=DealStatus.OPEN)
        .values("stage_id")
        # Annotation names must not shadow the `value` field, or F("value") below would point at the Sum.
        .annotate(
            n=Count("pk"),
            value_sum=Coalesce(Sum("value"), Value(Decimal("0")), output_field=money),
            weighted_sum=Coalesce(
                Sum(ExpressionWrapper(F("value") * F("stage__probability") / 100, output_field=money)),
                Value(Decimal("0")), output_field=money,
            ),
        )
        .order_by()
    )
    return {row["stage_id"]: {"count": row["n"], "value": row["value_sum"], "weighted": row["weighted_sum"]} for row in rows}
