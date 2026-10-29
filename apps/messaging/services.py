"""Compose, deliver and receive messages. Every outcome is written to the timeline through activities.record()."""

import re
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr

from django.db import transaction
from django.utils import timezone
from django.utils.html import strip_tags

from apps.activities.models import ActivityKind
from apps.activities.services import record
from apps.contacts.models import Contact

from . import email as email_backend
from .models import (
    Channel,
    ChannelSettings,
    Conversation,
    Direction,
    Message,
    MessageStatus,
)
from .rendering import build_context, render
from .sms import normalize_phone, send_sms

REPLY_PREFIX = re.compile(r"^\s*((re|fwd?|aw)\s*:\s*)+", re.IGNORECASE)


def _excerpt(text, limit=120):
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


@transaction.atomic
def compose(*, org, user, channel, to_address, body, subject="", contact=None, deal=None, template=None, reply_to=None,
            marketing=False):
    """Creates the outbound Message (variables rendered) and queues delivery after commit.

    marketing=True (sequences, broadcasts) adds an unsubscribe link to email and the STOP footer to SMS.
    """
    values = build_context(org=org, user=user, contact=contact, deal=deal)
    body, subject = render(body, values), render(subject, values)
    if marketing and channel == Channel.SMS and ChannelSettings.for_org(org).sms_opt_out_footer:
        body = f"{body}\nReply STOP to opt out"
    if reply_to is not None:
        conversation = reply_to.conversation
        contact = contact or conversation.contact
        if channel == Channel.EMAIL and subject and not REPLY_PREFIX.match(subject):
            subject = f"Re: {subject}"
    else:
        conversation = Conversation.objects.create(org=org, channel=channel, contact=contact, address=to_address,
                                                   subject=subject[:255])
    message = Message.objects.create(
        org=org, conversation=conversation, channel=channel, direction=Direction.OUTBOUND, status=MessageStatus.QUEUED,
        contact=contact, deal=deal, template=template, sent_by=user, to_address=to_address, subject=subject[:255],
        body_text=body, is_marketing=marketing,
        message_id=email_backend.new_message_id(ChannelSettings.for_org(org)) if channel == Channel.EMAIL else "",
        in_reply_to=(reply_to.message_id if reply_to is not None else ""),
    )
    conversation.last_message_at = timezone.now()
    conversation.is_archived = False
    conversation.save(update_fields=["last_message_at", "is_archived", "updated_at"])

    from .tasks import deliver_message  # avoid import cycle (tasks imports services)

    transaction.on_commit(lambda: deliver_message.delay(message.pk))
    return message


def deliver(message):
    """Sends a queued message once. Safe against double delivery by Celery retries."""
    if message.status != MessageStatus.QUEUED:
        return message
    try:
        if message.channel == Channel.EMAIL:
            email_backend.send_email(message)
        else:
            message.provider_ref = send_sms(ChannelSettings.for_org(message.org), message.to_address, message.body_text)
    except Exception as exc:  # noqa: BLE001 - any provider/SMTP failure is shown to the user, not raised
        message.status = MessageStatus.FAILED
        message.error = f"{type(exc).__name__}: {exc}"[:2000]
        message.save(update_fields=["status", "error", "body_html", "from_address", "updated_at"])
        record(org=message.org, kind=ActivityKind.MESSAGE_FAILED, actor=message.sent_by, contact=message.contact,
               deal=message.deal, message=message,
               summary=f"{message.get_channel_display()} to {message.to_address} failed: {message.error[:120]}")
        return message

    message.status = MessageStatus.SENT
    message.sent_at = timezone.now()
    message.error = ""
    message.save(update_fields=["status", "sent_at", "error", "body_html", "from_address", "provider_ref", "updated_at"])
    if message.channel == Channel.EMAIL:
        kind, summary = ActivityKind.EMAIL_SENT, f"Email sent: {message.subject or _excerpt(message.body_text, 60)}"
    else:
        kind, summary = ActivityKind.SMS_SENT, f"SMS sent: {_excerpt(message.body_text, 80)}"
    record(org=message.org, kind=kind, actor=message.sent_by, contact=message.contact, deal=message.deal,
           message=message, summary=summary)
    return message


# --- Inbound -------------------------------------------------------------------------------------------------------


def _plain_body(parsed):
    part = parsed.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    content = part.get_content()
    if part.get_content_type() == "text/html":
        content = re.sub(r"(?is)<(script|style).*?</\1>", "", content)
        content = re.sub(r"(?i)<br\s*/?>|</p>", "\n", content)
        content = strip_tags(content)
    return content.strip()


def _ids(header):
    return re.findall(r"<[^>]+>", header or "")


