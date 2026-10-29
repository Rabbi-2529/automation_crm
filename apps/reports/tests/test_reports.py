import csv
import io
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.activities.models import ActivityKind
from apps.activities.services import record
from apps.automation.tests.base import AutomationTestCase
from apps.contacts.models import Contact
from apps.orgs.models import Team
from apps.pipeline.models import Deal, DealStatus, LostReason

from .. import metrics
from ..emails import is_due, send_due_reports
from ..models import ReportSubscription, SalesTarget

TODAY = date(2026, 10, 29)


def at(day, hour=12):
    return timezone.make_aware(datetime.combine(day, time(hour)))


class PeriodTests(AutomationTestCase):
    def test_period_resolution_and_previous(self):
        p = metrics.resolve_period("this_month", today=TODAY)
        self.assertEqual((p.start, p.end, p.previous().start, p.previous().end), (date(2026, 10, 1), date(2026, 10, 30), date(2026, 9, 1), date(2026, 10, 1)))
        p = metrics.resolve_period("last_month", today=TODAY)
        self.assertEqual((p.start, p.end), (date(2026, 9, 1), date(2026, 10, 1)))
        p = metrics.resolve_period("this_quarter", today=TODAY)
        self.assertEqual((p.start, p.previous().start), (date(2026, 10, 1), date(2026, 7, 1)))
        p = metrics.resolve_period("last_quarter", today=date(2026, 2, 10))
        self.assertEqual((p.start, p.end), (date(2025, 10, 1), date(2026, 1, 1)))
        p = metrics.resolve_period("custom", date(2026, 1, 5), date(2026, 1, 11), today=TODAY)
        self.assertEqual((p.start, p.end, p.days, p.previous().start), (date(2026, 1, 5), date(2026, 1, 12), 7, date(2025, 12, 29)))
        # Bad custom ranges fall back to this month.
        self.assertEqual(metrics.resolve_period("custom", date(2026, 2, 1), date(2026, 1, 1), today=TODAY).key, "this_month")


class MetricsBase(AutomationTestCase):
    def setUp(self):
        super().setUp()
        self.reason = LostReason.objects.for_org(self.org).first()
        self.october = metrics.resolve_period("custom", date(2026, 10, 1), date(2026, 10, 31), today=TODAY)
        self.september = metrics.resolve_period("custom", date(2026, 9, 1), date(2026, 9, 30), today=TODAY)

    def deal(self, title, value, status=DealStatus.OPEN, closed=None, created=None, owner=None, stage=0, close_date=None, **extra):
        deal = Deal.objects.create(org=self.org, pipeline=self.pipeline, stage=self.stages[stage], title=title, value=value,
                                   status=status, owner=owner, expected_close_date=close_date, **extra)
        Deal.objects.filter(pk=deal.pk).update(created_at=created or at(date(2026, 10, 1)), closed_at=closed)
        deal.refresh_from_db()
        return deal

    def scope(self, period=None, **kwargs):
        return metrics.Scope(org=self.org, period=period or self.october, pipeline=self.pipeline, **kwargs)


