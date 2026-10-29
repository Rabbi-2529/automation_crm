"""What each action does. `perform()` returns an Outcome; raising ActionError fails the run with that message."""

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import timedelta

import requests
from django.conf import settings
from django.core.mail import send_mail
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from apps.activities.models import ActivityKind, Note
from apps.activities.services import record, record_changes, snapshot
from apps.contacts.models import LifecycleStage, Source, Tag
from apps.core.net import check_public_url
from apps.messaging.consent import can_message, next_send_time
from apps.messaging.models import Channel, ChannelSettings, MessageTemplate
from apps.messaging.rendering import build_context, render
from apps.messaging.services import compose
from apps.pipeline import services as pipeline_services
from apps.pipeline.models import Deal, Stage
from apps.tasks.models import Task
from apps.tasks.services import task_created

from .conditions import matches
from .registry import OWNER

UNITS = {"minutes": "minutes", "hours": "hours", "days": "days"}
WEBHOOK_TIMEOUT = 10


class ActionError(Exception):
    pass


@dataclass
class Outcome:
    status: str  # done | skipped | wait | defer (retry this same step later) | stop
    message: str = ""
    resume_at: object = None


def done(message=""):
    return Outcome("done", message)


def skipped(message):
    return Outcome("skipped", message)


# --- helpers -----------------------------------------------------------------------------------------------------------


def _owner(run):
    owner = (run.deal.owner if run.deal_id else None) or (run.contact.owner if run.contact_id else None)
    return owner or run.workflow.created_by


def _user(run, value):
    from django.contrib.auth import get_user_model

    if value == OWNER:
        return _owner(run)
    return get_user_model().objects.filter(pk=value, memberships__org=run.org, memberships__is_active=True).first()


def _values(run, user=None):
    return build_context(org=run.org, user=user or _owner(run), contact=run.contact, deal=run.deal)


def _target(run):
    """Records like tags/owner apply to the deal when the run has one, otherwise the contact."""
    return ("deal", run.deal) if run.deal_id else ("contact", run.contact)


def _delta(amount, unit):
    return timedelta(**{UNITS.get(unit, "days"): float(amount or 0)})


def _template(org, pk, channel):
    return MessageTemplate.objects.for_org(org).filter(pk=pk or 0, channel=channel).first()


def _check_public_url(url):
    """Blocks webhooks to localhost / private networks (SSRF)."""
    try:
        check_public_url(url)
    except ValueError as exc:
        raise ActionError(f"Webhook blocked: {exc}") from exc


def payload_for(run):
    contact, deal = run.contact, run.deal
    return {
        "workflow": {"id": run.workflow_id, "name": run.workflow.name},
        "run": run.pk, "trigger": run.trigger_summary, "sent_at": timezone.now().isoformat(),
        "contact": contact and {
            "id": contact.pk, "first_name": contact.first_name, "last_name": contact.last_name, "email": contact.email,
            "phone": contact.phone, "job_title": contact.job_title, "lifecycle_stage": contact.lifecycle_stage,
            "company": contact.company.name if contact.company_id else None,
            "tags": list(contact.tags.values_list("name", flat=True)), "custom_fields": contact.custom_fields,
        },
        "deal": deal and {
            "id": deal.pk, "title": deal.title, "value": deal.value, "currency": deal.currency_code, "status": deal.status,
            "stage": deal.stage.name, "pipeline": deal.pipeline.name, "expected_close_date": deal.expected_close_date,
        },
    }


# --- actions -----------------------------------------------------------------------------------------------------------


def _automated_send_check(run, channel):
    """Opt-outs skip the step; quiet hours defer it (the same step runs again when the window opens)."""
    contact = run.contact
    address = (contact.email if channel == Channel.EMAIL else contact.phone) if contact else ""
    if not contact or not address:
        return skipped(f"Contact has no {'email address' if channel == Channel.EMAIL else 'phone number'}")
    if not can_message(contact, channel):
        return skipped(f"Contact unsubscribed from {Channel(channel).label}")
    if (later := next_send_time(ChannelSettings.for_org(run.org))) is not None:
        return Outcome("defer", f"Outside sending hours; sends at {timezone.localtime(later):%d %b %H:%M}", resume_at=later)
    return None


