import json

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.html import format_html
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, FormView, ListView, TemplateView, UpdateView

from apps.activities.forms import AttachmentForm, NoteForm
from apps.activities.services import record_changes, snapshot, timeline_for
from apps.contacts import custom_fields
from apps.contacts.forms import org_users
from apps.contacts.models import Contact, Entity
from apps.core import cells
from apps.core.datatables import Column, DataTableView
from apps.orgs.mixins import ManagerRequiredMixin, OrgFormKwargsMixin, OrgMemberRequiredMixin, OrgScopedQuerysetMixin

from . import services
from .filters import filter_deals
from .forms import DealForm, LostForm, LostReasonForm, PipelineForm, StageFormSet
from .models import Deal, DealStatus, LostReason, Pipeline, Stage

DEAL_TRACKED = ["title", "value", "contact", "company", "owner", "expected_close_date"]


def deal_queryset(request):
    return Deal.objects.for_org(request.org).select_related("stage", "pipeline", "contact", "company", "owner", "org")


class DealBoardView(OrgMemberRequiredMixin, TemplateView):
    template_name = "pipeline/board.html"

    def get_context_data(self, **kwargs):
        org = self.request.org
        pipelines = list(Pipeline.objects.for_org(org))
        pipeline = next((p for p in pipelines if str(p.pk) == self.request.GET.get("pipeline")), None)
        pipeline = pipeline or services.default_pipeline(org)
        if pipeline is None:
            raise Http404("No pipeline")
        deals = filter_deals(
            deal_queryset(self.request).filter(pipeline=pipeline).prefetch_related("tags"),
            {"status": "open", "owner": self.request.GET.get("owner"), "q": self.request.GET.get("q")},
            user=self.request.user,
        ).order_by("position", "-created_at")
        by_stage = {}
        for deal in deals:
            by_stage.setdefault(deal.stage_id, []).append(deal)
        totals = services.stage_totals(pipeline, deals)
        stages = list(pipeline.stages.all())
        columns = [{"stage": s, "deals": by_stage.get(s.pk, []), "totals": totals.get(s.pk, {})} for s in stages]
        closed = Deal.objects.filter(pipeline=pipeline).exclude(status=DealStatus.OPEN).values("status").annotate(
            count=Count("pk"), value=Sum("value"))
        return super().get_context_data(
            pipeline=pipeline, pipelines=pipelines, columns=columns,
            open_total=sum((c["totals"].get("value", 0) for c in columns), 0),
            weighted_total=sum((c["totals"].get("weighted", 0) for c in columns), 0),
            closed={row["status"]: row for row in closed},
            owners=org_users(org), filters=self.request.GET, **kwargs,
        )


class DealListView(OrgMemberRequiredMixin, TemplateView):
    """Filter bar + DataTable; rows come from DealTableView."""

    template_name = "pipeline/deal_list.html"

    def get_context_data(self, **kwargs):
        org = self.request.org
        return super().get_context_data(
            pipelines=Pipeline.objects.for_org(org), owners=org_users(org), statuses=DealStatus.choices,
            filters=self.request.GET, **kwargs,
        )


class DealTableView(OrgMemberRequiredMixin, DataTableView):
    columns = [
        Column("Deal", lambda d: format_html(
            '<a href="{}" class="font-medium text-slate-900 hover:text-violet-700">{}</a><span class="block text-xs text-slate-400">{}</span>',
            reverse("pipeline:deal_detail", args=[d.pk]), d.title, d.pipeline.name), ("title",)),
        Column("Value", lambda d: cells.money(d.currency_code, d.value), ("value",)),
        Column("Stage", lambda d: d.stage.name, ("stage__position",)),
        Column("Status", lambda d: cells.badge(d.get_status_display(), cells.STATUS_COLORS[d.status]), ("status",)),
        Column("Contact / company", lambda d: format_html("{}<span class=\"block text-slate-500\">{}</span>",
                                                          d.contact.display_name if d.contact else "", d.company.name if d.company else "")),
        Column("Expected close", lambda d: format_html('<span class="{}">{}</span>', "text-red-600" if d.is_overdue else "",
                                                       cells.date(d.expected_close_date)), ("expected_close_date",)),
        Column("Owner", lambda d: cells.person(d.owner), ("owner__first_name", "owner__email")),
        Column("Created", lambda d: cells.date(d.created_at), ("created_at",)),
    ]

    def base_queryset(self):
        return Deal.objects.for_org(self.request.org)

    def filter_queryset(self, queryset, params, search):
        queryset = queryset.select_related("stage", "pipeline", "contact", "company", "owner", "org")
        return filter_deals(queryset, {**params.dict(), "q": search}, user=self.request.user)

    def extra_payload(self, queryset):
        return {"filteredValue": float(queryset.order_by().aggregate(v=Sum("value"))["v"] or 0)}