class MetricsTests(MetricsBase):
    def test_kpis(self):
        self.deal("Won A", 1000, DealStatus.WON, closed=at(date(2026, 10, 5)), created=at(date(2026, 9, 25)), owner=self.agent.user)
        self.deal("Won B", 3000, DealStatus.WON, closed=at(date(2026, 10, 20)), created=at(date(2026, 10, 10)), owner=self.manager.user)
        self.deal("Lost", 500, DealStatus.LOST, closed=at(date(2026, 10, 7)), lost_reason=self.reason)
        self.deal("Sept win", 2000, DealStatus.WON, closed=at(date(2026, 9, 10)))
        self.deal("Open", 4000, stage=2)  # 50% stage
        k = metrics.kpis(self.scope())
        self.assertEqual((k["revenue"], k["won"], k["lost"], k["win_rate"]), (Decimal("4000"), 2, 1, 67))
        self.assertEqual(k["avg_deal"], Decimal("2000"))
        self.assertEqual(k["avg_cycle_days"], 10)  # (10 + 10) / 2
        self.assertEqual((k["open_value"], k["weighted"], k["open_count"]), (Decimal("4000"), Decimal("2000"), 1))
        self.assertEqual(k["deltas"]["revenue"], 100)  # 4000 vs 2000 in September
        self.assertEqual(k["deltas"]["won"], 100)
        # Owner filter.
        k = metrics.kpis(self.scope(owner_ids=[self.agent.user.pk]))
        self.assertEqual((k["revenue"], k["won"]), (Decimal("1000"), 1))

    def test_other_orgs_never_count(self):
        from apps.pipeline.services import default_pipeline

        theirs = default_pipeline(self.other_org)
        d = Deal.objects.create(org=self.other_org, pipeline=theirs, stage=theirs.stages.first(), title="X", value=5, status=DealStatus.WON)
        Deal.objects.filter(pk=d.pk).update(closed_at=at(date(2026, 10, 5)))
        scope = metrics.Scope(org=self.org, period=self.october)
        self.assertEqual(metrics.kpis(scope)["won"], 0)
        self.assertEqual(metrics.revenue_over_time(scope)["points"][4]["value"], 0)

    def test_revenue_buckets(self):
        self.deal("A", 1000, DealStatus.WON, closed=at(date(2026, 10, 5)))
        self.deal("B", 500, DealStatus.WON, closed=at(date(2026, 10, 5), hour=23))  # late evening still that local day
        series = metrics.revenue_over_time(self.scope())
        self.assertEqual(series["bucket"], "day")
        self.assertEqual(len(series["points"]), 31)
        self.assertEqual((series["points"][4]["label"], series["points"][4]["value"], series["points"][4]["count"]), ("05 Oct", Decimal("1500"), 2))
        year = metrics.resolve_period("this_year", today=TODAY)
        monthly = metrics.revenue_over_time(self.scope(year))
        self.assertEqual(monthly["bucket"], "month")
        self.assertEqual([p["label"] for p in monthly["points"]][-1], "Oct 2026")

    def test_funnel_uses_stage_history(self):
        jumped = self.deal("Jumped", 1, stage=0)
        record(org=self.org, kind=ActivityKind.DEAL_STAGE_CHANGED, deal=jumped, summary="m",
               data={"from": self.stages[0].pk, "to": self.stages[2].pk})
        Deal.objects.filter(pk=jumped.pk).update(stage=self.stages[1])  # moved back later; still "reached" stage 3
        self.deal("Won straight away", 1, DealStatus.WON, stage=0, closed=at(date(2026, 10, 3)))
        self.deal("Stuck", 1, stage=0)
        rows = metrics.funnel(self.scope())
        self.assertEqual([r["count"] for r in rows], [3, 2, 2, 1, 1])
        self.assertEqual(rows[-1]["label"], "Won")
        self.assertEqual(rows[1]["conversion"], 67)
        self.assertEqual(metrics.funnel(metrics.Scope(org=self.org, period=self.october)), [])  # needs a pipeline

    def test_forecast(self):
        today = timezone.localdate()
        self.deal("This month", 1000, stage=2, close_date=today + timedelta(days=1))
        self.deal("Overdue", 300, close_date=today - timedelta(days=3))
        self.deal("Undated", 200)
        self.deal("Too far", 999, close_date=today + timedelta(days=400))
        f = metrics.forecast(self.scope())
        self.assertEqual(len(f["months"]), 6)
        current = next(m for m in f["months"] if m["count"])
        self.assertEqual((current["value"], current["weighted"]), (Decimal("1000"), Decimal("500")))
        self.assertEqual((f["overdue"]["value"], f["undated"]["value"]), (Decimal("300"), Decimal("200")))

    def test_lost_reasons_and_sources(self):
        self.deal("L1", 100, DealStatus.LOST, closed=at(date(2026, 10, 2)), lost_reason=self.reason)
        self.deal("L2", 50, DealStatus.LOST, closed=at(date(2026, 10, 3)))
        self.assertEqual([(r["label"], r["count"]) for r in metrics.lost_reasons(self.scope())],
                         [(self.reason.name, 1), ("No reason given", 1)])
        c = self.contact(first_name="Web", source="web_form")
        Contact.objects.filter(pk=c.pk).update(created_at=at(date(2026, 10, 9)))
        self.assertIn(("Web form", 1), [(r["label"], r["count"]) for r in metrics.lead_sources(self.scope())])

    def test_leaderboard_and_targets(self):
        period = metrics.resolve_period("this_month", today=timezone.localdate())
        start = period.start
        self.deal("Agent win", 6000, DealStatus.WON, closed=period.start_dt + timedelta(hours=10), owner=self.agent.user)
        self.deal("Agent loss", 1, DealStatus.LOST, closed=period.start_dt + timedelta(hours=11), owner=self.agent.user)
        SalesTarget.objects.create(org=self.org, user=self.agent.user, metric="revenue", period="month", period_start=start, value=10000)
        SalesTarget.objects.create(org=self.org, user=None, metric="revenue", period="month", period_start=start, value=20000)
        scope = metrics.Scope(org=self.org, period=period)
        rows = metrics.leaderboard(scope, metrics.org_users(self.org))
        top = rows[0]
        self.assertEqual((top["user"], top["revenue"], top["won"], top["lost"], top["win_rate"]), (self.agent.user, Decimal("6000"), 1, 1, 50))
        self.assertEqual(top["target"]["percent"], 60)
        from ..targets import team_targets

        self.assertEqual(team_targets(scope)[0]["percent"], 30)
        # Custom periods don't line up with monthly targets, so no attainment is shown.
        self.assertIsNone(metrics.leaderboard(self.scope(), [self.agent.user])[0]["target"])