def send_email(run, cfg, action):
    contact = run.contact
    if (blocked := _automated_send_check(run, Channel.EMAIL)) is not None:
        return blocked
    template = _template(run.org, cfg.get("template"), Channel.EMAIL)
    subject = cfg.get("subject") or (template.subject if template else "")
    body = cfg.get("body") or (template.body if template else "")
    if not body or not subject:
        raise ActionError("Email needs a subject and message (or a template)")
    sender = run.workflow.created_by if cfg.get("send_as") == "creator" else _owner(run)
    message = compose(org=run.org, user=sender, channel=Channel.EMAIL, to_address=contact.email, subject=subject, body=body,
                      contact=contact, deal=run.deal, template=template)
    return done(f"Email queued to {contact.email}: {message.subject}")


def send_sms(run, cfg, action):
    contact = run.contact
    if (blocked := _automated_send_check(run, Channel.SMS)) is not None:
        return blocked
    template = _template(run.org, cfg.get("template"), Channel.SMS)
    body = cfg.get("body") or (template.body if template else "")
    if not body:
        raise ActionError("SMS needs a message (or a template)")
    compose(org=run.org, user=_owner(run), channel=Channel.SMS, to_address=contact.phone, body=body, contact=contact,
            deal=run.deal, template=template)
    return done(f"SMS queued to {contact.phone}")


def send_whatsapp(run, cfg, action):
    contact = run.contact
    if (blocked := _automated_send_check(run, Channel.WHATSAPP)) is not None:
        return blocked
    template = _template(run.org, cfg.get("template"), Channel.WHATSAPP)
    body = cfg.get("body") or (template.body if template else "")
    if not body and not (template and template.whatsapp_template):
        raise ActionError("WhatsApp needs a message (or a template)")
    compose(org=run.org, user=_owner(run), channel=Channel.WHATSAPP, to_address=contact.phone, body=body or template.whatsapp_template,
            contact=contact, deal=run.deal, template=template)
    return done(f"WhatsApp queued to {contact.phone}")


def notify_user(run, cfg, action):
    user = _user(run, cfg["user"])
    if not user:
        return skipped("Nobody to notify (no owner)")
    text = render(cfg["message"], _values(run, user))
    link = ""
    if run.deal_id:
        link = f"{settings.SITE_URL.rstrip('/')}/deals/{run.deal_id}/"
    elif run.contact_id:
        link = f"{settings.SITE_URL.rstrip('/')}/contacts/{run.contact_id}/"
    send_mail(f"[{run.workflow.name}] {run.subject_label}", f"{text}\n\n{link}".strip(), None, [user.email])
    return done(f"Emailed {user.email}")


def create_task(run, cfg, action):
    assignee = _user(run, cfg.get("assignee", OWNER))
    values = _values(run, assignee)
    task = Task.objects.create(
        org=run.org, title=render(cfg["title"], values)[:200], kind=cfg.get("kind", "follow_up"),
        priority=cfg.get("priority", "normal"), due_at=timezone.now() + _delta(cfg.get("due_in", 1), cfg.get("due_unit")),
        assignee=assignee, contact=None if run.deal_id else run.contact, deal=run.deal,
    )
    task_created(task, actor=None)
    return done(f"Task “{task.title}” for {assignee.display_name if assignee else 'nobody'}")


def assign_owner(run, cfg, action):
    from django.contrib.auth import get_user_model

    if cfg.get("mode") == "round_robin":
        ids = cfg.get("users") or []
        if not ids:
            raise ActionError("Round robin needs at least one person")
        index = action.state.get("next", 0)
        type(action).objects.filter(pk=action.pk).update(state={**action.state, "next": index + 1})
        action.state["next"] = index + 1
        user = get_user_model().objects.filter(pk=ids[index % len(ids)], memberships__org=run.org, memberships__is_active=True).first()
    else:
        user = _user(run, cfg.get("user"))
    if not user:
        raise ActionError("That person is no longer an active member")
    kind, obj = _target(run)
    before = snapshot(obj, ["owner"])
    obj.owner = user
    obj.save(update_fields=["owner", "updated_at"])
    record_changes(instance=obj, before=before, actor=None, target_kw={kind: obj})
    return done(f"Owner set to {user.display_name}")


