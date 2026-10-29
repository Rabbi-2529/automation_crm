"""Runs sequences (drip email/SMS per contact) and broadcasts (one message to a whole segment)."""

import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.activities.models import ActivityKind
from apps.activities.services import record
from apps.automation.conditions import matches
from apps.messaging.consent import can_message, next_send_time
from apps.messaging.models import Channel, ChannelSettings, Message, MessageStatus
from apps.messaging.services import compose

from .models import (
    Broadcast,
    BroadcastRecipient,
    BroadcastStatus,
    EnrollmentStatus,
    RecipientStatus,
    SendAs,
    SequenceDelivery,
    SequenceEnrollment,
)

logger = logging.getLogger(__name__)
SEQUENCE_BATCH = 300
BROADCAST_BATCH = 200  # per minute: gentle on SMTP servers and SMS gateways


def _delay(step):
    return timedelta(days=step.delay_days, hours=step.delay_hours)


def _sender(send_as, contact, fallback):
    return (contact.owner if send_as == SendAs.OWNER else None) or fallback or contact.owner


def _content(step_or_broadcast):
    template = step_or_broadcast.template
    subject = step_or_broadcast.subject or (template.subject if template else "")
    body = step_or_broadcast.body or (template.body if template else "")
    return subject, body


# --- Sequences ---------------------------------------------------------------------------------------------------------


def enroll(sequence, contact, by=None, note=""):
    """Adds the contact unless they're already active in this sequence. Returns the enrollment or None."""
    first = sequence.steps.order_by("position", "pk").first()
    if first is None:
        return None
    try:
        with transaction.atomic():
            enrollment = SequenceEnrollment.objects.create(
                org=sequence.org, sequence=sequence, contact=contact, enrolled_by=by, note=note[:255],
                next_run_at=timezone.now() + _delay(first),
            )
    except IntegrityError:
        return None  # already active
    record(org=sequence.org, kind=ActivityKind.SEQUENCE_ENROLLED, actor=by, contact=contact,
           summary=f"Added to sequence “{sequence.name}”", data={"sequence": sequence.pk})
    return enrollment


def finish(enrollment, status, reason=""):
    enrollment.status = status
    enrollment.finished_at = timezone.now()
    enrollment.next_run_at = None
    enrollment.save(update_fields=["status", "finished_at", "next_run_at", "updated_at"])
    label = EnrollmentStatus(status).label
    record(org=enrollment.org, kind=ActivityKind.SEQUENCE_FINISHED, contact=enrollment.contact,
           summary=f"Sequence “{enrollment.sequence.name}”: {label.lower()}" + (f" ({reason})" if reason else ""),
           data={"sequence": enrollment.sequence_id, "status": status})


def remove(sequence, contact, reason="removed"):
    active = SequenceEnrollment.objects.filter(sequence=sequence, contact=contact, status=EnrollmentStatus.ACTIVE).first()
    if active:
        finish(active, EnrollmentStatus.REMOVED, reason)
    return active


def run_enrollment(enrollment, now):
    sequence, contact = enrollment.sequence, enrollment.contact
    steps = list(sequence.steps.order_by("position", "pk"))
    if enrollment.current_step >= len(steps):
        return finish(enrollment, EnrollmentStatus.COMPLETED)
    if sequence.exit_conditions.get("rules") and matches(sequence.exit_conditions, contact=contact):
        return finish(enrollment, EnrollmentStatus.EXITED)
    step = steps[enrollment.current_step]
    channel_settings = ChannelSettings.for_org(enrollment.org)
    if (later := next_send_time(channel_settings, now)) is not None:
        enrollment.next_run_at = later
        enrollment.save(update_fields=["next_run_at", "updated_at"])
        return None

    subject, body = _content(step)
    if not can_message(contact, step.channel):
        reason = "opted out" if (contact.email_opt_out if step.channel == Channel.EMAIL else contact.sms_opt_out) else "no address"
        SequenceDelivery.objects.create(enrollment=enrollment, step=step, skipped_reason=reason)
    elif not body or (step.channel == Channel.EMAIL and not subject):
        SequenceDelivery.objects.create(enrollment=enrollment, step=step, skipped_reason="step has no content")
    else:
        message = compose(org=enrollment.org, user=_sender(sequence.send_as, contact, sequence.created_by), channel=step.channel,
                          to_address=contact.email if step.channel == Channel.EMAIL else contact.phone, subject=subject,
                          body=body, contact=contact, template=step.template, marketing=True)
        SequenceDelivery.objects.create(enrollment=enrollment, step=step, message=message)

    enrollment.current_step += 1
    if enrollment.current_step >= len(steps):
        enrollment.save(update_fields=["current_step", "updated_at"])
        return finish(enrollment, EnrollmentStatus.COMPLETED)
    enrollment.next_run_at = now + _delay(steps[enrollment.current_step])
    enrollment.save(update_fields=["current_step", "next_run_at", "updated_at"])
    return None


