import json

from django.contrib import messages
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import DeleteView, DetailView, ListView

from apps.contacts.models import Contact, Source
from apps.core.widgets import LookupWidget
from apps.orgs.mixins import ManagerRequiredMixin, OrgMemberRequiredMixin, OrgScopedQuerysetMixin

from . import engine
from .describe import RECIPES, Describer
from .models import RunStatus, Workflow, WorkflowAction, WorkflowRun
from .registry import ACTIONS, TRIGGERS, ConfigError, builder_schema, validate_conditions, validate_config

MAX_ACTIONS = 25


def workflow_definition(workflow):
    return {
        "name": workflow.name, "description": workflow.description, "is_active": workflow.is_active,
        "allow_reenroll": workflow.allow_reenroll, "trigger_type": workflow.trigger_type,
        "trigger_config": workflow.trigger_config, "conditions": workflow.conditions or {"match": "all", "rules": []},
        "actions": [{"type": a.action_type, "config": a.config} for a in workflow.actions.order_by("position", "pk")],
    }


def clean_definition(raw, org):
    """Validates the builder's JSON. Returns (clean dict, [errors])."""
    errors = []
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return None, ["The workflow couldn't be read. Reload the page and try again."]
    name = str(data.get("name", "")).strip()[:120]
    if not name:
        errors.append("Give the workflow a name.")
    trigger_type = data.get("trigger_type")
    clean = {"name": name, "description": str(data.get("description", "")).strip()[:255],
             "is_active": bool(data.get("is_active")), "allow_reenroll": bool(data.get("allow_reenroll")),
             "trigger_type": trigger_type, "trigger_config": {}, "conditions": {"match": "all", "rules": []}, "actions": []}
    if trigger_type not in TRIGGERS:
        errors.append("Pick a trigger.")
    else:
        try:
            clean["trigger_config"] = validate_config(TRIGGERS[trigger_type].config, data.get("trigger_config"), org, "Trigger")
        except ConfigError as exc:
            errors.extend(exc.errors)
    try:
        clean["conditions"] = validate_conditions(data.get("conditions"), org)
    except ConfigError as exc:
        errors.extend(exc.errors)
    raw_actions = data.get("actions") or []
    if not raw_actions:
        errors.append("Add at least one action.")
    if len(raw_actions) > MAX_ACTIONS:
        errors.append(f"A workflow can have at most {MAX_ACTIONS} actions.")
    subject = TRIGGERS[trigger_type].subject if trigger_type in TRIGGERS else "contact"
    for i, item in enumerate(raw_actions[:MAX_ACTIONS], start=1):
        action_type = (item or {}).get("type")
        if action_type not in ACTIONS:
            errors.append(f"Step {i}: pick an action.")
            continue
        spec = ACTIONS[action_type]
        if spec.needs == "deal" and subject != "deal" and not any(a.get("type") == "create_deal" for a in raw_actions[:i - 1]):
            errors.append(f"Step {i}: “{spec.label}” needs a deal trigger (or a “Create a deal” step before it).")
        try:
            clean["actions"].append({"type": action_type, "config": validate_config(spec.config, item.get("config"), org, f"Step {i}")})
        except ConfigError as exc:
            errors.extend(exc.errors)
    return clean, errors


@transaction.atomic
def save_definition(workflow, clean):
    for name in ("name", "description", "is_active", "allow_reenroll", "trigger_type", "trigger_config", "conditions"):
        setattr(workflow, name, clean[name])
    workflow.save()
    existing = {(a.position, a.action_type): a.state for a in workflow.actions.all()}
    workflow.actions.all().delete()
    WorkflowAction.objects.bulk_create(
        WorkflowAction(workflow=workflow, position=i, action_type=a["type"], config=a["config"],
                       state=existing.get((i, a["type"]), {}))  # keep e.g. the round-robin pointer
        for i, a in enumerate(clean["actions"])
    )