@transaction.atomic
def ingest_email(org, raw_bytes):
    """Stores one received email. Returns the Message, or None for duplicates / our own outgoing copies."""
    parsed = BytesParser(policy=policy.default).parsebytes(raw_bytes)
    message_id = (parsed.get("Message-ID") or "").strip()
    if message_id and Message.objects.filter(org=org, message_id=message_id).exists():
        return None
    from_address = parseaddr(str(parsed.get("From", "")))[1].lower()
    if not from_address:
        return None
    channel_settings = ChannelSettings.for_org(org)
    if from_address == email_backend.sender(channel_settings)[1].lower():
        return None  # a copy of something we sent

    subject = str(parsed.get("Subject", ""))[:255]
    in_reply_to = (parsed.get("In-Reply-To") or "").strip()
    references = _ids(parsed.get("References")) + _ids(in_reply_to)

    parent = Message.objects.filter(org=org, message_id__in=references).select_related("conversation").last() if references else None
    contact = Contact.objects.for_org(org).filter(email=from_address).first()
    if parent:
        conversation = parent.conversation
        if conversation.contact is None and contact:
            conversation.contact = contact
    else:
        conversation = Conversation.objects.create(org=org, channel=Channel.EMAIL, contact=contact, address=from_address,
                                                   subject=REPLY_PREFIX.sub("", subject)[:255])
    contact = contact or conversation.contact
    to_address = ", ".join(addr for _, addr in getaddresses([str(parsed.get("To", ""))])) or channel_settings.email_reply_to

    message = Message.objects.create(
        org=org, conversation=conversation, channel=Channel.EMAIL, direction=Direction.INBOUND,
        status=MessageStatus.RECEIVED, contact=contact, deal=parent.deal if parent else None,
        from_address=from_address, to_address=to_address[:254], subject=subject, body_text=_plain_body(parsed) or "(no text)",
        message_id=message_id[:255], in_reply_to=in_reply_to[:255], sent_at=timezone.now(),
    )
    conversation.last_message_at = message.created_at
    conversation.is_unread = True
    conversation.is_archived = False
    conversation.save()
    if contact:
        record(org=org, kind=ActivityKind.EMAIL_RECEIVED, contact=contact, deal=message.deal, message=message,
               summary=f"Email received: {subject or _excerpt(message.body_text, 60)}")
    return message


@transaction.atomic
def ingest_sms(org, from_number, body, provider_ref=""):
    if provider_ref and Message.objects.filter(org=org, provider_ref=provider_ref).exists():
        return None
    number = normalize_phone(from_number)
    tail = number[-10:]
    contact = Contact.objects.for_org(org).filter(phone_digits__endswith=tail).first() if len(tail) >= 7 else None
    conversation = (
        Conversation.objects.for_org(org).filter(channel=Channel.SMS, address__endswith=tail).first()
        or Conversation.objects.create(org=org, channel=Channel.SMS, contact=contact, address=number)
    )
    if conversation.contact is None and contact:
        conversation.contact = contact
    contact = contact or conversation.contact
    message = Message.objects.create(
        org=org, conversation=conversation, channel=Channel.SMS, direction=Direction.INBOUND, status=MessageStatus.RECEIVED,
        contact=contact, from_address=number, to_address="", body_text=body or "", provider_ref=provider_ref[:100],
        sent_at=timezone.now(),
    )
    conversation.last_message_at = message.created_at
    conversation.is_unread = True
    conversation.is_archived = False
    conversation.save()
    if contact:
        record(org=org, kind=ActivityKind.SMS_RECEIVED, contact=contact, message=message, summary=f"SMS received: {_excerpt(body, 80)}")
        from .consent import opt_in, opt_out, sms_keyword

        keyword = sms_keyword(body)
        if keyword == "stop":
            opt_out(contact, Channel.SMS, "replied STOP")
        elif keyword == "start":
            opt_in(contact, Channel.SMS, "replied START")
    return message


# --- Tracking & calls ------------------------------------------------------------------------------------------------


def track_open(message):
    first = message.opened_at is None
    Message.objects.filter(pk=message.pk).update(open_count=message.open_count + 1, opened_at=message.opened_at or timezone.now())
    if first:
        record(org=message.org, kind=ActivityKind.EMAIL_OPENED, contact=message.contact, deal=message.deal, message=message,
               summary=f"Opened email: {message.subject or 'your email'}")


def track_click(link):
    message = link.message
    first_on_message = message.click_count == 0
    now = timezone.now()
    type(link).objects.filter(pk=link.pk).update(clicks=link.clicks + 1, first_clicked_at=link.first_clicked_at or now)
    Message.objects.filter(pk=message.pk).update(click_count=message.click_count + 1)
    if first_on_message:
        record(org=message.org, kind=ActivityKind.EMAIL_CLICKED, contact=message.contact, deal=message.deal, message=message,
               summary=f"Clicked a link in: {message.subject or 'your email'}", data={"url": link.url})


def log_call(call):
    direction = "Outgoing" if call.direction == Direction.OUTBOUND else "Incoming"
    minutes = f", {call.duration_minutes} min" if call.duration_minutes else ""
    record(org=call.org, kind=ActivityKind.CALL_LOGGED, actor=call.user, contact=call.contact, deal=call.deal,
           summary=f"{direction} call: {call.get_outcome_display()}{minutes}", data={"call": call.pk, "notes": call.notes})


def unread_count(org):
    return Conversation.objects.for_org(org).filter(is_unread=True, is_archived=False).count()

