"""Target attainment: actual vs goal for the target whose month/quarter matches the report period."""

from decimal import Decimal

from django.db.models import Count, Sum

from apps.contacts.models import Contact
from apps.pipeline.models import Deal, DealStatus

from .models import SalesTarget, TargetMetric, TargetPeriod


def period_kind(period):
    """Targets are per calendar month/quarter, so they only show on the matching report periods."""
    if period.key in ("this_month", "last_month"):
        return TargetPeriod.MONTH
    if period.key in ("this_quarter", "last_quarter"):
        return TargetPeriod.QUARTER
    return None


def actual(org, metric, start_dt, end_dt, user=None):
    if metric == TargetMetric.NEW_LEADS:
        contacts = Contact.objects.filter(org=org, created_at__gte=start_dt, created_at__lt=end_dt)
        return Decimal((contacts.filter(owner=user) if user else contacts).count())
    deals = Deal.objects.filter(org=org)
    if user is not None:
        deals = deals.filter(owner=user)
    if metric == TargetMetric.NEW_DEALS:
        return Decimal(deals.filter(created_at__gte=start_dt, created_at__lt=end_dt).count())
    won = deals.filter(status=DealStatus.WON, closed_at__gte=start_dt, closed_at__lt=end_dt)
    if metric == TargetMetric.WON_DEALS:
        return Decimal(won.aggregate(n=Count("pk"))["n"])
    return won.aggregate(v=Sum("value"))["v"] or Decimal("0")


def attainment_for(scope, user=None, metric=TargetMetric.REVENUE):
    """{target, actual, percent} for the period's target, or None when there's no matching target."""
    kind = period_kind(scope.period)
    if kind is None:
        return None
    target = SalesTarget.objects.filter(org=scope.org, user=user, metric=metric, period=kind, period_start=scope.period.start).first()
    if target is None:
        return None
    value = actual(scope.org, metric, scope.period.start_dt, scope.period.end_dt, user=user)
    return {"target": target.value, "actual": value, "percent": round(100 * value / target.value) if target.value else None,
            "metric": target.get_metric_display()}


def team_targets(scope):
    """All team-wide targets for the period, with attainment."""
    kind = period_kind(scope.period)
    if kind is None:
        return []
    rows = []
    for target in SalesTarget.objects.filter(org=scope.org, user__isnull=True, period=kind, period_start=scope.period.start):
        value = actual(scope.org, target.metric, scope.period.start_dt, scope.period.end_dt)
        rows.append({"target": target, "actual": value, "percent": round(100 * value / target.value) if target.value else None})
    return rows