class WorkflowListView(OrgMemberRequiredMixin, ListView):
    template_name = "automation/workflow_list.html"
    context_object_name = "workflows"

    def get_queryset(self):
        return Workflow.objects.for_org(self.request.org).select_related("created_by").prefetch_related("actions")

    def get_context_data(self, **kwargs):
        return super().get_context_data(triggers=TRIGGERS, recipes=RECIPES, **kwargs)


class WorkflowBuilderView(ManagerRequiredMixin, View):
    template_name = "automation/builder.html"

    def get_workflow(self):
        if pk := self.kwargs.get("pk"):
            return get_object_or_404(Workflow.objects.for_org(self.request.org), pk=pk)
        return None

    def render_page(self, workflow, definition, errors=()):
        return render(self.request, self.template_name, {
            "workflow": workflow, "definition": definition, "schema": builder_schema(self.request.org), "errors": errors,
        })

    def get(self, request, pk=None):
        workflow = self.get_workflow()
        if workflow:
            definition = workflow_definition(workflow)
        else:
            recipe = RECIPES.get(request.GET.get("recipe"), {})
            definition = {"name": "", "description": "", "is_active": False, "allow_reenroll": False, "trigger_type": "",
                          "trigger_config": {}, "conditions": {"match": "all", "rules": []}, "actions": [],
                          **{k: v for k, v in recipe.items()}}
        return self.render_page(workflow, definition)

    def post(self, request, pk=None):
        workflow = self.get_workflow()
        clean, errors = clean_definition(request.POST.get("definition"), request.org)
        if errors:
            try:
                posted = json.loads(request.POST.get("definition") or "{}")
            except ValueError:
                posted = workflow_definition(workflow) if workflow else {}
            return self.render_page(workflow, posted, errors)
        workflow = workflow or Workflow(org=request.org, created_by=request.user)
        save_definition(workflow, clean)
        messages.success(request, f"Workflow “{workflow.name}” saved{' and active' if workflow.is_active else ' (inactive)'}.")
        return redirect("automation:workflow_detail", pk=workflow.pk)


class WorkflowDetailView(OrgMemberRequiredMixin, OrgScopedQuerysetMixin, DetailView):
    model = Workflow
    template_name = "automation/workflow_detail.html"
    context_object_name = "workflow"

    def get_context_data(self, **kwargs):
        workflow, describer = self.object, Describer(self.request.org)
        test_field = LookupWidget(reverse_lazy("contacts:contact_lookup"), placeholder="Pick a contact to run this on…")
        runs = workflow.runs.select_related("contact", "deal")[:200]
        counts = {s: 0 for s in RunStatus.values}
        for status in workflow.runs.values_list("status", flat=True):
            counts[status] += 1
        return super().get_context_data(
            trigger_text=describer.trigger(workflow), conditions_text=describer.conditions(workflow.conditions),
            action_texts=[(a, describer.action(a)) for a in workflow.actions.order_by("position", "pk")],
            runs=runs, counts=counts, test_picker=test_field.render("contact", None, {"id": "test-contact"}),
            webhook_url=self.request.build_absolute_uri(reverse("automation:webhook", args=[workflow.webhook_token])),
            **kwargs,
        )


class WorkflowToggleView(ManagerRequiredMixin, View):
    def post(self, request, pk):
        workflow = get_object_or_404(Workflow.objects.for_org(request.org), pk=pk)
        workflow.is_active = not workflow.is_active
        workflow.save(update_fields=["is_active", "updated_at"])
        if workflow.is_active:
            messages.success(request, f"“{workflow.name}” is live.")
        else:
            waiting = workflow.runs.filter(status=RunStatus.WAITING).count()
            messages.info(request, f"“{workflow.name}” paused." + (f" {waiting} waiting run(s) will continue if you turn it back on." if waiting else ""))
        return redirect(request.POST.get("next") or reverse("automation:workflow_detail", args=[workflow.pk]))


