"""Calendar page (FullCalendar) showing tasks by due time and open deals by expected close date."""

import json
from datetime import datetime, timedelta

from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views import View
from django.views.generic import TemplateView

from apps.activities.services import record_changes, snapshot
from apps.contacts.forms import org_users
from apps.orgs.mixins import OrgMemberRequiredMixin
from apps.pipeline.models import Deal, DealStatus

from .forms import DEFAULT_DUE_TIME
from .models import Priority
from .views import can_change, task_queryset

MAX_RANGE = timedelta(days=62)  # a month view plus padding weeks


def _parse_range(params):
    start, end = parse_datetime(params.get("start", "")), parse_datetime(params.get("end", ""))
    if not start or not end or end <= start or end - start > MAX_RANGE:
        return None
    to_aware = lambda d: d if timezone.is_aware(d) else timezone.make_aware(d)  # noqa: E731
    return to_aware(start), to_aware(end)


class CalendarView(OrgMemberRequiredMixin, TemplateView):
    template_name = "tasks/calendar.html"

    def get_context_data(self, **kwargs):
        return super().get_context_data(users=org_users(self.request.org), **kwargs)


class CalendarEventsView(OrgMemberRequiredMixin, View):
    """FullCalendar JSON feed: ?start=&end=&assignee=me|all|<id>&show=tasks,deals"""

    def get(self, request):
        window = _parse_range(request.GET)
        if window is None:
            return HttpResponseBadRequest("start/end missing or range too large")
        start, end = window
        assignee = request.GET.get("assignee", "me")
        show = set(request.GET.get("show", "tasks,deals").split(","))
        events = []

        if "tasks" in show:
            tasks = task_queryset(request).filter(due_at__gte=start, due_at__lt=end)
            if request.GET.get("done") != "1":
                tasks = tasks.filter(completed_at__isnull=True)
            tasks = self._by_owner(tasks, "assignee", assignee, request.user)
            for task in tasks:
                css = "crm-task-done" if task.is_done else ("crm-task-high" if task.priority == Priority.HIGH else "crm-task")
                events.append({
                    "id": f"task-{task.pk}", "title": task.title, "start": task.due_at.isoformat(),
                    "url": reverse("tasks:task_edit", args=[task.pk]) + "?next=" + reverse("tasks:calendar"),
                    "classNames": [css], "editable": not task.is_done and can_change(request, task),
                    "extendedProps": {"kind": "task", "pk": task.pk, "related": str(task.related or "")},
                })

        if "deals" in show:
            deals = Deal.objects.for_org(request.org).filter(
                status=DealStatus.OPEN, expected_close_date__gte=start.date(), expected_close_date__lt=end.date() + timedelta(days=1),
            ).select_related("org")
            deals = self._by_owner(deals, "owner", assignee, request.user)
            for deal in deals:
                events.append({
                    "id": f"deal-{deal.pk}", "title": f"Close: {deal.title} ({deal.currency_code} {deal.value:,.0f})",
                    "start": deal.expected_close_date.isoformat(), "allDay": True,
                    "url": reverse("pipeline:deal_detail", args=[deal.pk]), "classNames": ["crm-deal"], "editable": True,
                    "extendedProps": {"kind": "deal", "pk": deal.pk},
                })
        return JsonResponse(events, safe=False)

    @staticmethod
    def _by_owner(queryset, field, assignee, user):
        if assignee == "me":
            return queryset.filter(**{field: user})
        if assignee.isdigit():
            return queryset.filter(**{f"{field}__pk": assignee})
        return queryset


class CalendarMoveView(OrgMemberRequiredMixin, View):
    """Drag-and-drop on the calendar: {"kind": "task"|"deal", "pk", "start": ISO}."""

    def post(self, request):
        try:
            payload = json.loads(request.body)
            kind, pk, start = payload["kind"], int(payload["pk"]), payload["start"]
        except (ValueError, KeyError, TypeError):
            return HttpResponseBadRequest("Bad payload")
        if kind == "task":
            task = get_object_or_404(task_queryset(request), pk=pk)
            if task.is_done or not can_change(request, task):
                return HttpResponseBadRequest("Can't move this task")
            if payload.get("allDay"):  # dropped on a day in month view: keep the task's time of day
                day = parse_date(start[:10])
                old_time = timezone.localtime(task.due_at).time() if task.due_at else DEFAULT_DUE_TIME
                moved = timezone.make_aware(datetime.combine(day, old_time)) if day else None
            else:
                moved = parse_datetime(start)
                if moved is not None and timezone.is_naive(moved):
                    moved = timezone.make_aware(moved)
            if moved is None:
                return HttpResponseBadRequest("Bad date")
            task.due_at = moved
            task.reminder_sent_at = None
            task.save()  # recomputes remind_at
            return JsonResponse({"ok": True, "due_at": task.due_at.isoformat()})
        if kind == "deal":
            deal = get_object_or_404(Deal.objects.for_org(request.org), pk=pk, status=DealStatus.OPEN)
            day = parse_date(start[:10])
            if day is None:
                return HttpResponseBadRequest("Bad date")
            before = snapshot(deal, ["expected_close_date"])
            deal.expected_close_date = day
            deal.save(update_fields=["expected_close_date", "updated_at"])
            record_changes(instance=deal, before=before, actor=request.user, target_kw={"deal": deal})
            return JsonResponse({"ok": True, "expected_close_date": day.isoformat()})
        return HttpResponseBadRequest("Unknown kind")
