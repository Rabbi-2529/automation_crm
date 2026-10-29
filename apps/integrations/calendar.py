"""iCalendar (RFC 5545) feed of one person's tasks and deal close dates, for subscribing from Google Calendar & co."""

from datetime import UTC, timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from apps.pipeline.models import Deal, DealStatus
from apps.tasks.models import Task, TaskKind

PAST_DAYS = 60
EVENT_MINUTES = {TaskKind.MEETING: 60}
DEFAULT_MINUTES = 30


def _escape(text):
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


def _fold(line):
    """Lines longer than 75 octets continue on the next line after a space."""
    raw = line.encode()
    if len(raw) <= 75:
        return line
    parts, start = [], 0
    while start < len(raw):
        end = min(start + (75 if not parts else 74), len(raw))
        while end < len(raw) and (raw[end] & 0xC0) == 0x80:  # don't split a UTF-8 character
            end -= 1
        parts.append(raw[start:end].decode())
        start = end
    return "\r\n ".join(parts)


def _utc(value):
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _link(name, pk):
    return settings.SITE_URL.rstrip("/") + reverse(name, args=[pk])


def _task_event(task, stamp):
    start = task.due_at
    end = start + timedelta(minutes=EVENT_MINUTES.get(task.kind, DEFAULT_MINUTES))
    related = task.related
    description = "\n".join(filter(None, [
        task.description,
        f"{task.get_kind_display()} · {task.get_priority_display()} priority",
        f"Related: {related}" if related else "",
    ]))
    lines = [
        "BEGIN:VEVENT", f"UID:task-{task.pk}@thingbd-crm", f"DTSTAMP:{stamp}", f"DTSTART:{_utc(start)}", f"DTEND:{_utc(end)}",
        f"SUMMARY:{_escape(('✓ ' if task.is_done else '') + task.title)}", f"DESCRIPTION:{_escape(description)}",
        "STATUS:CONFIRMED", "TRANSP:TRANSPARENT",
    ]
    if related is not None:
        url_name = {"Deal": "pipeline:deal_detail", "Contact": "contacts:contact_detail", "Company": "contacts:company_detail"}
        lines.append(f"URL:{_link(url_name[type(related).__name__], related.pk)}")
    lines.append("END:VEVENT")
    return lines


def _deal_event(deal, stamp):
    day = deal.expected_close_date
    return [
        "BEGIN:VEVENT", f"UID:deal-{deal.pk}@thingbd-crm", f"DTSTAMP:{stamp}",
        f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
        f"SUMMARY:{_escape(f'Close: {deal.title} ({deal.currency_code} {deal.value:,.0f})')}",
        f"DESCRIPTION:{_escape(f'Expected close date · {deal.stage.name} · {deal.pipeline.name}')}",
        f"URL:{_link('pipeline:deal_detail', deal.pk)}", "TRANSP:TRANSPARENT", "END:VEVENT",
    ]


def build_feed(feed, now=None):
    now = now or timezone.now()
    stamp = _utc(now)
    user, org = feed.user, feed.org
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ThingBD//CRM//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(f'ThingBD CRM · {org.name}')}", f"X-WR-TIMEZONE:{org.timezone}",
        "X-PUBLISHED-TTL:PT1H", "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
    ]
    tasks = (Task.objects.for_org(org).filter(assignee=user, due_at__isnull=False, due_at__gte=now - timedelta(days=PAST_DAYS))
             .select_related("contact", "company", "deal").order_by("due_at")[:1000])
    for task in tasks:
        lines += _task_event(task, stamp)
    if feed.include_deals:
        deals = (Deal.objects.for_org(org).filter(owner=user, status=DealStatus.OPEN, expected_close_date__isnull=False)
                 .select_related("stage", "pipeline", "org")[:500])
        for deal in deals:
            lines += _deal_event(deal, stamp)
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