class DealCreateView(OrgMemberRequiredMixin, OrgFormKwargsMixin, CreateView):
    model = Deal
    form_class = DealForm
    template_name = "pipeline/deal_form.html"

    def get_initial(self):
        org, params = self.request.org, self.request.GET
        initial = {"owner": self.request.user.pk}
        stage = Stage.objects.for_org(org).filter(pk=params.get("stage")).first()
        if not stage and (pipeline := services.default_pipeline(org)):
            stage = pipeline.stages.first()
        if stage:
            initial["stage"] = stage.pk
        for name in ("contact", "company"):
            if params.get(name, "").isdigit():
                initial[name] = params[name]
        if contact := Contact.objects.for_org(org).select_related("company").filter(pk=initial.get("contact")).first():
            initial["title"] = f"{contact.company.name if contact.company else contact.display_name} deal"
        return initial

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.currency = self.request.org.currency
        deal = form.save()
        services.deal_created(deal, self.request.user)
        messages.success(self.request, f"Deal “{deal.title}” created.")
        return redirect("pipeline:deal_detail", pk=deal.pk)


class DealUpdateView(OrgMemberRequiredMixin, OrgScopedQuerysetMixin, OrgFormKwargsMixin, UpdateView):
    model = Deal
    form_class = DealForm
    template_name = "pipeline/deal_form.html"

    @transaction.atomic
    def form_valid(self, form):
        fresh = Deal.objects.select_related("stage").get(pk=self.object.pk)
        before = snapshot(fresh, DEAL_TRACKED)
        new_stage = form.cleaned_data["stage"]
        form.instance.stage = fresh.stage  # the move service records stage changes
        deal = form.save()
        record_changes(instance=deal, before=before, actor=self.request.user, custom_field_defs=form.custom_field_defs,
                       target_kw={"deal": deal})
        if new_stage.pk != fresh.stage_id:
            services.move_deal(deal, new_stage, self.request.user)  # DealForm.save already set the new pipeline
        messages.success(self.request, "Deal saved.")
        return redirect("pipeline:deal_detail", pk=deal.pk)


class DealDetailView(OrgMemberRequiredMixin, OrgScopedQuerysetMixin, DetailView):
    model = Deal
    template_name = "pipeline/deal_detail.html"
    context_object_name = "deal"

    def get_queryset(self):
        return deal_queryset(self.request).select_related("lost_reason", "created_by").prefetch_related("tags")

    def get_context_data(self, **kwargs):
        deal = self.object
        defs = custom_fields.definitions_for(self.request.org, Entity.DEAL)
        return super().get_context_data(
            custom_values=custom_fields.display(defs, deal.custom_fields),
            stages=list(deal.pipeline.stages.all()),
            activities=timeline_for(deal)[:100],
            attachments=deal.attachments.select_related("uploaded_by"),
            tasks=deal.tasks.open().select_related("assignee"),
            note_form=NoteForm(), attachment_form=AttachmentForm(),
            lost_form=LostForm(org=self.request.org),
            **kwargs,
        )


class DealDeleteView(ManagerRequiredMixin, OrgScopedQuerysetMixin, DeleteView):
    model = Deal
    template_name = "contacts/confirm_delete.html"
    success_url = reverse_lazy("pipeline:deal_board")

    def form_valid(self, form):
        messages.success(self.request, f"Deleted deal “{self.object.title}”.")
        return super().form_valid(form)


class DealActionMixin(OrgMemberRequiredMixin):
    def get_deal(self):
        return get_object_or_404(deal_queryset(self.request), pk=self.kwargs["pk"])


class DealMoveView(DealActionMixin, View):
    """Kanban drop: {"stage": id, "order": [ids in that column]}. The board recounts its own columns."""

    def post(self, request, pk):
        deal = self.get_deal()
        is_json = request.content_type == "application/json"
        try:
            payload = json.loads(request.body) if is_json else request.POST
            stage = Stage.objects.for_org(request.org).get(pk=int(payload["stage"]), pipeline=deal.pipeline)
            order = [int(i) for i in payload.get("order", [])] if is_json else None
        except (ValueError, KeyError, TypeError, Stage.DoesNotExist):
            return HttpResponseBadRequest("Invalid stage or order")
        if deal.status != DealStatus.OPEN:
            return HttpResponseBadRequest("Only open deals can be moved")
        services.move_deal(deal, stage, request.user, ordered_ids=order)
        if not is_json:  # stage bar on the deal page
            return redirect("pipeline:deal_detail", pk=deal.pk)
        return JsonResponse({"ok": True, "stage": stage.pk})


class DealWonView(DealActionMixin, View):
    def post(self, request, pk):
        deal = self.get_deal()
        if deal.status != DealStatus.WON:
            services.mark_won(deal, request.user)
            messages.success(request, f"🎉 “{deal.title}” marked as won.")
        return redirect(request.POST.get("next") or reverse("pipeline:deal_detail", args=[deal.pk]))


