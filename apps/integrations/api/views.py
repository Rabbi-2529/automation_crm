"""REST API v1. Writes go through the same services as the web app, so the timeline, automations and webhooks fire."""

from django.db import transaction
from django.db.models import Q
from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.activities.models import Activity, ActivityKind, Note
from apps.activities.services import record, record_changes, snapshot
from apps.contacts.models import Company, Contact, Source, Tag
from apps.contacts.views import COMPANY_TRACKED, CONTACT_TRACKED
from apps.orgs.models import Membership
from apps.pipeline import services as pipeline_services
from apps.pipeline.models import Deal, DealStatus, Pipeline
from apps.pipeline.views import DEAL_TRACKED
from apps.tasks import services as task_services
from apps.tasks.models import Task

from . import serializers as s


def _since(request, name="updated_since"):
    value = request.query_params.get(name)
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        raise ValidationError({name: "Use an ISO 8601 date-time, e.g. 2026-10-01T00:00:00Z."})
    return parsed


class OrgScopedMixin:
    """Querysets are always the API key's workspace. While the OpenAPI schema is generated there's no workspace."""

    model = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.model.objects.none()
        return self.org_queryset()

    def org_queryset(self):
        return self.model.objects.for_org(self.request.org)

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "org": getattr(self.request, "org", None)}


class OrgViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]


UPDATED_SINCE = OpenApiParameter("updated_since", str, description="Only records changed after this ISO date-time.")


@extend_schema(parameters=[
    OpenApiParameter("search", str, description="Name, email or phone contains."),
    OpenApiParameter("email", str), OpenApiParameter("phone", str, description="Matches the last 10 digits."),
    OpenApiParameter("tag", str, description="Tag name."), OpenApiParameter("owner", int),
    OpenApiParameter("lifecycle_stage", str), UPDATED_SINCE,
])
class ContactViewSet(OrgViewSet):
    model = Contact
    serializer_class = s.ContactSerializer

    def org_queryset(self):
        qs = super().org_queryset().select_related("company").prefetch_related("tags")
        p = self.request.query_params
        if q := p.get("search", "").strip():
            qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q))
        if email := p.get("email", "").strip().lower():
            qs = qs.filter(email=email)
        if phone := "".join(ch for ch in p.get("phone", "") if ch.isdigit()):
            qs = qs.filter(phone_digits__endswith=phone[-10:])
        if tag := p.get("tag"):
            qs = qs.filter(tags__name__iexact=tag)
        if owner := p.get("owner"):
            qs = qs.filter(owner_id=owner)
        if stage := p.get("lifecycle_stage"):
            qs = qs.filter(lifecycle_stage=stage)
        if since := _since(self.request):
            qs = qs.filter(updated_at__gt=since)
        return qs.distinct()

    def perform_create(self, serializer):
        user = self.request.user
        contact = serializer.save(org=self.request.org, source=Source.API, created_by=user,
                                  owner=serializer.validated_data.get("owner", user))
        record(org=contact.org, kind=ActivityKind.CREATED, actor=user, contact=contact, summary="Contact created (API)")
        for tag in contact.tags.all():
            record(org=contact.org, kind=ActivityKind.TAG_ADDED, actor=user, contact=contact, summary=f"Tagged “{tag.name}”",
                   data={"tag": tag.pk})

    def perform_update(self, serializer):
        before = snapshot(Contact.objects.get(pk=serializer.instance.pk), CONTACT_TRACKED)
        contact = serializer.save()
        record_changes(instance=contact, before=before, actor=self.request.user, target_kw={"contact": contact},
                       custom_field_defs=_defs(contact.org, "contact"))

    @extend_schema(summary="Create or update a contact matched by email, then phone",
                   responses={200: s.ContactSerializer, 201: s.ContactSerializer})
    @action(detail=False, methods=["post"])
    def upsert(self, request):
        email = str(request.data.get("email", "")).strip().lower()
        phone = "".join(ch for ch in str(request.data.get("phone", "")) if ch.isdigit())
        if not email and not phone:
            raise ValidationError("Send an email or phone to match on.")
        contacts = Contact.objects.for_org(request.org)
        existing = (contacts.filter(email=email).first() if email else None) or \
                   (contacts.filter(phone_digits__endswith=phone[-10:]).first() if len(phone) >= 7 else None)
        serializer = self.get_serializer(existing, data=request.data, partial=existing is not None)
        serializer.is_valid(raise_exception=True)
        if existing:
            self.perform_update(serializer)
            return Response(serializer.data)
        self.perform_create(serializer)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


