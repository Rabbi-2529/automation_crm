import json

from django import forms
from django.contrib import messages
from django.core.cache import cache
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import DeleteView, DetailView, ListView

from apps.automation.registry import ConfigError, builder_schema, validate_conditions
from apps.orgs.mixins import ManagerRequiredMixin, OrgScopedQuerysetMixin

from . import capture
from .forms import LeadSettingsForm, ScoringRuleForm, WebFormSettingsForm, parse_field_table
from .models import LeadSettings, ScoringRule, WebForm
from .tasks import recompute_scores

RATE_LIMIT = 10  # submissions per IP per form per 10 minutes
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content")


def contact_conditions_schema(org):
    """Builder schema limited to contact fields (segments, scoring rules, sequence exits)."""
    schema = builder_schema(org)
    schema["conditionFields"] = [f for f in schema["conditionFields"] if f["key"].startswith("contact.")]
    return schema


# --- Web forms (management) --------------------------------------------------------------------------------------------


class WebFormListView(ManagerRequiredMixin, ListView):
    template_name = "leads/form_list.html"
    context_object_name = "webforms"

    def get_queryset(self):
        return WebForm.objects.for_org(self.request.org)


class WebFormEditView(ManagerRequiredMixin, View):
    template_name = "leads/form_edit.html"

    def get_webform(self):
        if pk := self.kwargs.get("pk"):
            return get_object_or_404(WebForm.objects.for_org(self.request.org), pk=pk)
        return WebForm(org=self.request.org, fields=[
            {"key": "first_name", "label": "First name", "required": True},
            {"key": "email", "label": "Email", "required": True},
            {"key": "phone", "label": "Phone", "required": False},
            {"key": "message", "label": "How can we help?", "required": False},
        ])

    def field_rows(self, webform, post=None):
        chosen = {f["key"]: (i, f) for i, f in enumerate(webform.fields)}
        rows = []
        for key, label, kind in capture.available_fields(self.request.org):
            slug = key.replace(":", "__")
            position, spec = chosen.get(key, (len(chosen) + len(rows), None))
            if post is not None:
                rows.append({"key": key, "slug": slug, "default_label": label, "kind": kind,
                             "include": bool(post.get(f"include__{slug}")), "label": post.get(f"label__{slug}", label),
                             "required": bool(post.get(f"required__{slug}")), "position": post.get(f"position__{slug}", position)})
            else:
                rows.append({"key": key, "slug": slug, "default_label": label, "kind": kind, "include": spec is not None,
                             "label": (spec or {}).get("label", label), "required": (spec or {}).get("required", False),
                             "position": position})
        return sorted(rows, key=lambda r: (not r["include"], int(r["position"] or 0)))

    def get(self, request, pk=None):
        webform = self.get_webform()
        return render(request, self.template_name, {"webform": webform, "form": WebFormSettingsForm(instance=webform, org=request.org),
                                                    "rows": self.field_rows(webform)})

    def post(self, request, pk=None):
        webform = self.get_webform()
        form = WebFormSettingsForm(request.POST, instance=webform, org=request.org)
        field_error = None
        try:
            fields = parse_field_table(request.POST, request.org)
        except forms.ValidationError as exc:
            field_error, fields = exc.messages[0], None
        if not form.is_valid() or field_error:
            return render(request, self.template_name, {"webform": webform, "form": form, "field_error": field_error,
                                                        "rows": self.field_rows(webform, request.POST)})
        webform = form.save(commit=False)
        webform.fields = fields
        if not webform.pk:
            webform.created_by = request.user
        webform.save()
        form.save_m2m()
        messages.success(request, f"Form “{webform.name}” saved.")
        return redirect("leads:form_detail", pk=webform.pk)


class WebFormDetailView(ManagerRequiredMixin, OrgScopedQuerysetMixin, DetailView):
    model = WebForm
    template_name = "leads/form_detail.html"
    context_object_name = "webform"

    def get_context_data(self, **kwargs):
        url = self.request.build_absolute_uri(reverse("leads:public_form", args=[self.object.token]))
        return super().get_context_data(
            public_url=url, submissions=self.object.submissions.select_related("contact")[:500], **kwargs)


class WebFormDeleteView(ManagerRequiredMixin, OrgScopedQuerysetMixin, DeleteView):
    model = WebForm
    template_name = "contacts/confirm_delete.html"
    success_url = reverse_lazy("leads:form_list")
    extra_context = {"warning": "The public link stops working. Contacts it created are kept."}


# --- Web forms (public) ------------------------------------------------------------------------------------------------


def _client_ip(request):
    return request.META.get("REMOTE_ADDR")


