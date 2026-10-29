from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, FormView, ListView, TemplateView, UpdateView

from apps.activities.forms import AttachmentForm, NoteForm
from apps.activities.models import ActivityKind
from apps.activities.services import record, record_changes, snapshot, timeline_for
from apps.orgs.mixins import (
    AdminRequiredMixin,
    ManagerRequiredMixin,
    OrgFormKwargsMixin,
    OrgMemberRequiredMixin,
    OrgScopedQuerysetMixin,
)
from apps.orgs.models import Role

from . import custom_fields
from .filters import filter_companies, filter_contacts
from .forms import (
    BulkActionForm,
    CompanyForm,
    ContactForm,
    CustomFieldDefinitionForm,
    ImportMappingForm,
    ImportUploadForm,
    TagForm,
    org_users,
)
from .importer import company_rows, contact_rows, csv_stream, import_targets, read_headers
from .models import (
    Company,
    Contact,
    CustomFieldDefinition,
    Entity,
    ImportJob,
    ImportStatus,
    LifecycleStage,
    Tag,
)
from .tasks import import_contacts

def _segments(org):
    from apps.campaigns.models import Segment

    return Segment.objects.for_org(org)


CONTACT_TRACKED = ["first_name", "last_name", "email", "phone", "job_title", "company", "owner", "lifecycle_stage"]
COMPANY_TRACKED = ["name", "domain", "industry", "phone", "website", "address", "city", "country", "owner"]


# --- Contacts -------------------------------------------------------------------------------------------------------


class ContactListView(OrgMemberRequiredMixin, TemplateView):
    """Filter bar + DataTable; rows come from ContactTableView."""

    template_name = "contacts/contact_list.html"

    def get_context_data(self, **kwargs):
        org = self.request.org
        return super().get_context_data(
            tags=Tag.objects.for_org(org),
            owners=org_users(org),
            stages=LifecycleStage.choices,
            segments=_segments(org),
            filters=self.request.GET,
            total_count=Contact.objects.for_org(org).count(),
            **kwargs,
        )


class ContactCreateView(OrgMemberRequiredMixin, OrgFormKwargsMixin, CreateView):
    model = Contact
    form_class = ContactForm
    template_name = "contacts/contact_form.html"

    def get_initial(self):
        initial = {"owner": self.request.user.pk}
        if company := self.request.GET.get("company"):
            initial["company"] = company
        return initial

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        contact = form.save()
        record(org=contact.org, kind=ActivityKind.CREATED, actor=self.request.user, contact=contact,
               summary="Contact created")
        for tag in contact.tags.all():
            record(org=contact.org, kind=ActivityKind.TAG_ADDED, actor=self.request.user, contact=contact,
                   summary=f"Tagged “{tag.name}”", data={"tag": tag.pk})
        messages.success(self.request, f"Added {contact.display_name}.")
        return redirect(contact_url(contact))


class ContactUpdateView(OrgMemberRequiredMixin, OrgScopedQuerysetMixin, OrgFormKwargsMixin, UpdateView):
    model = Contact
    form_class = ContactForm
    template_name = "contacts/contact_form.html"

    def form_valid(self, form):
        before = snapshot(Contact.objects.get(pk=self.object.pk), CONTACT_TRACKED)
        contact = form.save()
        record_changes(instance=contact, before=before, actor=self.request.user, custom_field_defs=form.custom_field_defs,
                       target_kw={"contact": contact})
        messages.success(self.request, "Contact saved.")
        return redirect(contact_url(contact))


class ContactDetailView(OrgMemberRequiredMixin, OrgScopedQuerysetMixin, DetailView):
    model = Contact
    template_name = "contacts/contact_detail.html"
    context_object_name = "contact"

    def get_queryset(self):
        return super().get_queryset().select_related("company", "owner", "created_by").prefetch_related("tags")

    def get_context_data(self, **kwargs):
        contact = self.object
        defs = custom_fields.definitions_for(self.request.org, Entity.CONTACT)
        return super().get_context_data(
            custom_values=custom_fields.display(defs, contact.custom_fields),
            activities=timeline_for(contact)[:100],
            deals=contact.deals.select_related("stage", "pipeline").order_by("status", "-created_at"),
            workflow_runs=contact.workflow_runs.select_related("workflow")[:5],
            score_logs=contact.score_logs.select_related("rule")[:8],
            enrollments=contact.sequence_enrollments.select_related("sequence").order_by("-created_at")[:6],
            enroll_form=_enroll_form(self.request.org),
            tasks=contact.tasks.open().select_related("assignee"),
            attachments=contact.attachments.select_related("uploaded_by"),
            note_form=NoteForm(),
            attachment_form=AttachmentForm(),
            colleagues=contact.company.contacts.exclude(pk=contact.pk)[:10] if contact.company else [],
            **kwargs,
        )