def _defs(org, entity):
    from apps.contacts import custom_fields

    return custom_fields.definitions_for(org, entity)


@extend_schema(parameters=[OpenApiParameter("search", str), UPDATED_SINCE])
class CompanyViewSet(OrgViewSet):
    model = Company
    serializer_class = s.CompanySerializer

    def org_queryset(self):
        qs = super().org_queryset().prefetch_related("tags")
        if q := self.request.query_params.get("search", "").strip():
            qs = qs.filter(Q(name__icontains=q) | Q(domain__icontains=q))
        if since := _since(self.request):
            qs = qs.filter(updated_at__gt=since)
        return qs

    def perform_create(self, serializer):
        company = serializer.save(org=self.request.org, created_by=self.request.user)
        record(org=company.org, kind=ActivityKind.CREATED, actor=self.request.user, company=company, summary="Company created (API)")

    def perform_update(self, serializer):
        before = snapshot(Company.objects.get(pk=serializer.instance.pk), COMPANY_TRACKED)
        company = serializer.save()
        record_changes(instance=company, before=before, actor=self.request.user, target_kw={"company": company},
                       custom_field_defs=_defs(company.org, "company"))


@extend_schema(parameters=[
    OpenApiParameter("pipeline", int), OpenApiParameter("stage", int), OpenApiParameter("status", str, enum=DealStatus.values),
    OpenApiParameter("contact", int), OpenApiParameter("owner", int), UPDATED_SINCE,
])
class DealViewSet(OrgViewSet):
    model = Deal
    serializer_class = s.DealSerializer

    def org_queryset(self):
        qs = super().org_queryset().select_related("stage", "pipeline", "lost_reason", "org").prefetch_related("tags")
        p = self.request.query_params
        for name in ("pipeline", "stage", "contact", "owner"):
            if value := p.get(name):
                qs = qs.filter(**{f"{name}_id": value})
        if value := p.get("status"):
            qs = qs.filter(status=value)
        if since := _since(self.request):
            qs = qs.filter(updated_at__gt=since)
        return qs

    @transaction.atomic
    def perform_create(self, serializer):
        data = serializer.validated_data
        stage = data.get("stage")
        pipeline = data.get("pipeline") or (stage.pipeline if stage else pipeline_services.default_pipeline(self.request.org))
        if pipeline is None:
            raise ValidationError({"pipeline": "This workspace has no pipeline."})
        stage = stage or pipeline.stages.order_by("position").first()
        contact = data.get("contact")
        deal = serializer.save(org=self.request.org, pipeline=pipeline, stage=stage, created_by=self.request.user,
                               owner=data.get("owner", self.request.user),
                               company=data.get("company") or (contact.company if contact else None))
        pipeline_services.deal_created(deal, self.request.user)

    @transaction.atomic
    def perform_update(self, serializer):
        deal = serializer.instance
        before = snapshot(Deal.objects.get(pk=deal.pk), DEAL_TRACKED)
        new_stage = serializer.validated_data.pop("stage", None)
        deal = serializer.save()
        record_changes(instance=deal, before=before, actor=self.request.user, target_kw={"deal": deal},
                       custom_field_defs=_defs(deal.org, "deal"))
        if new_stage and new_stage.pk != deal.stage_id:
            pipeline_services.move_deal(deal, new_stage, self.request.user)

    def _respond(self, deal):
        deal.refresh_from_db()
        return Response(self.get_serializer(deal).data)

    @extend_schema(summary="Mark the deal won", request=None)
    @action(detail=True, methods=["post"])
    def won(self, request, pk=None):
        deal = self.get_object()
        if deal.status != DealStatus.WON:
            pipeline_services.mark_won(deal, request.user)
        return self._respond(deal)

    @extend_schema(summary="Mark the deal lost", request=s.LostSerializer)
    @action(detail=True, methods=["post"])
    def lost(self, request, pk=None):
        deal = self.get_object()
        form = s.LostSerializer(data=request.data, context={"org": request.org})
        form.is_valid(raise_exception=True)
        pipeline_services.mark_lost(deal, request.user, form.validated_data.get("reason"), form.validated_data.get("note", ""))
        return self._respond(deal)

    @extend_schema(summary="Reopen a won or lost deal", request=None)
    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        deal = self.get_object()
        if deal.status != DealStatus.OPEN:
            pipeline_services.reopen(deal, request.user)
        return self._respond(deal)


