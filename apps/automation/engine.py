"""Enrolls contacts/deals into workflows and runs their actions, including waits and scheduled triggers."""

import logging
from datetime import timedelta

from django.db.models import Exists, F, OuterRef, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.activities.models import Activity, ActivityKind
from apps.activities.services import record
from apps.contacts.models import Contact
from apps.pipeline.models import Deal, DealStatus

from . import actions, context
from .conditions import matches
from .events import trigger_type
from .models import RunStatus, StepStatus, Workflow, WorkflowRun, WorkflowStepLog

logger = logging.getLogger(__name__)

MAX_DEPTH = 3  # workflow A triggers B triggers C triggers D, then we stop
RESUME_BATCH = 500
SCAN_BATCH = 500


# --- Matching events ---------------------------------------------------------------------------------------------------


def trigger_config_matches(workflow, activity):
    cfg, data, kind = workflow.trigger_config or {}, activity.data or {}, workflow.trigger_type
    if kind == "contact_created" and activity.kind == ActivityKind.IMPORTED:
        return cfg.get("include_imports", True) and data.get("created", False)
    if kind == "contact_updated" and cfg.get("field"):
        return cfg["field"] in (data.get("changes") or {})
    if kind == "lifecycle_changed" and cfg.get("to_stage"):
        return data.get("to") == cfg["to_stage"]
    if kind == "tag_added" and cfg.get("tag"):
        return str(data.get("tag")) == str(cfg["tag"])
    if kind == "deal_created" and cfg.get("pipeline"):
        return activity.deal is not None and str(activity.deal.pipeline_id) == str(cfg["pipeline"])
    if kind == "deal_stage_changed" and cfg.get("stage"):
        return str(data.get("to")) == str(cfg["stage"])
    if kind == "form_submitted" and cfg.get("form"):
        return str(data.get("form")) == str(cfg["form"])
    if kind == "facebook_lead" and cfg.get("page"):
        return str(data.get("page")) == str(cfg["page"])
    if kind == "lead_grade_changed" and cfg.get("to_grade"):
        return data.get("to") == cfg["to_grade"]
    return True


def handle_activity(activity):
    """Starts runs for every active workflow this activity triggers."""
    kind = trigger_type(activity)
    if not kind:
        return []
    marker = (activity.data or {}).get("_automation") or {}
    depth = marker.get("depth", -1) + 1 if marker else 0
    if depth > MAX_DEPTH:
        logger.warning("Automation depth limit hit for activity %s", activity.pk)
        return []
    deal = activity.deal
    contact = activity.contact or (deal.contact if deal else None)
    runs = []
    for workflow in Workflow.objects.filter(org_id=activity.org_id, is_active=True, trigger_type=kind).select_related("org", "created_by"):
        if marker.get("workflow") == workflow.pk:
            continue  # a workflow never re-triggers itself
        if not trigger_config_matches(workflow, activity):
            continue
        run = enroll(workflow, contact=contact, deal=deal if kind.startswith("deal_") or kind == "task_completed" else None,
                     activity=activity, depth=depth, summary=activity.summary)
        if run:
            runs.append(run)
    return runs


def already_enrolled(workflow, contact=None, deal=None):
    runs = WorkflowRun.objects.filter(workflow=workflow)
    return runs.filter(deal=deal).exists() if deal else runs.filter(contact=contact, deal__isnull=True).exists()


def enroll(workflow, *, contact=None, deal=None, activity=None, depth=0, summary="", force=False):
    """Creates and executes a run. Returns None when re-enrollment or conditions keep the record out."""
    if contact is None and deal is None:
        return None
    if not force and not workflow.allow_reenroll and already_enrolled(workflow, contact, deal):
        return None
    if not matches(workflow.conditions, contact, deal):
        return None
    run = WorkflowRun.objects.create(org=workflow.org, workflow=workflow, contact=contact, deal=deal, trigger_activity=activity,
                                     trigger_summary=summary[:255], depth=depth)
    Workflow.objects.filter(pk=workflow.pk).update(run_count=F("run_count") + 1, last_run_at=timezone.now())
    with context.running(run):
        record(org=run.org, kind=ActivityKind.AUTOMATION, contact=None if deal else contact, deal=deal,
               summary=f"Entered workflow “{workflow.name}”", data={"run": run.pk})
    execute(run)
    return run


# --- Running -----------------------------------------------------------------------------------------------------------


def _log(run, position, action_type, status, message=""):
    WorkflowStepLog.objects.create(run=run, position=position, action_type=action_type, status=status, message=message[:500])


