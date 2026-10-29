"""Opt-outs, unsubscribe links and quiet hours for automated / marketing messages."""

from datetime import datetime, timedelta

from django.conf import settings
from django.core import signing
from django.urls import reverse
from django.utils import timezone

from apps.activities.models import ActivityKind
from apps.activities.services import record

from .models import Channel

SALT = "crm.unsubscribe"
STOP_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "বন্ধ"}
START_WORDS = {"start", "unstop", "subscribe"}


def unsubscribe_url(contact):
    token = signing.dumps({"c": contact.pk, "o": contact.org_id}, salt=SALT)
    return settings.SITE_URL.rstrip("/") + reverse("messaging:unsubscribe", args=[token])


def contact_from_token(token):
    from apps.contacts.models import Contact

    try:
        data = signing.loads(token, salt=SALT)  # no max_age: unsubscribe links must keep working
    except signing.BadSignature:
        return None
    return Contact.objects.filter(pk=data.get("c"), org_id=data.get("o")).first()


OPT_OUT_FIELDS = {Channel.EMAIL: "email_opt_out", Channel.SMS: "sms_opt_out", Channel.WHATSAPP: "whatsapp_opt_out"}


def is_opted_out(contact, channel):
    return getattr(contact, OPT_OUT_FIELDS[channel])


def can_message(contact, channel):
    if contact is None:
        return False
    address = contact.email if channel == Channel.EMAIL else contact.phone
    return bool(address) and not is_opted_out(contact, channel)


def opt_out(contact, channel, how):
    field = OPT_OUT_FIELDS[channel]
    if getattr(contact, field):
        return False
    setattr(contact, field, True)
    contact.opted_out_at = timezone.now()
    contact.save(update_fields=[field, "opted_out_at", "updated_at"])
    record(org=contact.org, kind=ActivityKind.UNSUBSCRIBED, contact=contact,
           summary=f"Unsubscribed from {Channel(channel).label} ({how})", data={"channel": channel})
    return True


def opt_in(contact, channel, how):
    field = OPT_OUT_FIELDS[channel]
    if not getattr(contact, field):
        return False
    setattr(contact, field, False)
    contact.save(update_fields=[field, "updated_at"])
    record(org=contact.org, kind=ActivityKind.RESUBSCRIBED, contact=contact,
           summary=f"Resubscribed to {Channel(channel).label} ({how})", data={"channel": channel})
    return True


def sms_keyword(body):
    word = (body or "").strip().lower().strip(".!")
    if word in STOP_WORDS:
        return "stop"
    if word in START_WORDS:
        return "start"
    return None


def next_send_time(channel_settings, now=None):
    """None if automated messages may go out now, otherwise when the sending window next opens."""
    if not channel_settings.quiet_hours_enabled:
        return None
    now = timezone.localtime(now or timezone.now())
    start, end = channel_settings.send_window_start, channel_settings.send_window_end
    current = now.time()
    if start <= end:
        if start <= current < end:
            return None
        day = now.date() if current < start else now.date() + timedelta(days=1)
    else:  # window crosses midnight, e.g. 20:00-02:00
        if current >= start or current < end:
            return None
        day = now.date()
    return timezone.make_aware(datetime.combine(day, start))