@extend_schema(parameters=[
    OpenApiParameter("assignee", int), OpenApiParameter("contact", int), OpenApiParameter("deal", int),
    OpenApiParameter("open", bool, description="true = not completed, false = completed"),
    OpenApiParameter("due_before", str), OpenApiParameter("due_after", str),
])
class TaskViewSet(OrgViewSet):
    model = Task
    serializer_class = s.TaskSerializer

    def org_queryset(self):
        qs = super().org_queryset()
        p = self.request.query_params
        for name in ("assignee", "contact", "deal"):
            if value := p.get(name):
                qs = qs.filter(**{f"{name}_id": value})
        if (value := p.get("open")) in ("true", "false"):
            qs = qs.filter(completed_at__isnull=value == "true")
        if before := _since(self.request, "due_before"):
            qs = qs.filter(due_at__lt=before)
        if after := _since(self.request, "due_after"):
            qs = qs.filter(due_at__gte=after)
        return qs

    def perform_create(self, serializer):
        data = serializer.validated_data
        task = serializer.save(org=self.request.org, created_by=self.request.user, assignee=data.get("assignee", self.request.user))
        task_services.task_created(task, self.request.user)

    @extend_schema(summary="Complete the task", request=None)
    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        task = self.get_object()
        if not task.is_done:
            task_services.complete(task, request.user)
        return Response(self.get_serializer(task).data)

    @extend_schema(summary="Reopen a completed task", request=None)
    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        task = self.get_object()
        if task.is_done:
            task_services.reopen(task)
        return Response(self.get_serializer(task).data)


class NoteViewSet(OrgScopedMixin, mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    model = Note
    serializer_class = s.NoteSerializer

    def org_queryset(self):
        qs = Note.objects.for_org(self.request.org)
        for name in ("contact", "company", "deal"):
            if value := self.request.query_params.get(name):
                qs = qs.filter(**{f"{name}_id": value})
        return qs

    def perform_create(self, serializer):
        note = serializer.save(org=self.request.org, author=self.request.user)
        targets = {k: getattr(note, k) for k in ("contact", "company", "deal") if getattr(note, f"{k}_id")}
        record(org=note.org, kind=ActivityKind.NOTE_ADDED, actor=self.request.user, note=note, summary="Added a note (API)", **targets)


@extend_schema(parameters=[
    OpenApiParameter("contact", int), OpenApiParameter("company", int), OpenApiParameter("deal", int),
    OpenApiParameter("kind", str, enum=ActivityKind.values), OpenApiParameter("since", str, description="ISO date-time"),
])
class ActivityViewSet(OrgScopedMixin, viewsets.ReadOnlyModelViewSet):
    """The timeline: everything that happened, newest first."""

    model = Activity
    serializer_class = s.ActivitySerializer

    def org_queryset(self):
        qs = Activity.objects.for_org(self.request.org).select_related("actor")
        p = self.request.query_params
        for name in ("contact", "company", "deal"):
            if value := p.get(name):
                qs = qs.filter(**{f"{name}_id": value})
        if kind := p.get("kind"):
            qs = qs.filter(kind=kind)
        if since := _since(self.request, "since"):
            qs = qs.filter(created_at__gt=since)
        return qs


class PipelineViewSet(OrgScopedMixin, viewsets.ReadOnlyModelViewSet):
    model = Pipeline
    serializer_class = s.PipelineSerializer

    def org_queryset(self):
        return Pipeline.objects.for_org(self.request.org).prefetch_related("stages")


class TagViewSet(OrgScopedMixin, viewsets.ReadOnlyModelViewSet):
    model = Tag
    serializer_class = s.TagSerializer


class MemberViewSet(OrgScopedMixin, viewsets.ReadOnlyModelViewSet):
    """People in the workspace (ids are used for owner / assignee)."""

    model = Membership
    serializer_class = s.MemberSerializer

    def org_queryset(self):
        return Membership.objects.filter(org=self.request.org, is_active=True).select_related("user")


class WorkspaceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    currency = serializers.CharField()
    timezone = serializers.CharField()
    key = serializers.CharField()
    scope = serializers.CharField()
    acting_as = serializers.CharField()


class MeView(APIView):
    """Which workspace and person this API key belongs to. Handy to test a key."""

    @extend_schema(responses=WorkspaceSerializer)
    def get(self, request):
        org, key = request.org, request.auth
        return Response({"id": org.pk, "name": org.name, "currency": org.currency, "timezone": org.timezone,
                         "key": key.name, "scope": key.scope, "acting_as": request.user.display_name})
