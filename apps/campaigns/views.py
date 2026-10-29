import json

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import DeleteView, ListView

from apps.automation.registry import ConfigError, validate_conditions
from apps.contacts.models import Contact
from apps.leads.views import contact_conditions_schema
from apps.orgs.mixins import ManagerRequiredMixin, OrgMemberRequiredMixin, OrgScopedQuerysetMixin

from . import engine
from .forms import BroadcastForm, EnrollForm, ScheduleForm, SegmentForm, SequenceForm, StepFormSet
from .models import Broadcast, BroadcastStatus, EnrollmentStatus, Segment, Sequence, SequenceEnrollment


def _conditions_from_post(request):
    try:
        raw = json.loads(request.POST.get("conditions_json") or "{}")
    except ValueError:
        raw = {}
    try:
        return validate_conditions(raw, request.org), []
    except ConfigError as exc:
        return raw, exc.errors


# --- Segments ---------------------------------------------------------------------------------------------------------


class SegmentListView(OrgMemberRequiredMixin, ListView):
    template_name = "campaigns/segment_list.html"
    context_object_name = "segments"

    def get_queryset(self):
        segments = list(Segment.objects.for_org(self.request.org).select_related("created_by"))
        for segment in segments:
            segment.size = segment.contacts().count()
        return segments


class SegmentEditView(ManagerRequiredMixin, View):
    template_name = "campaigns/segment_form.html"

    def get_segment(self):
        if pk := self.kwargs.get("pk"):
            return get_object_or_404(Segment.objects.for_org(self.request.org), pk=pk)
        return Segment(org=self.request.org)

    def render_page(self, segment, form, conditions, errors=()):
        preview = None
        if segment.pk:
            preview = segment.contacts().count()
        return render(self.request, self.template_name, {
            "segment": segment, "form": form, "errors": errors, "preview": preview,
            "schema": contact_conditions_schema(self.request.org), "definition": {"conditions": conditions or {"match": "all", "rules": []}},
        })

    def get(self, request, pk=None):
        segment = self.get_segment()
        return self.render_page(segment, SegmentForm(instance=segment, org=request.org), segment.conditions)

    def post(self, request, pk=None):
        segment = self.get_segment()
        form = SegmentForm(request.POST, instance=segment, org=request.org)
        conditions, errors = _conditions_from_post(request)
        if not form.is_valid() or errors:
            return self.render_page(segment, form, conditions, errors)
        segment = form.save(commit=False)
        segment.conditions = conditions
        segment.created_by = segment.created_by or request.user
        segment.save()
        messages.success(request, f"Segment “{segment.name}” saved: {segment.contacts().count()} contact(s) right now.")
        return redirect(reverse("contacts:contact_list") + f"?segment={segment.pk}")


class SegmentDeleteView(ManagerRequiredMixin, OrgScopedQuerysetMixin, DeleteView):
    model = Segment
    template_name = "contacts/confirm_delete.html"
    success_url = reverse_lazy("campaigns:segment_list")

    def form_valid(self, form):
        if self.object.broadcasts.exists():
            messages.error(self.request, "Broadcasts use this segment, so it can't be deleted.")
            return redirect("campaigns:segment_list")
        return super().form_valid(form)


# --- Sequences --------------------------------------------------------------------------------------------------------


class SequenceListView(OrgMemberRequiredMixin, ListView):
    template_name = "campaigns/sequence_list.html"
    context_object_name = "sequences"

    def get_queryset(self):
        return Sequence.objects.for_org(self.request.org).annotate(
            step_count=Count("steps", distinct=True),
            active_count=Count("enrollments", filter=Q(enrollments__status=EnrollmentStatus.ACTIVE), distinct=True),
            total_count=Count("enrollments", distinct=True),
        )


