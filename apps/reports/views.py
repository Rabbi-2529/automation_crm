import json
from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View
from django.views.generic import DeleteView

from apps.contacts.importer import csv_stream
from apps.orgs.mixins import ManagerRequiredMixin, OrgMemberRequiredMixin, OrgScopedQuerysetMixin
from apps.orgs.models import Membership, Role, Team
from apps.pipeline.models import DealStatus, Pipeline
from apps.pipeline.services import default_pipeline

from . import emails, metrics
from .forms import SubscriptionForm, TargetForm
from .models import ReportSubscription, SalesTarget
from .targets import actual, team_targets


def scope_from_request(request):
    """Period / pipeline / owner filters from the query string, all org-scoped."""
    params = request.GET
    period = metrics.resolve_period(params.get("period", "this_month"), parse_date(params.get("start", "") or ""),
                                    parse_date(params.get("end", "") or ""))
    pipelines = Pipeline.objects.for_org(request.org)
    pipeline = pipelines.filter(pk=params.get("pipeline") or 0).first() if params.get("pipeline", "").isdigit() else None
    if params.get("pipeline") != "all" and pipeline is None:
        pipeline = default_pipeline(request.org)
    owner, owner_ids = params.get("owner", ""), []
    if owner == "me":
        owner_ids = [request.user.pk]
    elif owner.startswith("team-") and owner[5:].isdigit():
        team = Team.objects.for_org(request.org).filter(pk=owner[5:]).first()
        owner_ids = list(team.members.values_list("user_id", flat=True)) if team else [0]
    elif owner.isdigit():
        owner_ids = list(Membership.objects.filter(org=request.org, user_id=owner).values_list("user_id", flat=True)) or [0]
    return metrics.Scope(org=request.org, period=period, pipeline=pipeline, owner_ids=owner_ids)


def filter_context(request, scope):
    return {
        "periods": metrics.PERIODS, "period": scope.period, "pipelines": Pipeline.objects.for_org(request.org),
        "pipeline": scope.pipeline, "users": metrics.org_users(request.org), "teams": Team.objects.for_org(request.org),
        "filters": request.GET,
    }


class ReportsDashboardView(OrgMemberRequiredMixin, View):
    template_name = "reports/dashboard.html"

    def get(self, request):
        scope = scope_from_request(request)
        revenue = metrics.revenue_over_time(scope)
        forecast = metrics.forecast(scope)
        users = metrics.org_users(request.org)
        if scope.owner_ids:
            users = [u for u in users if u.pk in scope.owner_ids]
        charts = {
            "revenue": revenue, "funnel": metrics.funnel(scope), "forecast": forecast,
            "lost": metrics.lost_reasons(scope), "sources": metrics.lead_sources(scope), "currency": request.org.currency,
        }
        return render(request, self.template_name, {
            **filter_context(request, scope), **charts, "kpis": metrics.kpis(scope), "targets": team_targets(scope),
            "leaders": metrics.leaderboard(scope, users)[:5],
            "chart_data": json.loads(json.dumps(charts, cls=DjangoJSONEncoder)),
        })


class LeaderboardView(OrgMemberRequiredMixin, View):
    template_name = "reports/leaderboard.html"

    def get(self, request):
        scope = scope_from_request(request)
        users = metrics.org_users(request.org)
        if scope.owner_ids:
            users = [u for u in users if u.pk in scope.owner_ids]
        return render(request, self.template_name, {**filter_context(request, scope), "rows": metrics.leaderboard(scope, users)})


class LeaderboardExportView(ManagerRequiredMixin, View):
    def get(self, request):
        scope = scope_from_request(request)
        rows = metrics.leaderboard(scope, metrics.org_users(request.org))

        def generate():
            yield ["Rep", "Email", "Revenue won", "Deals won", "Deals lost", "Win rate %", "Open pipeline", "Open deals",
                   "New deals", "Emails sent", "SMS sent", "Calls", "Tasks completed", "Target", "Attainment %"]
            for r in rows:
                target = r["target"] or {}
                yield [r["user"].display_name, r["user"].email, r["revenue"], r["won"], r["lost"], r["win_rate"] if r["win_rate"] is not None else "",
                       r["open_value"], r["open_count"], r["new_deals"], r["emails"], r["sms"], r["calls"], r["tasks_done"],
                       target.get("target", ""), target.get("percent", "")]
        return _csv(generate(), f"leaderboard-{scope.period.start:%Y%m%d}")


