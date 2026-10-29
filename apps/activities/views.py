import mimetypes

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.views import View

from apps.contacts.models import Company, Contact
from apps.orgs.mixins import OrgMemberRequiredMixin
from apps.orgs.models import Role

from .forms import AttachmentForm, NoteForm
from .models import ActivityKind, Attachment, Note
from .services import record

TARGETS = {"contact": Contact, "company": Company}


class TargetMixin(OrgMemberRequiredMixin):
    """Resolves /<target>/<pk>/ to an org-owned Contact or Company."""

    def get_target(self):
        model = TARGETS.get(self.kwargs["target"])
        if model is None:
            raise Http404
        return get_object_or_404(model.objects.for_org(self.request.org), pk=self.kwargs["pk"])

    def target_kw(self, target):
        return {self.kwargs["target"]: target}

    def back(self, target):
        return redirect(f"contacts:{self.kwargs['target']}_detail", pk=target.pk)


class NoteCreateView(TargetMixin, View):
    def post(self, request, target, pk):
        obj = self.get_target()
        form = NoteForm(request.POST)
        if form.is_valid():
            note = Note.objects.create(org=request.org, author=request.user, body=form.cleaned_data["body"], **self.target_kw(obj))
            record(org=request.org, kind=ActivityKind.NOTE_ADDED, actor=request.user, note=note,
                   summary="Added a note", **self.target_kw(obj))
        else:
            messages.error(request, "The note was empty or too long.")
        return self.back(obj)


class NoteDeleteView(OrgMemberRequiredMixin, View):
    def post(self, request, pk):
        note = get_object_or_404(Note.objects.for_org(request.org), pk=pk)
        if note.author_id != request.user.pk and not request.membership.has_role(Role.MANAGER):
            raise PermissionDenied
        target = ("contact", note.contact_id) if note.contact_id else ("company", note.company_id)
        note.delete()
        messages.success(request, "Note deleted.")
        return redirect(f"contacts:{target[0]}_detail", pk=target[1])


class AttachmentUploadView(TargetMixin, View):
    def post(self, request, target, pk):
        obj = self.get_target()
        form = AttachmentForm(request.POST, request.FILES)
        if form.is_valid():
            upload = form.cleaned_data["file"]
            attachment = Attachment.objects.create(
                org=request.org, uploaded_by=request.user, file=upload, original_name=upload.name[:255], size=upload.size,
                **self.target_kw(obj),
            )
            record(org=request.org, kind=ActivityKind.ATTACHMENT_ADDED, actor=request.user, attachment=attachment,
                   summary=f"Attached {attachment.original_name}", **self.target_kw(obj))
            messages.success(request, f"Uploaded {attachment.original_name}.")
        else:
            messages.error(request, " ".join(form.errors.get("file", ["Upload failed."])))
        return self.back(obj)


class AttachmentDownloadView(OrgMemberRequiredMixin, View):
    def get(self, request, pk):
        attachment = get_object_or_404(Attachment.objects.for_org(request.org), pk=pk)
        content_type = mimetypes.guess_type(attachment.original_name)[0] or "application/octet-stream"
        # Always download (never render inline) so uploaded HTML/SVG can't run in our origin.
        return FileResponse(attachment.file.open("rb"), as_attachment=True, filename=attachment.original_name,
                            content_type=content_type)


class AttachmentDeleteView(OrgMemberRequiredMixin, View):
    def post(self, request, pk):
        attachment = get_object_or_404(Attachment.objects.for_org(request.org), pk=pk)
        if attachment.uploaded_by_id != request.user.pk and not request.membership.has_role(Role.MANAGER):
            raise PermissionDenied
        target = ("contact", attachment.contact_id) if attachment.contact_id else ("company", attachment.company_id)
        attachment.file.delete(save=False)
        attachment.delete()
        messages.success(request, "File deleted.")
        return redirect(f"contacts:{target[0]}_detail", pk=target[1])