class WorkflowTestRunView(ManagerRequiredMixin, View):
    """Runs the workflow now on one contact (re-enrollment rule ignored, conditions still apply)."""

    def post(self, request, pk):
        workflow = get_object_or_404(Workflow.objects.for_org(request.org).select_related("org", "created_by"), pk=pk)
        contact = get_object_or_404(Contact.objects.for_org(request.org), pk=request.POST.get("contact") or 0)
        deal = contact.deals.filter(status="open").order_by("-created_at").first() if TRIGGERS[workflow.trigger_type].subject == "deal" else None
        if TRIGGERS[workflow.trigger_type].subject == "deal" and not deal:
            messages.error(request, f"{contact.display_name} has no open deal to run this deal workflow on.")
            return redirect("automation:workflow_detail", pk=pk)
        run = engine.enroll(workflow, contact=contact, deal=deal, summary=f"Test run by {request.user.display_name}", force=True)
        if run is None:
            messages.warning(request, f"{contact.display_name} doesn't meet this workflow's conditions, so nothing ran.")
            return redirect("automation:workflow_detail", pk=pk)
        messages.success(request, f"Ran on {contact.display_name}: {run.get_status_display().lower()}.")
        return redirect("automation:run_detail", pk=run.pk)


class WorkflowDeleteView(ManagerRequiredMixin, OrgScopedQuerysetMixin, DeleteView):
    model = Workflow
    template_name = "contacts/confirm_delete.html"
    success_url = reverse_lazy("automation:workflow_list")
    extra_context = {"warning": "Its run history is deleted too. Pause it instead if you might need it again."}


class RunDetailView(OrgMemberRequiredMixin, DetailView):
    template_name = "automation/run_detail.html"
    context_object_name = "run"

    def get_queryset(self):
        return WorkflowRun.objects.for_org(self.request.org).select_related("workflow", "contact", "deal")

    def get_context_data(self, **kwargs):
        describer = Describer(self.request.org)
        steps = {s.position: s for s in self.object.steps.all()}
        plan = [(i, action, describer.action(action), steps.get(i))
                for i, action in enumerate(self.object.workflow.actions.order_by("position", "pk"), start=1)]
        return super().get_context_data(plan=plan, **kwargs)


class RunCancelView(ManagerRequiredMixin, View):
    def post(self, request, pk):
        run = get_object_or_404(WorkflowRun.objects.for_org(request.org), pk=pk)
        if run.is_open:
            WorkflowRun.objects.filter(pk=run.pk).update(status=RunStatus.CANCELLED, resume_at=None)
            messages.info(request, "Run cancelled.")
        return redirect("automation:run_detail", pk=pk)


@method_decorator(csrf_exempt, name="dispatch")
class WebhookTriggerView(View):
    """Public: POST JSON {email|phone, first_name, last_name, job_title, company?} to start an 'Incoming webhook' workflow."""

    def post(self, request, token):
        workflow = Workflow.objects.filter(webhook_token=token, trigger_type="webhook").select_related("org", "created_by").first()
        if workflow is None:
            raise Http404
        if not workflow.is_active:
            return JsonResponse({"ok": False, "error": "Workflow is paused"}, status=409)
        try:
            payload = json.loads(request.body or b"{}")
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            return JsonResponse({"ok": False, "error": "Send a JSON object"}, status=400)
        email = str(payload.get("email", "")).strip().lower()[:254]
        phone = str(payload.get("phone", "")).strip()[:32]
        if not email and not phone:
            return JsonResponse({"ok": False, "error": "email or phone is required"}, status=400)
        contacts = Contact.objects.for_org(workflow.org)
        contact = (contacts.filter(email=email).first() if email else None) or (contacts.filter(phone=phone).first() if phone else None)
        created = contact is None
        if created:
            contact = Contact(org=workflow.org, email=email, phone=phone, source=Source.API, owner=workflow.created_by)
            for field in ("first_name", "last_name", "job_title"):
                setattr(contact, field, str(payload.get(field, "")).strip()[:100])
            contact.save()
        run = engine.enroll(workflow, contact=contact, summary="Incoming webhook")
        return JsonResponse({"ok": True, "contact": contact.pk, "created": created, "run": run.pk if run else None,
                             "status": run.status if run else "not enrolled"}, status=201 if created else 200)