class SequenceEditView(ManagerRequiredMixin, View):
    template_name = "campaigns/sequence_form.html"

    def get_sequence(self):
        if pk := self.kwargs.get("pk"):
            return get_object_or_404(Sequence.objects.for_org(self.request.org), pk=pk)
        return Sequence(org=self.request.org)

    def render_page(self, sequence, form, formset, conditions, errors=()):
        return render(self.request, self.template_name, {
            "sequence": sequence, "form": form, "formset": formset, "errors": errors,
            "schema": contact_conditions_schema(self.request.org), "definition": {"conditions": conditions or {"match": "all", "rules": []}},
        })

    def get(self, request, pk=None):
        sequence = self.get_sequence()
        return self.render_page(sequence, SequenceForm(instance=sequence, org=request.org),
                                StepFormSet(instance=sequence, org=request.org, prefix="steps"), sequence.exit_conditions)

    @transaction.atomic
    def post(self, request, pk=None):
        sequence = self.get_sequence()
        form = SequenceForm(request.POST, instance=sequence, org=request.org)
        formset = StepFormSet(request.POST, instance=sequence, org=request.org, prefix="steps")
        conditions, errors = _conditions_from_post(request)
        if not (form.is_valid() and formset.is_valid()) or errors:
            return self.render_page(sequence, form, formset, conditions, errors)
        sequence = form.save(commit=False)
        sequence.exit_conditions = conditions
        sequence.created_by = sequence.created_by or request.user
        sequence.save()
        formset.instance = sequence
        steps = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for step in steps:
            step.save()
        for i, step in enumerate(sequence.steps.order_by("position", "pk")):
            if step.position != i:
                step.position = i
                step.save(update_fields=["position"])
        messages.success(request, f"Sequence “{sequence.name}” saved.")
        return redirect("campaigns:sequence_detail", pk=sequence.pk)


class SequenceDetailView(OrgMemberRequiredMixin, View):
    template_name = "campaigns/sequence_detail.html"

    def get(self, request, pk):
        sequence = get_object_or_404(Sequence.objects.for_org(request.org), pk=pk)
        steps, counts = engine.sequence_stats(sequence)
        return render(request, self.template_name, {
            "sequence": sequence, "steps": steps, "counts": counts, "statuses": EnrollmentStatus.choices,
            "enrollments": sequence.enrollments.select_related("contact", "enrolled_by")[:300],
            "segments": Segment.objects.for_org(request.org),
        })


class SequenceEnrollSegmentView(ManagerRequiredMixin, View):
    def post(self, request, pk):
        sequence = get_object_or_404(Sequence.objects.for_org(request.org), pk=pk)
        segment = get_object_or_404(Segment.objects.for_org(request.org), pk=request.POST.get("segment") or 0)
        added = sum(1 for contact in segment.contacts().iterator(chunk_size=500)
                    if engine.enroll(sequence, contact, by=request.user, note=f"Segment “{segment.name}”"))
        messages.success(request, f"Added {added} contact(s) from “{segment.name}”. Anyone already active was left as is.")
        return redirect("campaigns:sequence_detail", pk=pk)


class ContactEnrollView(OrgMemberRequiredMixin, View):
    """From a contact page: add to / remove from a sequence."""

    def post(self, request, contact_pk):
        contact = get_object_or_404(Contact.objects.for_org(request.org), pk=contact_pk)
        back = reverse("contacts:contact_detail", args=[contact.pk])
        if remove_pk := request.POST.get("remove"):
            sequence = get_object_or_404(Sequence.objects.for_org(request.org), pk=remove_pk)
            engine.remove(sequence, contact, reason=f"removed by {request.user.display_name}")
            messages.info(request, f"Removed from “{sequence.name}”.")
            return redirect(back)
        form = EnrollForm(request.POST, org=request.org)
        if form.is_valid():
            sequence = form.cleaned_data["sequence"]
            if engine.enroll(sequence, contact, by=request.user):
                messages.success(request, f"Added to “{sequence.name}”.")
            else:
                messages.warning(request, f"Already in “{sequence.name}”, or it has no steps.")
        return redirect(back)


class EnrollmentRemoveView(ManagerRequiredMixin, View):
    def post(self, request, pk):
        enrollment = get_object_or_404(SequenceEnrollment.objects.for_org(request.org), pk=pk, status=EnrollmentStatus.ACTIVE)
        engine.finish(enrollment, EnrollmentStatus.REMOVED, f"removed by {request.user.display_name}")
        return redirect("campaigns:sequence_detail", pk=enrollment.sequence_id)


class SequenceDeleteView(ManagerRequiredMixin, OrgScopedQuerysetMixin, DeleteView):
    model = Sequence
    template_name = "contacts/confirm_delete.html"
    success_url = reverse_lazy("campaigns:sequence_list")
    extra_context = {"warning": "Active enrollments stop immediately."}