def _finish(run, status, error=""):
    run.status = status
    run.error = error[:2000]
    run.finished_at = timezone.now()
    run.resume_at = None
    run.save(update_fields=["status", "error", "finished_at", "resume_at", "current_step", "updated_at"])


def execute(run):
    """Runs actions from `run.current_step` until the end, a wait, a stop or a failure."""
    steps = list(run.workflow.actions.order_by("position", "pk"))
    run.status = RunStatus.RUNNING
    with context.running(run):
        while run.current_step < len(steps):
            action = steps[run.current_step]
            position = run.current_step + 1
            try:
                outcome = actions.perform(action, run)
            except actions.ActionError as exc:
                _log(run, position, action.action_type, StepStatus.FAILED, str(exc))
                _finish(run, RunStatus.FAILED, str(exc))
                return run
            except Exception as exc:  # noqa: BLE001 - a broken step must not crash the worker or other runs
                logger.exception("Workflow step crashed (run %s, step %s)", run.pk, position)
                _log(run, position, action.action_type, StepStatus.FAILED, f"{type(exc).__name__}: {exc}")
                _finish(run, RunStatus.FAILED, f"{type(exc).__name__}: {exc}")
                return run

            if outcome.status == "defer":  # e.g. quiet hours: retry this same step later
                _log(run, position, action.action_type, StepStatus.WAITING, outcome.message)
                run.status, run.resume_at = RunStatus.WAITING, outcome.resume_at
                run.save(update_fields=["status", "resume_at", "updated_at"])
                return run
            run.current_step += 1
            if outcome.status == "wait":
                _log(run, position, action.action_type, StepStatus.WAITING, outcome.message)
                run.status, run.resume_at = RunStatus.WAITING, outcome.resume_at
                run.save(update_fields=["status", "resume_at", "current_step", "updated_at"])
                return run
            if outcome.status == "stop":
                _log(run, position, action.action_type, StepStatus.STOPPED, outcome.message)
                _finish(run, RunStatus.STOPPED)
                return run
            _log(run, position, action.action_type, StepStatus.SKIPPED if outcome.status == "skipped" else StepStatus.DONE,
                 outcome.message)
            run.save(update_fields=["current_step", "updated_at"])
    _finish(run, RunStatus.COMPLETED)
    return run


def resume_due_runs(now=None):
    """Continues runs whose wait is over. Each run is claimed atomically so two workers can't double-run it."""
    now = now or timezone.now()
    resumed = 0
    due = WorkflowRun.objects.filter(status=RunStatus.WAITING, resume_at__lte=now, workflow__is_active=True).values_list("pk", flat=True)
    for pk in list(due[:RESUME_BATCH]):
        if not WorkflowRun.objects.filter(pk=pk, status=RunStatus.WAITING).update(status=RunStatus.RUNNING):
            continue
        run = WorkflowRun.objects.select_related("workflow__org", "workflow__created_by", "contact", "deal", "org").get(pk=pk)
        if run.contact_id:
            run.contact.refresh_from_db()
        execute(run)
        resumed += 1
    return resumed


def cancel_open_runs(workflow):
    return WorkflowRun.objects.filter(workflow=workflow, status__in=[RunStatus.RUNNING, RunStatus.WAITING]).update(
        status=RunStatus.CANCELLED, finished_at=timezone.now(), resume_at=None)


# --- Scheduled triggers ------------------------------------------------------------------------------------------------