def set_lifecycle(run, cfg, action):
    contact = run.contact
    if not contact:
        return skipped("No contact")
    if contact.lifecycle_stage == cfg["stage"]:
        return skipped("Already in that stage")
    before = snapshot(contact, ["lifecycle_stage"])
    contact.lifecycle_stage = cfg["stage"]
    contact.save(update_fields=["lifecycle_stage", "updated_at"])
    record_changes(instance=contact, before=before, actor=None, target_kw={"contact": contact})
    return done(f"Lifecycle stage → {LifecycleStage(cfg['stage']).label}")


def update_field(run, cfg, action):
    contact = run.contact
    if not contact:
        return skipped("No contact")
    field, value = cfg["field"], render(cfg.get("value", ""), _values(run))
    if field == "source" and value not in Source.values:
        raise ActionError(f"'{value}' isn't a valid source ({', '.join(Source.values)})")
    before = snapshot(contact, [field])
    setattr(contact, field, value[: contact._meta.get_field(field).max_length])
    contact.save(update_fields=[field, "updated_at"])
    record_changes(instance=contact, before=before, actor=None, target_kw={"contact": contact})
    return done(f"{field} → {value or '(empty)'}")


def _change_tag(run, cfg, add):
    tag = Tag.objects.for_org(run.org).filter(pk=cfg["tag"]).first()
    if not tag:
        raise ActionError("Tag no longer exists")
    kind, obj = _target(run)
    has = obj.tags.filter(pk=tag.pk).exists()
    if has == add:
        return skipped(f"{'Already has' if add else 'Does not have'} “{tag.name}”")
    before = snapshot(obj, [])
    (obj.tags.add if add else obj.tags.remove)(tag)
    record_changes(instance=obj, before=before, actor=None, target_kw={kind: obj})
    return done(f"{'Added' if add else 'Removed'} tag “{tag.name}”")


def add_tag(run, cfg, action):
    return _change_tag(run, cfg, add=True)


def remove_tag(run, cfg, action):
    return _change_tag(run, cfg, add=False)


def add_note(run, cfg, action):
    kind, obj = _target(run)
    note = Note.objects.create(org=run.org, body=render(cfg["body"], _values(run)), **{kind: obj})
    record(org=run.org, kind=ActivityKind.NOTE_ADDED, note=note, summary="Note added by automation", **{kind: obj})
    return done("Note added")


def create_deal(run, cfg, action):
    if not run.contact:
        return skipped("No contact to create a deal for")
    stage = Stage.objects.for_org(run.org).select_related("pipeline").filter(pk=cfg["stage"]).first()
    if not stage:
        raise ActionError("Stage no longer exists")
    contact = run.contact
    deal = Deal.objects.create(
        org=run.org, pipeline=stage.pipeline, stage=stage, title=render(cfg.get("title") or "New deal", _values(run))[:200],
        value=cfg.get("value") or 0, currency=run.org.currency, contact=contact, company=contact.company, owner=contact.owner,
    )
    pipeline_services.deal_created(deal, actor=None)
    run.deal = deal
    run.save(update_fields=["deal", "updated_at"])
    return done(f"Created deal “{deal.title}”")


def move_deal(run, cfg, action):
    if not run.deal_id:
        return skipped("Run has no deal")
    stage = Stage.objects.for_org(run.org).filter(pk=cfg["stage"]).first()
    if not stage:
        raise ActionError("Stage no longer exists")
    if stage.pipeline_id != run.deal.pipeline_id:
        raise ActionError(f"“{stage.name}” is in a different pipeline than this deal")
    if run.deal.stage_id == stage.pk:
        return skipped(f"Already in {stage.name}")
    pipeline_services.move_deal(run.deal, stage, actor=None)
    return done(f"Moved to {stage.name}")