class ContactDeleteView(ManagerRequiredMixin, OrgScopedQuerysetMixin, DeleteView):
    model = Contact
    template_name = "contacts/confirm_delete.html"
    success_url = reverse_lazy("contacts:contact_list")

    def form_valid(self, form):
        messages.success(self.request, f"Deleted {self.object.display_name}.")
        return super().form_valid(form)


class ContactBulkActionView(OrgMemberRequiredMixin, View):
    def post(self, request):
        form = BulkActionForm(request.POST, org=request.org)
        back = request.POST.get("next") or reverse("contacts:contact_list")
        if not back.startswith("/"):
            back = reverse("contacts:contact_list")
        if not form.is_valid():
            messages.error(request, " ".join(e for errors in form.errors.values() for e in errors))
            return redirect(back)

        action, contacts = form.cleaned_data["action"], list(form.cleaned_data["ids"])
        if action == "delete":
            if not request.membership.has_role(Role.MANAGER):
                raise PermissionDenied
            Contact.objects.filter(pk__in=[c.pk for c in contacts]).delete()
            messages.success(request, f"Deleted {len(contacts)} contacts.")
            return redirect(back)

        with transaction.atomic():
            for contact in contacts:
                before = snapshot(contact, CONTACT_TRACKED)
                if action == "add_tag":
                    contact.tags.add(form.cleaned_data["tag"])
                elif action == "remove_tag":
                    contact.tags.remove(form.cleaned_data["tag"])
                elif action == "assign_owner":
                    contact.owner = form.cleaned_data["owner"]
                    contact.save(update_fields=["owner", "updated_at"])
                elif action == "set_stage":
                    contact.lifecycle_stage = form.cleaned_data["stage"]
                    contact.save(update_fields=["lifecycle_stage", "updated_at"])
                record_changes(instance=contact, before=before, actor=request.user, target_kw={"contact": contact})
        messages.success(request, f"Updated {len(contacts)} contacts.")
        return redirect(back)