class ViewTests(MetricsBase):
    def test_dashboard_filters_and_permissions(self):
        team = Team.objects.create(org=self.org, name="Field")
        team.members.add(self.agent)
        self.deal("Agent win", 700, DealStatus.WON, closed=timezone.now(), owner=self.agent.user)
        self.deal("Manager win", 300, DealStatus.WON, closed=timezone.now(), owner=self.manager.user)
        self.login(self.agent)
        page = self.client.get(reverse("reports:overview"))
        self.assertEqual(page.context["kpis"]["revenue"], Decimal("1000"))
        self.assertIn('id="chart-data"', page.content.decode())
        self.assertEqual(self.client.get(reverse("reports:overview"), {"owner": f"team-{team.pk}"}).context["kpis"]["revenue"], Decimal("700"))
        self.assertEqual(self.client.get(reverse("reports:overview"), {"owner": "me"}).context["kpis"]["revenue"], Decimal("700"))
        self.assertEqual(self.client.get(reverse("reports:overview"), {"owner": self.outsider.user.pk}).context["kpis"]["revenue"], 0)
        self.assertEqual(self.client.get(reverse("reports:leaderboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("reports:leaderboard_export")).status_code, 403)
        self.assertEqual(self.client.get(reverse("reports:targets")).status_code, 403)

    def test_csv_exports(self):
        self.deal("Agent win", 700, DealStatus.WON, closed=timezone.now(), owner=self.agent.user, created=timezone.now() - timedelta(days=3))
        self.login(self.manager)
        body = b"".join(self.client.get(reverse("reports:closed_deals_export")).streaming_content).decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(body)))
        self.assertEqual(rows[0][:3], ["Deal", "Status", "Value"])
        self.assertEqual((rows[1][0], rows[1][1], rows[1][5]), ("Agent win", "Won", "3"))
        body = b"".join(self.client.get(reverse("reports:leaderboard_export")).streaming_content).decode("utf-8-sig")
        self.assertIn(self.agent.user.email, body)

    def test_target_form(self):
        self.login(self.manager)
        response = self.client.post(reverse("reports:target_create"), {"metric": "revenue", "period": "quarter", "month": "2026-11", "value": "5"})
        self.assertContains(response, "Quarters start in")
        self.client.post(reverse("reports:target_create"), {"metric": "revenue", "period": "month", "month": "2026-11", "value": "50000"})
        self.assertEqual(SalesTarget.objects.get().period_start, date(2026, 11, 1))
        response = self.client.post(reverse("reports:target_create"), {"metric": "revenue", "period": "month", "month": "2026-11", "value": "1"})
        self.assertContains(response, "already exists")
        self.assertEqual(self.client.post(reverse("reports:target_create"), {"user": self.outsider.user.pk, "metric": "revenue",
                                                                             "period": "month", "month": "2026-12", "value": "1"}).status_code, 200)
        self.assertEqual(SalesTarget.objects.count(), 1)


class ScheduledReportTests(AutomationTestCase):
    def test_is_due_rules(self):
        daily = ReportSubscription(org=self.org, user=self.agent.user, frequency="daily", hour=9)
        self.assertFalse(is_due(daily, at(TODAY, 8)))
        self.assertTrue(is_due(daily, at(TODAY, 9)))
        daily.last_sent_at = at(TODAY, 9)
        self.assertFalse(is_due(daily, at(TODAY, 15)))
        self.assertTrue(is_due(daily, at(TODAY + timedelta(days=1), 10)))
        weekly = ReportSubscription(org=self.org, user=self.agent.user, frequency="weekly", weekday=TODAY.weekday(), hour=9)
        self.assertTrue(is_due(weekly, at(TODAY, 10)))
        self.assertFalse(is_due(weekly, at(TODAY + timedelta(days=1), 10)))
        monthly = ReportSubscription(org=self.org, user=self.agent.user, frequency="monthly", hour=9)
        self.assertFalse(is_due(monthly, at(TODAY, 10)))
        self.assertTrue(is_due(monthly, at(date(2026, 11, 1), 10)))

    def test_send_due_reports_once(self):
        ReportSubscription.objects.create(org=self.org, user=self.manager.user, kind="summary", frequency="daily", hour=0)
        ReportSubscription.objects.create(org=self.org, user=self.agent.user, kind="digest", frequency="daily", hour=0)
        self.assertEqual(send_due_reports(), 2)
        self.assertEqual(send_due_reports(), 0)
        subjects = sorted(m.subject for m in mail.outbox)
        self.assertTrue(subjects[0].startswith("ThingBD sales summary"))
        self.assertTrue(subjects[1].startswith("Your pipeline today"))
        self.assertTrue(all(m.alternatives for m in mail.outbox))

    def test_digest_content_and_my_reports_page(self):
        Deal.objects.create(org=self.org, pipeline=self.pipeline, stage=self.stages[0], title="Closing soon", value=500,
                            owner=self.agent.user, expected_close_date=timezone.localdate() + timedelta(days=2))
        self.login(self.agent)
        response = self.client.post(reverse("reports:my_reports"), {"kind": "summary", "frequency": "daily", "weekday": 6, "hour": 9,
                                                                   "is_active": "on"})
        self.assertIn("kind", response.context["form"].errors)  # agents can't pick the team summary
        self.client.post(reverse("reports:my_reports"), {"kind": "digest", "frequency": "daily", "weekday": 6, "hour": 9, "is_active": "on"})
        sub = ReportSubscription.objects.get(user=self.agent.user)
        self.client.post(reverse("reports:my_reports"), {"action": "send", "pk": sub.pk})
        self.assertIn("Closing soon", mail.outbox[-1].body)
        other = ReportSubscription.objects.create(org=self.org, user=self.manager.user, kind="digest", frequency="weekly")
        self.assertEqual(self.client.post(reverse("reports:my_reports"), {"action": "delete", "pk": other.pk}).status_code, 404)


class FilterRenderTests(MetricsBase):
    def test_selected_team_and_inclusive_end_date_render(self):
        team = Team.objects.create(org=self.org, name="Field")
        self.login(self.agent)
        html = self.client.get(reverse("reports:overview"), {"owner": f"team-{team.pk}", "period": "custom",
                                                             "start": "2026-10-01", "end": "2026-10-15"}).content.decode()
        self.assertIn(f'value="team-{team.pk}" selected', html)
        self.assertIn("01 Oct 2026 – 15 Oct 2026", html)
