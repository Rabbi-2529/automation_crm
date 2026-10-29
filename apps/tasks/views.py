from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import DeleteView, TemplateView, UpdateView

from apps.contacts.forms import org_users
from apps.orgs.mixins import OrgMemberRequiredMixin
from apps.orgs.models import Role

from . import services
from .forms import QuickTaskForm, TaskForm
from .models import Task, today_bounds


def safe_next(request, fallback):
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return target
    return fallback


def can_change(request, task):
    return request.user.pk in (task.assignee_id, task.created_by_id) or request.membership.has_role(Role.MANAGER)


def task_queryset(request):
    return Task.objects.for_org(request.org).select_related("assignee", "contact", "company", "deal")


class TaskListView(OrgMemberRequiredMixin, TemplateView):
    template_name = "tasks/task_list.html"

    def get_context_data(self, **kwargs):
        request = self.request
        assignee = request.GET.get("assignee", "me")
        tasks = task_queryset(request)
        if assignee == "me":
            tasks = tasks.filter(assignee=request.user)
        elif assignee == "none":
            tasks = tasks.filter(assignee__isnull=True)
        elif assignee.isdigit():
            tasks = tasks.filter(assignee__pk=assignee)
        show_done = request.GET.get("show") == "done"
        context = {"assignee": assignee, "show_done": show_done, "users": org_users(request.org),
                   "quick_form": QuickTaskForm(org=request.org, initial={"assignee": request.user.pk})}
        if show_done:
            context["done"] = tasks.filter(completed_at__isnull=False).order_by("-completed_at")[:100]
        else:
            open_tasks = tasks.open()
            now = timezone.now()
            start, end = today_bounds()
            context["groups"] = [
                ("Overdue", "overdue", open_tasks.filter(due_at__lt=now)),
                ("Today", "today", open_tasks.filter(due_at__gte=now, due_at__lt=end)),
                ("Next 7 days", "week", open_tasks.filter(due_at__gte=end, due_at__lt=end + timedelta(days=7))),
                ("Later", "later", open_tasks.filter(due_at__gte=end + timedelta(days=7))),
                ("No due date", "nodate", open_tasks.filter(due_at__isnull=True)),
            ]
        return super().get_context_data(**context, **kwargs)


class TaskCreateView(OrgMemberRequiredMixin, View):
    """Full form on GET; POST also accepts the quick-add box (title + date) from lists and record pages."""

    template_name = "tasks/task_form.html"

    def initial(self):
        initial = {"assignee": self.request.user.pk}
        if due := parse_date(self.request.GET.get("due_date", "")):
            initial["due_date"] = due  # from clicking a day on the calendar
        for name in ("contact", "company", "deal"):
            if self.request.GET.get(name, "").isdigit():
                initial[name] = self.request.GET[name]
        return initial

    def get(self, request):
        form = TaskForm(org=request.org, initial=self.initial())
        return render(request, self.template_name, {"form": form, "next": safe_next(request, "")})

    def post(self, request):
        form_class = QuickTaskForm if request.POST.get("quick") else TaskForm
        form = form_class(request.POST, org=request.org)
        fallback = reverse("tasks:task_list")
        if not form.is_valid():
            if form_class is QuickTaskForm:
                messages.error(request, "Couldn't add the task: " + " ".join(e for errs in form.errors.values() for e in errs))
                return redirect(safe_next(request, fallback))
            return render(request, self.template_name, {"form": form, "next": safe_next(request, "")})
        form.instance.created_by = request.user
        task = form.save()
        services.task_created(task, request.user)
        messages.success(request, f"Task “{task.title}” added.")
        return redirect(safe_next(request, fallback))


class TaskUpdateView(OrgMemberRequiredMixin, UpdateView):
    form_class = TaskForm
    template_name = "tasks/task_form.html"

    def get_queryset(self):
        return task_queryset(self.request)

    def get_object(self, queryset=None):
        task = super().get_object(queryset)
        if not can_change(self.request, task):
            raise PermissionDenied
        return task

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "org": self.request.org}

    def get_context_data(self, **kwargs):
        return super().get_context_data(next=safe_next(self.request, ""), **kwargs)

    def form_valid(self, form):
        form.save()
        messages.success(self.request, "Task saved.")
        return redirect(safe_next(self.request, reverse("tasks:task_list")))


class TaskDeleteView(OrgMemberRequiredMixin, DeleteView):
    template_name = "contacts/confirm_delete.html"

    def get_queryset(self):
        return task_queryset(self.request)

    def get_object(self, queryset=None):
        task = super().get_object(queryset)
        if not can_change(self.request, task):
            raise PermissionDenied
        return task

    def get_success_url(self):
        return safe_next(self.request, reverse("tasks:task_list"))


class TaskToggleView(OrgMemberRequiredMixin, View):
    """Tick / untick. Anyone in the org can complete a task (covering for a colleague is normal)."""

    def post(self, request, pk):
        task = get_object_or_404(task_queryset(request), pk=pk)
        if task.is_done:
            services.reopen(task)
        else:
            services.complete(task, request.user)
        if request.headers.get("HX-Request"):
            return render(request, "tasks/partials/task_row.html", {"task": task, "next": safe_next(request, "")})
        return redirect(safe_next(request, reverse("tasks:task_list")))