class ContactExportView(ManagerRequiredMixin, View):
    def get(self, request):
        queryset = filter_contacts(Contact.objects.for_org(request.org), request.GET)
        response = StreamingHttpResponse(csv_stream(contact_rows(queryset, request.org)), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="contacts-{timezone.localdate():%Y%m%d}.csv"'
        return response


class ContactLookupView(OrgMemberRequiredMixin, View):
    """Select2 AJAX source for LookupWidget pickers."""

    def get(self, request):
        q = request.GET.get("q", "")
        contacts = filter_contacts(Contact.objects.for_org(request.org).select_related("company"), {"q": q, "sort": "name"})[:10]
        return JsonResponse({"results": [
            {"id": c.pk, "text": c.display_name, "hint": " · ".join(filter(None, [c.email, c.company.name if c.company else ""]))}
            for c in contacts
        ]})


class CompanyLookupView(OrgMemberRequiredMixin, View):
    def get(self, request):
        companies = filter_companies(Company.objects.for_org(request.org), {"q": request.GET.get("q", "")})[:10]
        return JsonResponse({"results": [{"id": c.pk, "text": c.name, "hint": c.domain or c.city} for c in companies]})


def _enroll_form(org):
    from apps.campaigns.forms import EnrollForm

    return EnrollForm(org=org)


class ContactConsentView(OrgMemberRequiredMixin, View):
    """Manually mark a contact as opted out of / back into email or SMS (e.g. they asked by phone)."""

    def post(self, request, pk):
        from apps.messaging.consent import opt_in, opt_out

        contact = Contact.objects.for_org(request.org).filter(pk=pk).first()
        channel = request.POST.get("channel")
        if contact and channel in ("email", "sms"):
            how = f"set by {request.user.display_name}"
            changed = opt_out(contact, channel, how) if request.POST.get("opt") == "out" else opt_in(contact, channel, how)
            if changed:
                messages.success(request, "Preferences updated.")
        return redirect("contacts:contact_detail", pk=pk)


def contact_url(contact):
    return reverse("contacts:contact_detail", args=[contact.pk])


# --- Companies ------------------------------------------------------------------------------------------------------


class CompanyListView(OrgMemberRequiredMixin, TemplateView):
    template_name = "contacts/company_list.html"

    def get_context_data(self, **kwargs):
        org = self.request.org
        return super().get_context_data(
            tags=Tag.objects.for_org(org), owners=org_users(org), filters=self.request.GET,
            total_count=Company.objects.for_org(org).count(), **kwargs,
        )


class CompanyCreateView(OrgMemberRequiredMixin, OrgFormKwargsMixin, CreateView):
    model = Company
    form_class = CompanyForm
    template_name = "contacts/company_form.html"

    def get_initial(self):
        return {"owner": self.request.user.pk}

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        company = form.save()
        record(org=company.org, kind=ActivityKind.CREATED, actor=self.request.user, company=company, summary="Company created")
        messages.success(self.request, f"Added {company.name}.")
        return redirect("contacts:company_detail", pk=company.pk)


class CompanyUpdateView(OrgMemberRequiredMixin, OrgScopedQuerysetMixin, OrgFormKwargsMixin, UpdateView):
    model = Company
    form_class = CompanyForm
    template_name = "contacts/company_form.html"

    def form_valid(self, form):
        before = snapshot(Company.objects.get(pk=self.object.pk), COMPANY_TRACKED)
        company = form.save()
        record_changes(instance=company, before=before, actor=self.request.user, custom_field_defs=form.custom_field_defs,
                       target_kw={"company": company})
        messages.success(self.request, "Company saved.")
        return redirect("contacts:company_detail", pk=company.pk)


class CompanyDetailView(OrgMemberRequiredMixin, OrgScopedQuerysetMixin, DetailView):
    model = Company
    template_name = "contacts/company_detail.html"
    context_object_name = "company"

    def get_context_data(self, **kwargs):
        company = self.object
        defs = custom_fields.definitions_for(self.request.org, Entity.COMPANY)
        return super().get_context_data(
            custom_values=custom_fields.display(defs, company.custom_fields),
            people=company.contacts.select_related("owner").order_by("first_name"),
            activities=timeline_for(company)[:100],
            deals=company.deals.select_related("stage", "pipeline").order_by("status", "-created_at"),
            tasks=company.tasks.open().select_related("assignee"),
            attachments=company.attachments.select_related("uploaded_by"),
            note_form=NoteForm(),
            attachment_form=AttachmentForm(),
            **kwargs,
        )


class CompanyDeleteView(ManagerRequiredMixin, OrgScopedQuerysetMixin, DeleteView):
    model = Company
    template_name = "contacts/confirm_delete.html"
    success_url = reverse_lazy("contacts:company_list")

    def form_valid(self, form):
        messages.success(self.request, f"Deleted {self.object.name}. Its contacts were kept.")
        return super().form_valid(form)


class CompanyExportView(ManagerRequiredMixin, View):
    def get(self, request):
        queryset = filter_companies(Company.objects.for_org(request.org), request.GET)
        response = StreamingHttpResponse(csv_stream(company_rows(queryset, request.org)), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="companies-{timezone.localdate():%Y%m%d}.csv"'
        return response


# --- Tags ------------------------------------------------------------------------------------------------------------


class TagListView(ManagerRequiredMixin, ListView):
    template_name = "contacts/tag_list.html"
    context_object_name = "tags"

    def get_queryset(self):
        return Tag.objects.for_org(self.request.org).annotate(
            contact_count=Count("contacts", distinct=True), company_count=Count("companies", distinct=True)
        )


class TagFormMixin(ManagerRequiredMixin, OrgScopedQuerysetMixin, OrgFormKwargsMixin):
    model = Tag
    form_class = TagForm
    template_name = "contacts/simple_form.html"
    success_url = reverse_lazy("contacts:tag_list")
    extra_context = {"back_url": reverse_lazy("contacts:tag_list"), "noun": "tag"}

    def form_valid(self, form):
        messages.success(self.request, f"Tag “{form.instance.name}” saved.")
        return super().form_valid(form)


class TagCreateView(TagFormMixin, CreateView):
    pass


class TagUpdateView(TagFormMixin, UpdateView):
    pass


class TagDeleteView(ManagerRequiredMixin, OrgScopedQuerysetMixin, DeleteView):
    model = Tag
    template_name = "contacts/confirm_delete.html"
    success_url = reverse_lazy("contacts:tag_list")


# --- Custom fields ---------------------------------------------------------------------------------------------------


class CustomFieldListView(AdminRequiredMixin, ListView):
    template_name = "contacts/custom_field_list.html"
    context_object_name = "definitions"

    def get_queryset(self):
        return CustomFieldDefinition.objects.for_org(self.request.org)


class CustomFieldFormMixin(AdminRequiredMixin, OrgScopedQuerysetMixin, OrgFormKwargsMixin):
    model = CustomFieldDefinition
    form_class = CustomFieldDefinitionForm
    template_name = "contacts/simple_form.html"
    success_url = reverse_lazy("contacts:custom_field_list")
    extra_context = {"back_url": reverse_lazy("contacts:custom_field_list"), "noun": "custom field"}

    def get_initial(self):
        return {"entity": self.request.GET.get("entity", Entity.CONTACT)}

    def form_valid(self, form):
        messages.success(self.request, f"Field “{form.instance.label}” saved.")
        return super().form_valid(form)


class CustomFieldCreateView(CustomFieldFormMixin, CreateView):
    pass


class CustomFieldUpdateView(CustomFieldFormMixin, UpdateView):
    pass


class CustomFieldDeleteView(AdminRequiredMixin, OrgScopedQuerysetMixin, DeleteView):
    model = CustomFieldDefinition
    template_name = "contacts/confirm_delete.html"
    success_url = reverse_lazy("contacts:custom_field_list")
    extra_context = {"warning": "Values already saved on records are kept but hidden until a field with the same key exists."}


# --- Imports ---------------------------------------------------------------------------------------------------------


class ImportListView(ManagerRequiredMixin, ListView):
    template_name = "contacts/import_list.html"
    context_object_name = "jobs"

    def get_queryset(self):
        return ImportJob.objects.for_org(self.request.org).select_related("created_by")


class ImportUploadView(ManagerRequiredMixin, FormView):
    template_name = "contacts/import_upload.html"
    form_class = ImportUploadForm

    def form_valid(self, form):
        upload = form.cleaned_data["file"]
        try:
            headers, rows = read_headers(upload)
        except ValueError as exc:
            form.add_error("file", str(exc))
            return self.form_invalid(form)
        job = ImportJob.objects.create(
            org=self.request.org, file=upload, original_name=upload.name[:255], headers=headers, total_rows=rows,
            created_by=self.request.user, default_owner=self.request.user,
        )
        return redirect("contacts:import_map", pk=job.pk)


class ImportMappingView(ManagerRequiredMixin, OrgScopedQuerysetMixin, UpdateView):
    model = ImportJob
    form_class = ImportMappingForm
    template_name = "contacts/import_map.html"
    context_object_name = "job"

    def get_queryset(self):
        return super().get_queryset().filter(status=ImportStatus.MAPPING)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "targets": import_targets(self.request.org)}

    def form_valid(self, form):
        job = form.save(commit=False)
        job.status = ImportStatus.QUEUED
        job.save()
        form.save_m2m()
        transaction.on_commit(lambda: import_contacts.delay(job.pk))
        return redirect("contacts:import_detail", pk=job.pk)


class ImportDetailView(ManagerRequiredMixin, OrgScopedQuerysetMixin, DetailView):
    model = ImportJob
    template_name = "contacts/import_detail.html"
    context_object_name = "job"

    def get_template_names(self):
        if self.request.headers.get("HX-Request"):
            return ["contacts/partials/import_progress.html"]
        return [self.template_name]