def process_sequences(now=None):
    now = now or timezone.now()
    due = SequenceEnrollment.objects.filter(status=EnrollmentStatus.ACTIVE, next_run_at__lte=now, sequence__is_active=True)
    done = 0
    for pk, next_run_at in list(due.values_list("pk", "next_run_at")[:SEQUENCE_BATCH]):
        # Claim the enrollment so a second worker can't send the same step.
        if not SequenceEnrollment.objects.filter(pk=pk, next_run_at=next_run_at).update(next_run_at=None):
            continue
        enrollment = SequenceEnrollment.objects.select_related("sequence__created_by", "contact__owner", "contact__org", "org").get(pk=pk)
        try:
            run_enrollment(enrollment, now)
        except Exception as exc:  # noqa: BLE001 - one bad step must not stall every other enrollment
            logger.exception("Sequence step failed for enrollment %s", pk)
            finish(enrollment, EnrollmentStatus.FAILED, f"{type(exc).__name__}: {exc}"[:120])
        done += 1
    return done


STOP_KINDS = {ActivityKind.EMAIL_RECEIVED: EnrollmentStatus.REPLIED, ActivityKind.SMS_RECEIVED: EnrollmentStatus.REPLIED,
              ActivityKind.UNSUBSCRIBED: EnrollmentStatus.UNSUBSCRIBED}


def dispatch(activity):
    """After-commit hook: replies and unsubscribes stop active sequences for that contact."""
    status = STOP_KINDS.get(activity.kind)
    if not status or not activity.contact_id:
        return
    active = SequenceEnrollment.objects.filter(contact_id=activity.contact_id, status=EnrollmentStatus.ACTIVE).select_related("sequence", "contact")
    if status == EnrollmentStatus.REPLIED:
        active = active.filter(sequence__exit_on_reply=True)
    for enrollment in active:
        finish(enrollment, status)


def sequence_stats(sequence):
    steps = []
    for step in sequence.steps.order_by("position", "pk"):
        messages = Message.objects.filter(pk__in=step.deliveries.exclude(message=None).values("message"))
        steps.append({
            "step": step, "sent": messages.filter(status=MessageStatus.SENT).count(),
            "failed": messages.filter(status=MessageStatus.FAILED).count(),
            "opened": messages.exclude(opened_at=None).count(), "clicked": messages.filter(click_count__gt=0).count(),
            "skipped": step.deliveries.filter(message=None).count(),
        })
    counts = dict(sequence.enrollments.values_list("status").annotate(n=Count("pk")))
    return steps, counts


# --- Broadcasts --------------------------------------------------------------------------------------------------------


def snapshot_recipients(broadcast):
    ids = broadcast.segment.contacts().values_list("pk", flat=True)
    BroadcastRecipient.objects.bulk_create(
        [BroadcastRecipient(broadcast=broadcast, contact_id=pk) for pk in ids], ignore_conflicts=True, batch_size=1000)


def process_broadcasts(now=None):
    now = now or timezone.now()
    for broadcast in Broadcast.objects.filter(status=BroadcastStatus.SCHEDULED, scheduled_at__lte=now):
        if Broadcast.objects.filter(pk=broadcast.pk, status=BroadcastStatus.SCHEDULED).update(status=BroadcastStatus.SENDING, started_at=now):
            snapshot_recipients(broadcast)

    sent = 0
    for broadcast in Broadcast.objects.filter(status=BroadcastStatus.SENDING).select_related("org", "created_by", "template"):
        if next_send_time(ChannelSettings.for_org(broadcast.org), now) is not None:
            continue  # quiet hours: pick up again when the window opens
        subject, body = _content(broadcast)
        pending = broadcast.recipients.filter(status=RecipientStatus.PENDING).select_related("contact__owner", "contact__org")
        batch = list(pending[:BROADCAST_BATCH])
        for recipient in batch:
            contact = recipient.contact
            if not can_message(contact, broadcast.channel):
                optout = contact.email_opt_out if broadcast.channel == Channel.EMAIL else contact.sms_opt_out
                recipient.status, recipient.skipped_reason = RecipientStatus.SKIPPED, "opted out" if optout else "no address"
                recipient.save(update_fields=["status", "skipped_reason"])
                continue
            recipient.message = compose(
                org=broadcast.org, user=_sender(broadcast.send_as, contact, broadcast.created_by), channel=broadcast.channel,
                to_address=contact.email if broadcast.channel == Channel.EMAIL else contact.phone, subject=subject, body=body,
                contact=contact, template=broadcast.template, marketing=True,
            )
            recipient.status = RecipientStatus.SENT
            recipient.save(update_fields=["status", "message"])
            sent += 1
        if not broadcast.recipients.filter(status=RecipientStatus.PENDING).exists():
            Broadcast.objects.filter(pk=broadcast.pk).update(status=BroadcastStatus.SENT, finished_at=timezone.now())
    return sent


def broadcast_stats(broadcast):
    recipients = broadcast.recipients
    messages = Message.objects.filter(pk__in=recipients.exclude(message=None).values("message"))
    total = recipients.count()
    delivered = messages.filter(status=MessageStatus.SENT).count()
    return {
        "total": total, "pending": recipients.filter(status=RecipientStatus.PENDING).count(),
        "skipped": recipients.filter(status=RecipientStatus.SKIPPED).count(),
        "sent": delivered, "failed": messages.filter(status=MessageStatus.FAILED).count(),
        "opened": messages.exclude(opened_at=None).count(), "clicked": messages.filter(click_count__gt=0).count(),
        "unsubscribed": recipients.filter(Q(contact__email_opt_out=True) if broadcast.channel == Channel.EMAIL
                                          else Q(contact__sms_opt_out=True), status=RecipientStatus.SENT).count(),
    }

