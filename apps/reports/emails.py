"""Scheduled report emails: the team summary and each rep's pipeline digest."""

from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.pipeline.models import Deal, DealStatus
from apps.pipeline.services import default_pipeline
from apps.tasks.models import Task

from . import metrics
from .models import Frequency, ReportKind, ReportSubscription


def report_period(frequency, today=None):
    """Daily = yesterday, weekly = the last 7 days, monthly = last calendar month."""
    today = today or timezone.localdate()
    if frequency == Frequency.DAILY:
        return metrics.Period("custom", f"{today - timedelta(days=1):%A %d %b}", today - timedelta(days=1), today)
    if frequency == Frequency.WEEKLY:
        return metrics.Period("custom", "the last 7 days", today - timedelta(days=7), today)
    return metrics.resolve_period("last_month", today=today)


def digest_context(org, user, period):
    today = timezone.localdate()
    deals = Deal.objects.filter(org=org, owner=user, status=DealStatus.OPEN).select_related("stage", "contact")
    week_end = today + timedelta(days=7)
    return {
        "closing_soon": deals.filter(expected_close_date__gte=today, expected_close_date__lt=week_end).order_by("expected_close_date"),
        "overdue_deals": deals.filter(expected_close_date__lt=today).order_by("expected_close_date"),
        "idle_deals": [d for d in deals.order_by("stage_changed_at")[:50] if d.is_rotting][:10],
        "tasks_due": Task.objects.open().filter(org=org, assignee=user, due_at__lt=metrics._aware(today + timedelta(days=1))).order_by("due_at")[:15],
        "kpis": metrics.kpis(metrics.Scope(org=org, period=period, owner_ids=[user.pk])),
    }


def build(subscription, today=None):
    org, user = subscription.org, subscription.user
    period = report_period(subscription.frequency, today)
    context = {"org": org, "user": user, "period": period, "subscription": subscription,
               "site": settings.SITE_URL.rstrip("/"), "reports_url": settings.SITE_URL.rstrip("/") + reverse("reports:overview")}
    if subscription.kind == ReportKind.SUMMARY:
        scope = metrics.Scope(org=org, period=period, pipeline=default_pipeline(org))
        context.update(kpis=metrics.kpis(scope), leaders=metrics.leaderboard(scope, metrics.org_users(org))[:5],
                       lost=metrics.lost_reasons(scope)[:3])
        subject = f"{org.name} sales summary: {period.label}"
        template = "reports/emails/summary"
    else:
        context.update(digest_context(org, user, period))
        subject = f"Your pipeline today ({org.name})" if subscription.frequency == Frequency.DAILY else f"Your pipeline digest ({org.name})"
        template = "reports/emails/digest"
    message = EmailMultiAlternatives(subject, render_to_string(f"{template}.txt", context), None, [user.email])
    message.attach_alternative(render_to_string(f"{template}.html", context), "text/html")
    return message


def is_due(subscription, now):
    local = timezone.localtime(now)
    if local.hour < subscription.hour:
        return False
    if subscription.frequency == Frequency.WEEKLY and local.weekday() != subscription.weekday:
        return False
    if subscription.frequency == Frequency.MONTHLY and local.day != 1:
        return False
    last = subscription.last_sent_at and timezone.localtime(subscription.last_sent_at)
    return not last or last.date() < local.date()


def send_due_reports(now=None):
    now = now or timezone.now()
    sent = 0
    subscriptions = ReportSubscription.objects.filter(is_active=True, org__is_active=True, user__is_active=True).select_related("org", "user")
    for subscription in subscriptions:
        if not is_due(subscription, now):
            continue
        # Claim first, so a second worker can't send the same report.
        if not ReportSubscription.objects.filter(pk=subscription.pk, last_sent_at=subscription.last_sent_at).update(last_sent_at=now):
            continue
        build(subscription).send(fail_silently=False)
        sent += 1
    return sent
