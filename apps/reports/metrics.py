"""Sales metrics. Every function takes the same Scope so the dashboard, emails and exports agree on the numbers.

Periods are whole local days (workspace time zone); `end` is exclusive. Money is the workspace currency.
"""

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.activities.models import Activity, ActivityKind
from apps.contacts.models import Contact, LifecycleStage, Source
from apps.messaging.models import CallLog, Direction, Message
from apps.pipeline.models import Deal, DealStatus
from apps.tasks.models import Task

User = get_user_model()
ZERO = Decimal("0")

PERIODS = OrderedDict([
    ("this_month", "This month"), ("last_month", "Last month"), ("this_quarter", "This quarter"),
    ("last_quarter", "Last quarter"), ("last_30", "Last 30 days"), ("last_90", "Last 90 days"), ("this_year", "This year"),
    ("custom", "Custom range"),
])


def _aware(day):
    return timezone.make_aware(datetime.combine(day, time.min))


def _quarter_start(day):
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def _add_months(day, months):
    month = day.month - 1 + months
    return date(day.year + month // 12, month % 12 + 1, 1)


@dataclass
class Period:
    key: str
    label: str
    start: date  # inclusive
    end: date  # exclusive

    @property
    def start_dt(self):
        return _aware(self.start)

    @property
    def end_dt(self):
        return _aware(self.end)

    @property
    def days(self):
        return (self.end - self.start).days

    @property
    def last_day(self):
        """Inclusive end, for display."""
        return self.end - timedelta(days=1)

    def previous(self):
        """The same-length window just before this one (calendar-aligned for months/quarters)."""
        if self.key in ("this_month", "last_month"):
            start = _add_months(self.start, -1)
            return Period("previous", "previous month", start, self.start)
        if self.key in ("this_quarter", "last_quarter"):
            start = _add_months(self.start, -3)
            return Period("previous", "previous quarter", start, self.start)
        if self.key == "this_year":
            return Period("previous", "last year", date(self.start.year - 1, 1, 1), date(self.start.year, 1, 1))
        return Period("previous", "previous period", self.start - timedelta(days=self.days), self.start)


def resolve_period(key, start=None, end=None, today=None):
    today = today or timezone.localdate()
    tomorrow = today + timedelta(days=1)
    if key == "last_month":
        this = date(today.year, today.month, 1)
        return Period(key, PERIODS[key], _add_months(this, -1), this)
    if key == "this_quarter":
        return Period(key, PERIODS[key], _quarter_start(today), tomorrow)
    if key == "last_quarter":
        this = _quarter_start(today)
        return Period(key, PERIODS[key], _add_months(this, -3), this)
    if key == "last_30":
        return Period(key, PERIODS[key], today - timedelta(days=29), tomorrow)
    if key == "last_90":
        return Period(key, PERIODS[key], today - timedelta(days=89), tomorrow)
    if key == "this_year":
        return Period(key, PERIODS[key], date(today.year, 1, 1), tomorrow)
    if key == "custom" and start and end and start <= end and (end - start).days <= 731:
        return Period(key, f"{start:%d %b %Y} – {end:%d %b %Y}", start, end + timedelta(days=1))
    return Period("this_month", PERIODS["this_month"], date(today.year, today.month, 1), tomorrow)


@dataclass
class Scope:
    org: object
    period: Period
    pipeline: object = None
    owner_ids: list = field(default_factory=list)  # empty = everyone

    def deals(self):
        deals = Deal.objects.filter(org=self.org)
        if self.pipeline is not None:
            deals = deals.filter(pipeline=self.pipeline)
        if self.owner_ids:
            deals = deals.filter(owner_id__in=self.owner_ids)
        return deals

    def contacts(self):
        contacts = Contact.objects.filter(org=self.org)
        if self.owner_ids:
            contacts = contacts.filter(owner_id__in=self.owner_ids)
        return contacts

    def in_period(self, field_name, period=None):
        period = period or self.period
        return Q(**{f"{field_name}__gte": period.start_dt, f"{field_name}__lt": period.end_dt})


# --- KPIs ----------------------------------------------------------------------------------------------------------------


def _closed(scope, period):
    closed = scope.deals().filter(scope.in_period("closed_at", period))
    won = closed.filter(status=DealStatus.WON)
    agg = won.aggregate(revenue=Sum("value"), n=Count("pk"))
    lost = closed.filter(status=DealStatus.LOST).count()
    revenue, won_count = agg["revenue"] or ZERO, agg["n"]
    # Imported/backdated deals can have closed_at before created_at; count those as same-day rather than negative.
    cycles = [max((closed_at - created).days, 0) for created, closed_at in won.values_list("created_at", "closed_at")]
    return {
        "revenue": revenue, "won": won_count, "lost": lost,
        "win_rate": round(100 * won_count / (won_count + lost)) if won_count + lost else None,
        "avg_deal": (revenue / won_count) if won_count else None,
        "avg_cycle_days": round(sum(cycles) / len(cycles)) if cycles else None,
    }


def _delta(now, before):
    if now is None or before is None:
        return None
    if before == 0:
        return None if now == 0 else "new"
    return round(100 * (Decimal(now) - Decimal(before)) / Decimal(before))


def kpis(scope):
    current, previous = _closed(scope, scope.period), _closed(scope, scope.period.previous())
    open_deals = scope.deals().filter(status=DealStatus.OPEN).select_related("stage")
    open_value = sum((d.value for d in open_deals), ZERO)
    weighted = sum((d.value * d.stage.probability / 100 for d in open_deals), ZERO)
    new_deals = scope.deals().filter(scope.in_period("created_at")).count()
    leads = scope.contacts().filter(scope.in_period("created_at"))
    new_leads = leads.count()
    prev_leads = scope.contacts().filter(scope.in_period("created_at", scope.period.previous())).count()
    converted = leads.filter(lifecycle_stage=LifecycleStage.CUSTOMER).count()
    return {
        **current,
        "open_value": open_value, "open_count": len(open_deals), "weighted": weighted, "new_deals": new_deals,
        "new_leads": new_leads, "lead_conversion": round(100 * converted / new_leads) if new_leads else None,
        "deltas": {
            "revenue": _delta(current["revenue"], previous["revenue"]), "won": _delta(current["won"], previous["won"]),
            "win_rate": None if current["win_rate"] is None or previous["win_rate"] is None else current["win_rate"] - previous["win_rate"],
            "new_leads": _delta(new_leads, prev_leads),
        },
        "previous_label": scope.period.previous().label,
    }


# --- Series ------------------------------------------------------------------------------------------------------------


def bucket_for(period):
    if period.days <= 31:
        return "day"
    if period.days <= 120:
        return "week"
    return "month"


def _bucket_start(day, bucket):
    if bucket == "day":
        return day
    if bucket == "week":  # weeks start on Saturday (Bangladesh work week)
        return day - timedelta(days=(day.weekday() - 5) % 7)
    return date(day.year, day.month, 1)


def _bucket_label(day, bucket):
    return day.strftime("%d %b") if bucket != "month" else day.strftime("%b %Y")


def revenue_over_time(scope):
    """[{label, value, count}] for every bucket in the period, zeros included."""
    bucket = bucket_for(scope.period)
    buckets = OrderedDict()
    day = _bucket_start(scope.period.start, bucket)
    while day < scope.period.end:
        buckets[day] = {"label": _bucket_label(day, bucket), "value": ZERO, "count": 0}
        day = day + timedelta(days=1) if bucket == "day" else day + timedelta(days=7) if bucket == "week" else _add_months(day, 1)
    won = scope.deals().filter(scope.in_period("closed_at"), status=DealStatus.WON).values_list("closed_at", "value")
    for closed_at, value in won:
        key = _bucket_start(timezone.localtime(closed_at).date(), bucket)
        if key in buckets:
            buckets[key]["value"] += value
            buckets[key]["count"] += 1
    return {"bucket": bucket, "points": list(buckets.values())}


def funnel(scope):
    """How far deals created in the period got: one row per open stage, then Won. Needs a pipeline."""
    if scope.pipeline is None:
        return []
    stages = list(scope.pipeline.stages.order_by("position", "pk"))
    if not stages:
        return []
    position = {s.pk: i for i, s in enumerate(stages)}
    deals = list(scope.deals().filter(scope.in_period("created_at")).values("pk", "stage_id", "status"))
    reached = {d["pk"]: position.get(d["stage_id"], 0) for d in deals}
    moves = Activity.objects.filter(deal_id__in=reached.keys(), kind=ActivityKind.DEAL_STAGE_CHANGED).values_list("deal_id", "data")
    for deal_id, data in moves:
        reached[deal_id] = max(reached[deal_id], position.get((data or {}).get("to"), 0))
    won = {d["pk"] for d in deals if d["status"] == DealStatus.WON}
    rows = []
    for i, stage in enumerate(stages):
        # Won deals passed every stage even if someone skipped straight to "won".
        count = sum(1 for pk, pos in reached.items() if pos >= i or pk in won)
        rows.append({"label": stage.name, "count": count})
    rows.append({"label": "Won", "count": len(won), "is_won": True})
    for previous, row in zip(rows, rows[1:]):
        row["conversion"] = round(100 * row["count"] / previous["count"]) if previous["count"] else None
    return rows


def forecast(scope, months=6):
    """Open deals by expected close month: [{label, value, weighted, count}], plus overdue and undated buckets."""
    today = timezone.localdate()
    first = date(today.year, today.month, 1)
    months_out = [_add_months(first, i) for i in range(months)]
    rows = OrderedDict((m, {"label": m.strftime("%b %Y"), "value": ZERO, "weighted": ZERO, "count": 0}) for m in months_out)
    overdue = {"label": "Overdue", "value": ZERO, "weighted": ZERO, "count": 0}
    undated = {"label": "No close date", "value": ZERO, "weighted": ZERO, "count": 0}
    for deal in scope.deals().filter(status=DealStatus.OPEN).select_related("stage"):
        close = deal.expected_close_date
        if close is None:
            row = undated
        elif close < today:
            row = overdue
        else:
            row = rows.get(date(close.year, close.month, 1))
            if row is None:
                continue  # beyond the horizon
        row["value"] += deal.value
        row["weighted"] += deal.value * deal.stage.probability / 100
        row["count"] += 1
    return {"months": list(rows.values()), "overdue": overdue, "undated": undated}


def lost_reasons(scope):
    lost = scope.deals().filter(scope.in_period("closed_at"), status=DealStatus.LOST)
    rows = lost.values("lost_reason__name").annotate(count=Count("pk"), value=Sum("value")).order_by("-count")
    return [{"label": r["lost_reason__name"] or "No reason given", "count": r["count"], "value": r["value"] or ZERO} for r in rows]


def lead_sources(scope):
    labels = dict(Source.choices)
    rows = scope.contacts().filter(scope.in_period("created_at")).values("source").annotate(count=Count("pk")).order_by("-count")
    return [{"label": labels.get(r["source"], r["source"]), "count": r["count"]} for r in rows]


# --- People -------------------------------------------------------------------------------------------------------------


def leaderboard(scope, users):
    """One row per user with outcomes, activity and target attainment, sorted by revenue won."""
    from .targets import attainment_for

    ids = [u.pk for u in users]
    closed = scope.deals().filter(scope.in_period("closed_at"), owner_id__in=ids)
    won = {r["owner_id"]: r for r in closed.filter(status=DealStatus.WON).values("owner_id").annotate(value=Sum("value"), n=Count("pk"))}
    lost = dict(closed.filter(status=DealStatus.LOST).values_list("owner_id").annotate(n=Count("pk")))
    open_ = {r["owner_id"]: r for r in scope.deals().filter(status=DealStatus.OPEN, owner_id__in=ids).values("owner_id")
             .annotate(value=Sum("value"), n=Count("pk"))}
    created = dict(scope.deals().filter(scope.in_period("created_at"), owner_id__in=ids).values_list("owner_id").annotate(n=Count("pk")))
    period_q = scope.in_period("created_at")
    emails = dict(Message.objects.filter(period_q, org=scope.org, direction=Direction.OUTBOUND, channel="email", sent_by_id__in=ids)
                  .values_list("sent_by_id").annotate(n=Count("pk")))
    sms = dict(Message.objects.filter(period_q, org=scope.org, direction=Direction.OUTBOUND, channel="sms", sent_by_id__in=ids)
               .values_list("sent_by_id").annotate(n=Count("pk")))
    calls = dict(CallLog.objects.filter(scope.in_period("called_at"), org=scope.org, user_id__in=ids).values_list("user_id").annotate(n=Count("pk")))
    tasks = dict(Task.objects.filter(scope.in_period("completed_at"), org=scope.org, completed_by_id__in=ids)
                 .values_list("completed_by_id").annotate(n=Count("pk")))
    rows = []
    for user in users:
        won_n = won.get(user.pk, {}).get("n", 0)
        lost_n = lost.get(user.pk, 0)
        revenue = won.get(user.pk, {}).get("value") or ZERO
        rows.append({
            "user": user, "revenue": revenue, "won": won_n, "lost": lost_n,
            "win_rate": round(100 * won_n / (won_n + lost_n)) if won_n + lost_n else None,
            "open_value": open_.get(user.pk, {}).get("value") or ZERO, "open_count": open_.get(user.pk, {}).get("n", 0),
            "new_deals": created.get(user.pk, 0), "emails": emails.get(user.pk, 0), "sms": sms.get(user.pk, 0),
            "calls": calls.get(user.pk, 0), "tasks_done": tasks.get(user.pk, 0),
            "target": attainment_for(scope, user=user),
        })
    rows.sort(key=lambda r: (r["revenue"], r["won"]), reverse=True)
    return rows


def org_users(org):
    return list(User.objects.filter(memberships__org=org, memberships__is_active=True).order_by("first_name", "email"))