def webhook(run, cfg, action):
    url = cfg["url"]
    _check_public_url(url)
    body = json.dumps(payload_for(run), cls=DjangoJSONEncoder)
    signature = hmac.new(run.workflow.webhook_token.encode(), body.encode(), hashlib.sha256).hexdigest()
    try:
        response = requests.post(url, data=body, timeout=WEBHOOK_TIMEOUT, allow_redirects=False, headers={
            "Content-Type": "application/json", "User-Agent": "ThingBD-CRM-Automation/1", "X-ThingBD-Signature": f"sha256={signature}",
        })
    except requests.RequestException as exc:
        raise ActionError(f"Webhook failed: {exc}") from exc
    if response.status_code >= 300:
        raise ActionError(f"Webhook returned HTTP {response.status_code}")
    return done(f"Webhook delivered (HTTP {response.status_code})")


def _sequence(run, cfg):
    from apps.campaigns.models import Sequence

    sequence = Sequence.objects.for_org(run.org).filter(pk=cfg.get("sequence") or 0).first()
    if sequence is None:
        raise ActionError("Sequence no longer exists")
    return sequence


def enroll_sequence(run, cfg, action):
    from apps.campaigns import engine as campaigns

    if not run.contact:
        return skipped("No contact")
    sequence = _sequence(run, cfg)
    if campaigns.enroll(sequence, run.contact, note=f"Workflow “{run.workflow.name}”") is None:
        return skipped(f"Already in “{sequence.name}” (or it has no steps)")
    return done(f"Added to sequence “{sequence.name}”")


def remove_sequence(run, cfg, action):
    from apps.campaigns import engine as campaigns

    if not run.contact:
        return skipped("No contact")
    sequence = _sequence(run, cfg)
    if campaigns.remove(sequence, run.contact, reason=f"workflow “{run.workflow.name}”") is None:
        return skipped(f"Not active in “{sequence.name}”")
    return done(f"Removed from sequence “{sequence.name}”")


def adjust_score(run, cfg, action):
    from apps.leads.models import ScoreLog
    from apps.leads.scoring import recompute

    if not run.contact:
        return skipped("No contact")
    points = int(cfg.get("points") or 0)
    ScoreLog.objects.create(contact=run.contact, rule=None, points=points,
                            reason=(cfg.get("reason") or f"Workflow “{run.workflow.name}”")[:200])
    score = recompute(run.contact)
    return done(f"Score {points:+d} → {score}")


def wait(run, cfg, action):
    delta = _delta(cfg.get("amount", 1), cfg.get("unit"))
    return Outcome("wait", f"Waiting {cfg.get('amount')} {cfg.get('unit')}", resume_at=timezone.now() + delta)


def condition_gate(run, cfg, action):
    if run.contact_id:
        run.contact.refresh_from_db()
    if run.deal_id:
        run.deal.refresh_from_db()
    if matches(cfg.get("conditions"), run.contact, run.deal):
        return done("Conditions met")
    return Outcome("stop", "Conditions not met; run stopped")


HANDLERS = {
    "send_email": send_email, "send_sms": send_sms, "send_whatsapp": send_whatsapp, "notify_user": notify_user, "create_task": create_task,
    "assign_owner": assign_owner, "set_lifecycle": set_lifecycle, "update_field": update_field, "add_tag": add_tag,
    "remove_tag": remove_tag, "add_note": add_note, "create_deal": create_deal, "move_deal": move_deal,
    "webhook": webhook, "wait": wait, "condition_gate": condition_gate,
    "enroll_sequence": enroll_sequence, "remove_sequence": remove_sequence, "adjust_score": adjust_score,
}


def perform(action, run):
    handler = HANDLERS.get(action.action_type)
    if handler is None:
        raise ActionError(f"Unknown action {action.action_type}")
    return handler(run, action.config or {}, action)