# --- Broadcasts -------------------------------------------------------------------------------------------------------


class BroadcastListView(OrgMemberRequiredMixin, ListView):
    template_name = "campaigns/broadcast_list.html"
    context_object_name = "broadcasts"

    def get_queryset(self):
        return Broadcast.objects.for_org(self.request.org).select_related("segment", "created_by").annotate(
            recipient_count=Count("recipients"))


class BroadcastEditView(ManagerRequiredMixin, View):
    template_name = "campaigns/broadcast_form.html"

    def get_broadcast(self):
        if pk := self.kwargs.get("pk"):
            return get_object_or_404(Broadcast.objects.for_org(self.request.org), pk=pk, status=BroadcastStatus.DRAFT)
        return Broadcast(org=self.request.org, channel=self.request.GET.get("channel", "email"))

    def get(self, request, pk=None):
        broadcast = self.get_broadcast()
        return render(request, self.template_name, {"broadcast": broadcast, "form": BroadcastForm(instance=broadcast, org=request.org)})

    def post(self, request, pk=None):
        broadcast = self.get_broadcast()
        form = BroadcastForm(request.POST, instance=broadcast, org=request.org)
        if not form.is_valid():
            return render(request, self.template_name, {"broadcast": broadcast, "form": form})
        broadcast = form.save(commit=False)
        broadcast.created_by = broadcast.created_by or request.user
        broadcast.save()
        return redirect("campaigns:broadcast_detail", pk=broadcast.pk)


class BroadcastDetailView(OrgMemberRequiredMixin, View):
    template_name = "campaigns/broadcast_detail.html"

    def get(self, request, pk, schedule_form=None):
        broadcast = get_object_or_404(Broadcast.objects.for_org(request.org).select_related("segment", "template"), pk=pk)
        audience = broadcast.segment.contacts()
        reachable = audience.filter(email_opt_out=False).exclude(email="") if broadcast.channel == "email" else \
            audience.filter(sms_opt_out=False).exclude(phone="")
        return render(request, self.template_name, {
            "broadcast": broadcast, "audience": audience.count(), "reachable": reachable.count(),
            "stats": engine.broadcast_stats(broadcast) if broadcast.status != BroadcastStatus.DRAFT else None,
            "schedule_form": schedule_form or ScheduleForm(),
            "recipients": broadcast.recipients.select_related("contact", "message")[:500],
        })

    def post(self, request, pk):
        """Send now / schedule / cancel."""
        broadcast = get_object_or_404(Broadcast.objects.for_org(request.org), pk=pk)
        if not request.membership.is_manager:
            return redirect("campaigns:broadcast_detail", pk=pk)
        if request.POST.get("action") == "cancel":
            if broadcast.status in (BroadcastStatus.SCHEDULED, BroadcastStatus.SENDING):
                Broadcast.objects.filter(pk=pk).update(status=BroadcastStatus.CANCELLED, finished_at=timezone.now())
                messages.info(request, "Broadcast cancelled. Messages already queued still go out.")
            return redirect("campaigns:broadcast_detail", pk=pk)
        if broadcast.status != BroadcastStatus.DRAFT:
            return redirect("campaigns:broadcast_detail", pk=pk)
        form = ScheduleForm(request.POST)
        if not form.is_valid():
            return self.get(request, pk, schedule_form=form)
        when = form.cleaned_data.get("when") or timezone.now()
        Broadcast.objects.filter(pk=pk).update(status=BroadcastStatus.SCHEDULED, scheduled_at=when)
        if when <= timezone.now():
            engine.process_broadcasts()
            messages.success(request, "Sending now. Stats update as messages go out.")
        else:
            messages.success(request, f"Scheduled for {timezone.localtime(when):%d %b %Y, %H:%M}.")
        return redirect("campaigns:broadcast_detail", pk=pk)


class BroadcastDeleteView(ManagerRequiredMixin, OrgScopedQuerysetMixin, DeleteView):
    model = Broadcast
    template_name = "contacts/confirm_delete.html"
    success_url = reverse_lazy("campaigns:broadcast_list")

    def get_queryset(self):
        return super().get_queryset().filter(status__in=[BroadcastStatus.DRAFT, BroadcastStatus.CANCELLED])