class DealLostView(DealActionMixin, FormView):
    form_class = LostForm
    template_name = "pipeline/deal_lost.html"

    def dispatch(self, request, *args, **kwargs):
        self.deal = None
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        self.deal = self.deal or self.get_deal()
        return {**super().get_form_kwargs(), "org": self.request.org}

    def get_context_data(self, **kwargs):
        return super().get_context_data(deal=self.deal, **kwargs)

    def form_valid(self, form):
        services.mark_lost(self.deal, self.request.user, form.cleaned_data["reason"], form.cleaned_data["note"])
        messages.info(self.request, f"“{self.deal.title}” marked as lost.")
        return redirect("pipeline:deal_detail", pk=self.deal.pk)


class DealReopenView(DealActionMixin, View):
    def post(self, request, pk):
        deal = self.get_deal()
        if deal.status != DealStatus.OPEN:
            services.reopen(deal, request.user)
            messages.success(request, f"“{deal.title}” is open again.")
        return redirect("pipeline:deal_detail", pk=deal.pk)


# --- Settings: pipelines & lost reasons ---------------------------------------------------------------------------


class PipelineListView(ManagerRequiredMixin, ListView):
    template_name = "pipeline/pipeline_list.html"
    context_object_name = "pipelines"

    def get_queryset(self):
        return Pipeline.objects.for_org(self.request.org).prefetch_related("stages").annotate(
            open_deals=Count("deals", filter=Q(deals__status=DealStatus.OPEN))
        )

    def get_context_data(self, **kwargs):
        return super().get_context_data(lost_reasons=LostReason.objects.for_org(self.request.org), **kwargs)


class PipelineEditMixin(ManagerRequiredMixin, OrgFormKwargsMixin):
    model = Pipeline
    form_class = PipelineForm
    template_name = "pipeline/pipeline_form.html"

    def get_queryset(self):
        return Pipeline.objects.for_org(self.request.org)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("stage_formset", self.build_formset())
        return context

    def build_formset(self, data=None):
        return StageFormSet(data, instance=self.object or Pipeline(org=self.request.org), prefix="stages")

    def post(self, request, *args, **kwargs):
        self.object = self.get_object() if self.kwargs.get("pk") else None
        form = self.get_form()
        formset = self.build_formset(request.POST)
        if form.is_valid() and formset.is_valid():
            return self.save_all(form, formset)
        return self.render_to_response(self.get_context_data(form=form, stage_formset=formset))

    @transaction.atomic
    def save_all(self, form, formset):
        pipeline = form.save()
        formset.instance = pipeline
        for stage in formset.save(commit=False):
            stage.org = self.request.org
            stage.save()
        for stage in formset.deleted_objects:
            stage.delete()
        if pipeline.is_default:
            Pipeline.objects.for_org(self.request.org).exclude(pk=pipeline.pk).update(is_default=False)
        messages.success(self.request, f"Pipeline “{pipeline.name}” saved.")
        return redirect("pipeline:pipeline_list")


class PipelineCreateView(PipelineEditMixin, CreateView):
    def get_initial(self):
        return {"position": Pipeline.objects.for_org(self.request.org).count()}


class PipelineUpdateView(PipelineEditMixin, UpdateView):
    pass


class PipelineDeleteView(ManagerRequiredMixin, OrgScopedQuerysetMixin, DeleteView):
    model = Pipeline
    template_name = "contacts/confirm_delete.html"
    success_url = reverse_lazy("pipeline:pipeline_list")

    def form_valid(self, form):
        if self.object.deals.exists():
            messages.error(self.request, "This pipeline still has deals. Move or delete them first.")
            return redirect("pipeline:pipeline_list")
        if Pipeline.objects.for_org(self.request.org).count() == 1:
            messages.error(self.request, "You need at least one pipeline.")
            return redirect("pipeline:pipeline_list")
        return super().form_valid(form)


class LostReasonFormMixin(ManagerRequiredMixin, OrgScopedQuerysetMixin, OrgFormKwargsMixin):
    model = LostReason
    form_class = LostReasonForm
    template_name = "contacts/simple_form.html"
    success_url = reverse_lazy("pipeline:pipeline_list")
    extra_context = {"back_url": reverse_lazy("pipeline:pipeline_list"), "noun": "lost reason"}


class LostReasonCreateView(LostReasonFormMixin, CreateView):
    pass


class LostReasonUpdateView(LostReasonFormMixin, UpdateView):
    pass


class LostReasonDeleteView(ManagerRequiredMixin, OrgScopedQuerysetMixin, DeleteView):
    model = LostReason
    template_name = "contacts/confirm_delete.html"
    success_url = reverse_lazy("pipeline:pipeline_list")
    extra_context = {"warning": "Deals lost for this reason keep their status but lose the reason label."}