@method_decorator([csrf_exempt, xframe_options_exempt], name="dispatch")
class PublicFormView(View):
    """Hosted form, embeddable in any site with an iframe. No login; protected by honeypot, timing and rate limits."""

    template_name = "leads/public_form.html"

    def get_webform(self, token):
        webform = WebForm.objects.select_related("org").filter(token=token, org__is_active=True).first()
        if webform is None:
            raise Http404
        return webform

    def context(self, request, webform, form, **extra):
        return {"webform": webform, "form": form, "embed": request.GET.get("embed") == "1", "org": webform.org,
                "utm": {k: request.GET.get(k, "") for k in UTM_KEYS if request.GET.get(k)}, **extra}

    def get(self, request, token):
        webform = self.get_webform(token)
        form = capture.form_class(webform)(initial={"ts": capture.render_timestamp()})
        return render(request, self.template_name, self.context(request, webform, form))

    def post(self, request, token):
        webform = self.get_webform(token)
        if not webform.is_active:
            return render(request, self.template_name, self.context(request, webform, None, closed=True))
        key = f"webform:{webform.pk}:{_client_ip(request)}"
        attempts = cache.get_or_set(key, 0, timeout=600)
        if attempts >= RATE_LIMIT:
            return render(request, self.template_name, self.context(request, webform, None, throttled=True), status=429)
        cache.incr(key)

        form = capture.form_class(webform)(request.POST)
        if not form.is_valid():
            form.data = form.data.copy()
            form.data["ts"] = capture.render_timestamp()
            return render(request, self.template_name, self.context(request, webform, form), status=400)
        if not capture.looks_like_spam(form.cleaned_data):
            utm = {k: request.POST.get(k, "")[:100] for k in UTM_KEYS if request.POST.get(k)}
            capture.submit(webform, form.cleaned_data, utm=utm, referrer=request.META.get("HTTP_REFERER", ""), ip=_client_ip(request))
        # Bots get the same thank-you page, so they learn nothing.
        if webform.redirect_url:
            return redirect(webform.redirect_url)
        return render(request, self.template_name, self.context(request, webform, None, done=True))


# --- Lead scoring ----------------------------------------------------------------------------------------------------


class ScoringView(ManagerRequiredMixin, View):
    template_name = "leads/scoring.html"

    def render_page(self, request, settings_form=None):
        settings = LeadSettings.for_org(request.org)
        return render(request, self.template_name, {
            "rules": ScoringRule.objects.for_org(request.org),
            "settings_form": settings_form or LeadSettingsForm(instance=settings), "settings": settings,
        })

    def get(self, request):
        return self.render_page(request)

    def post(self, request):
        if request.POST.get("action") == "recalculate":
            recompute_scores.delay(request.org.pk)
            messages.success(request, "Recalculating every contact's score.")
            return redirect("leads:scoring")
        form = LeadSettingsForm(request.POST, instance=LeadSettings.for_org(request.org))
        if not form.is_valid():
            return self.render_page(request, form)
        form.save()
        recompute_scores.delay(request.org.pk)
        messages.success(request, "Grade thresholds saved; grades are being updated.")
        return redirect("leads:scoring")


class ScoringRuleEditView(ManagerRequiredMixin, View):
    template_name = "leads/rule_form.html"

    def get_rule(self):
        if pk := self.kwargs.get("pk"):
            return get_object_or_404(ScoringRule.objects.for_org(self.request.org), pk=pk)
        return ScoringRule(org=self.request.org, kind=self.request.GET.get("kind", ScoringRule.PROFILE), points=10)

    def render_page(self, request, rule, form, conditions, errors=()):
        return render(request, self.template_name, {
            "rule": rule, "form": form, "schema": contact_conditions_schema(request.org),
            "definition": {"conditions": conditions or {"match": "all", "rules": []}}, "errors": errors,
        })

    def get(self, request, pk=None):
        rule = self.get_rule()
        return self.render_page(request, rule, ScoringRuleForm(instance=rule, org=request.org), rule.conditions)

    def post(self, request, pk=None):
        rule = self.get_rule()
        form = ScoringRuleForm(request.POST, instance=rule, org=request.org)
        try:
            raw = json.loads(request.POST.get("conditions_json") or "{}")
        except ValueError:
            raw = {}
        errors = []
        try:
            conditions = validate_conditions(raw, request.org)
        except ConfigError as exc:
            conditions, errors = raw, exc.errors
        if form.is_valid() and form.cleaned_data["kind"] == ScoringRule.PROFILE and not conditions.get("rules") and not errors:
            errors = ["Profile rules need at least one condition."]
        if not form.is_valid() or errors:
            return self.render_page(request, rule, form, conditions, errors)
        rule = form.save(commit=False)
        rule.conditions = conditions if rule.kind == ScoringRule.PROFILE else {}
        if rule.kind == ScoringRule.PROFILE:
            rule.event, rule.max_times = "", None
        rule.save()
        recompute_scores.delay(request.org.pk)
        messages.success(request, f"Rule “{rule.name}” saved; scores are being updated.")
        return redirect("leads:scoring")


class ScoringRuleDeleteView(ManagerRequiredMixin, OrgScopedQuerysetMixin, DeleteView):
    model = ScoringRule
    template_name = "contacts/confirm_delete.html"
    success_url = reverse_lazy("leads:scoring")

    def form_valid(self, form):
        response = super().form_valid(form)
        recompute_scores.delay(self.request.org.pk)
        return response
