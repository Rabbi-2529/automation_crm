from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render
from django.views import View
from django.views.generic import TemplateView

from apps.orgs.mixins import AdminRequiredMixin, OrgMemberRequiredMixin
from apps.orgs.models import Membership, Team

from .tasks import ping

ROADMAP = [
    ("Phase 1", "Contacts, companies, tags, custom fields, timeline"),
    ("Phase 2", "Pipelines, deals, kanban, tasks"),
    ("Phase 3", "Email & SMS, templates, inbox"),
    ("Phase 4", "Automation engine: triggers → conditions → actions"),
    ("Phase 5", "Web forms, lead scoring, drip campaigns"),
    ("Phase 6", "Reports & dashboards"),
    ("Phase 7", "REST API, webhooks, integrations"),
    ("Phase 8", "Hardening, backups, deploy"),
]


class DashboardView(OrgMemberRequiredMixin, TemplateView):
    template_name = "core/dashboard.html"

    def get_context_data(self, **kwargs):
        org = self.request.org
        return super().get_context_data(
            member_count=Membership.objects.filter(org=org, is_active=True).count(),
            team_count=Team.objects.for_org(org).count(),
            my_teams=self.request.membership.teams.all(),
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