def scan_scheduled(now=None):
    """Enrolls records for time-based triggers. Run every 15 minutes by Celery beat."""
    now = now or timezone.now()
    enrolled = 0
    for workflow in Workflow.objects.filter(is_active=True, trigger_type="contact_inactive", org__is_active=True).select_related("org", "created_by"):
        days = int((workflow.trigger_config or {}).get("days", 14))
        cutoff = now - timedelta(days=days)
        # "Quiet period" = since the contact's last activity. One run per quiet period; new activity resets it.
        later_run = WorkflowRun.objects.filter(workflow=workflow, contact=OuterRef("pk"), created_at__gte=OuterRef("last_touch"))
        contacts = (
            Contact.objects.filter(org=workflow.org)
            .annotate(last_touch=Coalesce("last_activity_at", "created_at"))
            .filter(last_touch__lt=cutoff)
            .exclude(Exists(later_run))
            .select_related("company", "owner")[:SCAN_BATCH]
        )
        for contact in contacts:
            if enroll(workflow, contact=contact, summary=f"No activity for {days}+ days"):
                enrolled += 1

    today = timezone.localdate(now)
    for workflow in Workflow.objects.filter(is_active=True, trigger_type="deal_close_soon", org__is_active=True).select_related("org", "created_by"):
        days = int((workflow.trigger_config or {}).get("days", 3))
        target = today + timedelta(days=days)
        ran_today = WorkflowRun.objects.filter(workflow=workflow, deal=OuterRef("pk"), created_at__date=today)
        deals = (Deal.objects.filter(org=workflow.org, status=DealStatus.OPEN, expected_close_date=target)
                 .exclude(Exists(ran_today)).select_related("contact", "stage", "pipeline", "org")[:SCAN_BATCH])
        for deal in deals:
            if enroll(workflow, contact=deal.contact, deal=deal, summary=f"Expected to close in {days} day(s)"):
                enrolled += 1

    enrolled += _scan_not_contacted(now) + _scan_date_fields(now, today) + _scan_idle_deals(now)
    return enrolled


def _active(trigger_type):
    return Workflow.objects.filter(is_active=True, trigger_type=trigger_type, org__is_active=True).select_related("org", "created_by")


def _scan_not_contacted(now):
    """Speed to lead: new contacts with no outgoing email/SMS and no logged call after N hours."""
    from apps.messaging.models import CallLog, Direction, Message

    enrolled = 0
    for workflow in _active("lead_not_contacted"):
        hours = float((workflow.trigger_config or {}).get("hours", 2))
        reached = Q(Exists(Message.objects.filter(contact=OuterRef("pk"), direction=Direction.OUTBOUND))) | Q(
            Exists(CallLog.objects.filter(contact=OuterRef("pk"))))
        contacts = (Contact.objects.filter(org=workflow.org, created_at__lte=now - timedelta(hours=hours),
                                           created_at__gte=now - timedelta(days=30))
                    .exclude(reached).exclude(Exists(WorkflowRun.objects.filter(workflow=workflow, contact=OuterRef("pk"))))
                    .select_related("company", "owner")[:SCAN_BATCH])
        for contact in contacts:
            if enroll(workflow, contact=contact, summary=f"Not contacted within {hours:g} hours", force=True):
                enrolled += 1
    return enrolled


def _scan_date_fields(now, today):
    """Birthdays, renewals…: a contact date custom field equal to today + N days (optionally every year)."""
    from datetime import date

    enrolled = 0
    for workflow in _active("date_field"):
        cfg = workflow.trigger_config or {}
        key, target = cfg.get("field"), today + timedelta(days=int(cfg.get("days_before", 0)))
        if not key:
            continue
        ran_today = WorkflowRun.objects.filter(workflow=workflow, contact=OuterRef("pk"), created_at__date=today)
        candidates = (Contact.objects.filter(org=workflow.org, custom_fields__has_key=key).exclude(Exists(ran_today))
                      .select_related("company", "owner"))
        for contact in candidates.iterator(chunk_size=500):
            try:
                value = date.fromisoformat(str(contact.custom_fields.get(key, ""))[:10])
            except ValueError:
                continue
            hit = (value.month, value.day) == (target.month, target.day) if cfg.get("yearly", True) else value == target
            if hit and enroll(workflow, contact=contact, summary=f"{key} on {value:%d %b}", force=True):
                enrolled += 1
    return enrolled


def _scan_idle_deals(now):
    enrolled = 0
    for workflow in _active("deal_idle"):
        days = int((workflow.trigger_config or {}).get("days", 7))
        later_run = WorkflowRun.objects.filter(workflow=workflow, deal=OuterRef("pk"), created_at__gte=OuterRef("last_touch"))
        deals = (Deal.objects.filter(org=workflow.org, status=DealStatus.OPEN)
                 .annotate(last_touch=Coalesce("last_activity_at", "stage_changed_at"))
                 .filter(last_touch__lt=now - timedelta(days=days)).exclude(Exists(later_run))
                 .select_related("contact", "stage", "pipeline", "org")[:SCAN_BATCH])
        for deal in deals:
            if enroll(workflow, contact=deal.contact, deal=deal, summary=f"No activity for {days}+ days", force=workflow.allow_reenroll):
                enrolled += 1
    return enrolled


def process_activity_id(activity_id):
    activity = Activity.objects.select_related("org", "contact", "deal__contact", "deal__pipeline").filter(pk=activity_id).first()
    return handle_activity(activity) if activity else []

