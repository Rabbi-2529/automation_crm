from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.db.models import Count, Sum
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from apps.activities.models import Activity
from apps.contacts.models import Company, Contact, LifecycleStage
from apps.pipeline.models import Deal, DealStatus
from apps.pipeline.services import stage_totals
from apps.tasks.models import Task, today_bounds
from apps.orgs.mixins import AdminRequiredMixin, OrgMemberRequiredMixin
from apps.orgs.models import Membership, Team

from .tasks import ping

ROADMAP = [
    ("Phase 0", "Foundation: auth, workspaces, roles, teams, background jobs", True),
    ("Phase 1", "Contacts, companies, tags, custom fields, timeline, CSV import", True),
    ("Phase 2", "Pipelines, deals, kanban, tasks & reminders", True),
    ("Phase 3", "Email & SMS, templates, inbox", False),
    ("Phase 4", "Automation engine: triggers → conditions → actions", True),
    ("Phase 5", "Lead & communication automation: forms, scoring, segments, sequences, broadcasts", True),
    ("Phase 6", "Sales & reporting automation: dashboards, funnel, forecast, targets, scheduled reports", True),
    ("Phase 7", "REST API, integrations (WhatsApp, Facebook lead ads)", False),
    ("Phase 8", "Hardening, backups, deploy", False),
]


class DashboardView(OrgMemberRequiredMixin, TemplateView):
    template_name = "core/dashboard.html"

    def get_context_data(self, **kwargs):
        org = self.request.org
        contacts = Contact.objects.for_org(org)
        stage_counts = dict(contacts.values_list("lifecycle_stage").annotate(n=Count("pk")))
        week_ago = timezone.now() - timedelta(days=7)
        month_start = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        deals = Deal.objects.for_org(org)
        open_totals = stage_totals(None, deals).values()
        won_month = deals.filter(status=DealStatus.WON, closed_at__gte=month_start).aggregate(n=Count("pk"), v=Sum("value"))
        _, end_of_today = today_bounds()
        my_tasks = Task.objects.for_org(org).open().filter(assignee=self.request.user).select_related("contact", "company", "deal")
        return super().get_context_data(
            member_count=Membership.objects.filter(org=org, is_active=True).count(),
            team_count=Team.objects.for_org(org).count(),
            contact_count=contacts.count(),
            new_contacts_week=contacts.filter(created_at__gte=week_ago).count(),
            my_contact_count=contacts.filter(owner=self.request.user).count(),
            company_count=Company.objects.for_org(org).count(),
            stages=[(value, label, stage_counts.get(value, 0)) for value, label in LifecycleStage.choices],
            recent_activity=Activity.objects.for_org(org).select_related("actor", "contact", "company", "deal")[:12],
            open_deal_count=sum(row["count"] for row in open_totals),
            open_deal_value=sum((row["value"] for row in open_totals), 0),
            weighted_value=sum((row["weighted"] for row in open_totals), 0),
            won_month_count=won_month["n"], won_month_value=won_month["v"] or 0,
            my_tasks_due=my_tasks.filter(due_at__lt=end_of_today)[:8],
            my_tasks_overdue=my_tasks.filter(due_at__lt=timezone.now()).count(),
            roadmap=ROADMAP,
            **kwargs,
        )


def check_database():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return True, connection.vendor
    except Exception as exc:  # noqa: BLE001 - surfaced in the health payload
        return False, str(exc)


class HealthView(View):
    """Unauthenticated liveness probe for Docker/Nginx."""

    def get(self, request):
        db_ok, _ = check_database()
        return JsonResponse({"status": "ok" if db_ok else "error", "database": db_ok}, status=200 if db_ok else 503)


class SystemStatusView(AdminRequiredMixin, TemplateView):
    template_name = "core/system_status.html"

    def get_context_data(self, **kwargs):
        db_ok, db_detail = check_database()
        return super().get_context_data(
            db_ok=db_ok,
            db_detail=db_detail,
            broker=settings.CELERY_BROKER_URL.split("@")[-1],
            eager=settings.CELERY_TASK_ALWAYS_EAGER,
            **kwargs,
        )


class RunPingTaskView(AdminRequiredMixin, View):
    """Queues the ping task and waits briefly for the worker's answer (HTMX partial)."""

    def post(self, request):
        try:
            result = ping.delay().get(timeout=5)
            context = {"ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001 - broker down / no worker is the thing we report
            context = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return render(request, "core/partials/ping_result.html", context)