class ClosedDealsExportView(ManagerRequiredMixin, View):
    def get(self, request):
        scope = scope_from_request(request)
        deals = (scope.deals().filter(scope.in_period("closed_at")).exclude(status=DealStatus.OPEN)
                 .select_related("owner", "contact", "company", "stage", "pipeline", "lost_reason").order_by("closed_at"))

        def generate():
            yield ["Deal", "Status", "Value", "Closed", "Created", "Days to close", "Owner", "Contact", "Company", "Pipeline",
                   "Last stage", "Lost reason"]
            for d in deals.iterator(chunk_size=500):
                yield [d.title, d.get_status_display(), d.value, timezone.localtime(d.closed_at).date(), timezone.localtime(d.created_at).date(),
                       (d.closed_at - d.created_at).days, d.owner.display_name if d.owner else "", d.contact.display_name if d.contact else "",
                       d.company.name if d.company else "", d.pipeline.name, d.stage.name, d.lost_reason.name if d.lost_reason else ""]
        return _csv(generate(), f"closed-deals-{scope.period.start:%Y%m%d}")


def _csv(rows, name):
    response = StreamingHttpResponse(csv_stream(rows), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{name}.csv"'
    return response


# --- Targets -----------------------------------------------------------------------------------------------------------


class TargetListView(ManagerRequiredMixin, View):
    template_name = "reports/targets.html"

    def get(self, request):
        today = timezone.localdate()
        month = parse_date((request.GET.get("month") or "") + "-01") if request.GET.get("month") else None
        month = month or date(today.year, today.month, 1)
        targets = SalesTarget.objects.for_org(request.org).filter(period_start__gte=date(month.year, 1, 1),
                                                                  period_start__lt=date(month.year + 1, 1, 1)).select_related("user")
        rows = []
        for target in targets:
            start = target.period_start
            end = metrics._add_months(start, 1 if target.period == "month" else 3)
            value = actual(request.org, target.metric, metrics._aware(start), metrics._aware(end), user=target.user)
            rows.append({"target": target, "actual": value, "percent": round(100 * value / target.value) if target.value else None,
                         "is_current": start <= today < end})
        return render(request, self.template_name, {"rows": rows, "year": month.year, "form": TargetForm(org=request.org)})


class TargetEditView(ManagerRequiredMixin, View):
    template_name = "reports/target_form.html"

    def get_target(self):
        if pk := self.kwargs.get("pk"):
            return get_object_or_404(SalesTarget.objects.for_org(self.request.org), pk=pk)
        return SalesTarget(org=self.request.org)

    def get(self, request, pk=None):
        target = self.get_target()
        today = timezone.localdate()
        return render(request, self.template_name, {"target": target, "form": TargetForm(
            instance=target, org=request.org, initial={"month": target.period_start or date(today.year, today.month, 1)})})

    def post(self, request, pk=None):
        target = self.get_target()
        form = TargetForm(request.POST, instance=target, org=request.org)
        if not form.is_valid():
            return render(request, self.template_name, {"target": target, "form": form})
        form.instance.created_by = form.instance.created_by or request.user
        form.save()
        messages.success(request, "Target saved.")
        return redirect("reports:targets")


class TargetDeleteView(ManagerRequiredMixin, OrgScopedQuerysetMixin, DeleteView):
    model = SalesTarget
    template_name = "contacts/confirm_delete.html"
    success_url = reverse_lazy("reports:targets")


# --- Report emails -----------------------------------------------------------------------------------------------------


class MyReportsView(OrgMemberRequiredMixin, View):
    template_name = "reports/my_reports.html"

    def form(self, data=None, instance=None):
        return SubscriptionForm(data, instance=instance, org=self.request.org, user=self.request.user,
                                can_summary=self.request.membership.has_role(Role.MANAGER))

    def render_page(self, form=None):
        subs = ReportSubscription.objects.for_org(self.request.org).filter(user=self.request.user)
        return render(self.request, self.template_name, {"subscriptions": subs, "form": form or self.form()})

    def get(self, request):
        return self.render_page()

    def post(self, request):
        action = request.POST.get("action")
        if action in ("delete", "send", "toggle"):
            sub = get_object_or_404(ReportSubscription.objects.for_org(request.org), pk=request.POST.get("pk"), user=request.user)
            if action == "delete":
                sub.delete()
                messages.info(request, "Report email removed.")
            elif action == "toggle":
                sub.is_active = not sub.is_active
                sub.save(update_fields=["is_active", "updated_at"])
            else:
                try:
                    emails.build(sub).send(fail_silently=False)
                    messages.success(request, f"Sent “{sub.get_kind_display()}” to {request.user.email}.")
                except Exception as exc:  # noqa: BLE001 - mail server problems are shown, not a 500
                    messages.error(request, f"Couldn't send: {type(exc).__name__}: {exc}")
            return redirect("reports:my_reports")
        form = self.form(request.POST)
        if not form.is_valid():
            return self.render_page(form)
        if form.cleaned_data["kind"] == "summary" and not request.membership.has_role(Role.MANAGER):
            raise PermissionDenied
        form.save()
        messages.success(request, "Report email scheduled.")
        return redirect("reports:my_reports")

