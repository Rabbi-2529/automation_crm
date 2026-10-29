import base64

from django.contrib import messages
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import CreateView, DeleteView, ListView, TemplateView, UpdateView

from apps.contacts.models import Contact
from apps.orgs.mixins import (
    AdminRequiredMixin,
    ManagerRequiredMixin,
    OrgFormKwargsMixin,
    OrgMemberRequiredMixin,
    OrgScopedQuerysetMixin,
)
from apps.pipeline.models import Deal

from . import email as email_backend
from . import services
from .forms import CallLogForm, ComposeForm, EmailSettingsForm, ImapSettingsForm, ReplyForm, SmsSettingsForm, TemplateForm
from .inbound import fetch_inbox, twilio_signature_valid
from .models import Channel, ChannelSettings, Conversation, Message, MessageTemplate, SmsProvider, TrackedLink
from .rendering import VARIABLES, build_context, render as render_variables
from .sms import send_sms

PIXEL = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


# --- Compose ---------------------------------------------------------------------------------------------------------


class ComposeView(OrgMemberRequiredMixin, View):
    """New email/SMS to a contact: /messages/new/?channel=email&contact=ID[&deal=ID]"""

    template_name = "messaging/compose.html"

    def setup_context(self, request):
        self.channel = request.GET.get("channel", Channel.EMAIL)
        if self.channel not in Channel.values:
            raise Http404
        self.contact = get_object_or_404(Contact.objects.for_org(request.org).select_related("company"), pk=request.GET.get("contact"))
        self.deal = Deal.objects.for_org(request.org).filter(pk=request.GET.get("deal") or 0, contact=self.contact).first()
        self.channel_settings = ChannelSettings.for_org(request.org)

    def form(self, data=None):
        initial = {"to": self.contact.email if self.channel == Channel.EMAIL else self.contact.phone, "deal": self.deal}
        return ComposeForm(data, org=self.request.org, channel=self.channel, contact=self.contact, initial=initial)

    def render_page(self, form):
        return render(self.request, self.template_name, {
            "form": form, "contact": self.contact, "channel": self.channel, "variables": VARIABLES,
            "channel_settings": self.channel_settings,
            "sms_not_ready": self.channel == Channel.SMS and not self.channel_settings.sms_ready,
        })

    def get(self, request):
        self.setup_context(request)
        return self.render_page(self.form())

    def post(self, request):
        self.setup_context(request)
        form = self.form(request.POST)
        if not form.is_valid():
            return self.render_page(form)
        data = form.cleaned_data
        message = services.compose(
            org=request.org, user=request.user, channel=self.channel, to_address=data["to"], subject=data.get("subject", ""),
            body=data["body"], contact=self.contact, deal=data.get("deal"), template=data.get("template"),
        )
        # Delivery runs after this request commits (in Celery), so the outcome lands on the timeline, not here.
        messages.success(request, f"Sending {message.get_channel_display().lower()} to {message.to_address}. "
                                  "It appears on the timeline once sent (or with the error if it fails).")
        return redirect("contacts:contact_detail", pk=self.contact.pk)


class TemplatePreviewView(OrgMemberRequiredMixin, View):
    """Template with variables filled in for the compose box: ?contact=&deal="""

    def get(self, request, pk):
        template = get_object_or_404(MessageTemplate.objects.for_org(request.org), pk=pk)
        contact = Contact.objects.for_org(request.org).select_related("company").filter(pk=request.GET.get("contact") or 0).first()
        deal = Deal.objects.for_org(request.org).select_related("stage", "org").filter(pk=request.GET.get("deal") or 0).first()
        values = build_context(org=request.org, user=request.user, contact=contact, deal=deal)
        return JsonResponse({"subject": render_variables(template.subject, values), "body": render_variables(template.body, values)})


# --- Inbox -----------------------------------------------------------------------------------------------------------

INBOX_FILTERS = [("all", "All"), ("unread", "Unread"), ("email", "Email"), ("sms", "SMS"), ("mine", "My contacts"),
                 ("unknown", "Unknown senders"), ("archived", "Archived")]


class InboxView(OrgMemberRequiredMixin, TemplateView):
    template_name = "messaging/inbox.html"

    def conversations(self):
        request = self.request
        box = request.GET.get("box", "all")
        qs = Conversation.objects.for_org(request.org).select_related("contact").filter(last_message_at__isnull=False)
        qs = qs.filter(is_archived=(box == "archived"))
        if box == "unread":
            qs = qs.filter(is_unread=True)
        elif box in Channel.values:
            qs = qs.filter(channel=box)
        elif box == "mine":
            qs = qs.filter(contact__owner=request.user)
        elif box == "unknown":
            qs = qs.filter(contact__isnull=True)
        if q := request.GET.get("q", "").strip():
            qs = qs.filter(Q(subject__icontains=q) | Q(address__icontains=q) | Q(contact__first_name__icontains=q)
                           | Q(contact__last_name__icontains=q) | Q(messages__body_text__icontains=q)).distinct()
        return box, qs

    def get_context_data(self, **kwargs):
        box, conversations = self.conversations()
        selected = None
        if pk := self.request.GET.get("c"):
            selected = get_object_or_404(Conversation.objects.for_org(self.request.org).select_related("contact"), pk=pk)
            if selected.is_unread:
                Conversation.objects.filter(pk=selected.pk).update(is_unread=False)
                selected.is_unread = False
        return super().get_context_data(
            box=box, boxes=INBOX_FILTERS, conversations=conversations[:100], selected=selected,
            thread=selected.messages.select_related("sent_by").order_by("created_at") if selected else [],
            reply_form=ReplyForm(), q=self.request.GET.get("q", ""), **kwargs,
        )


class ConversationReplyView(OrgMemberRequiredMixin, View):
    def post(self, request, pk):
        conversation = get_object_or_404(Conversation.objects.for_org(request.org), pk=pk)
        form = ReplyForm(request.POST)
        back = reverse("messaging:inbox") + f"?c={conversation.pk}"
        if not form.is_valid():
            messages.error(request, "Write something before sending.")
            return redirect(back)
        last = conversation.messages.order_by("-created_at").first()
        reply_to = conversation.messages.filter(direction="in").order_by("-created_at").first() or last
        services.compose(
            org=request.org, user=request.user, channel=conversation.channel, to_address=conversation.address,
            subject=conversation.subject, body=form.cleaned_data["body"], contact=conversation.contact,
            deal=last.deal if last else None, reply_to=reply_to,
        )
        messages.success(request, "Reply sent.")
        return redirect(back)


class ConversationArchiveView(OrgMemberRequiredMixin, View):
    def post(self, request, pk):
        conversation = get_object_or_404(Conversation.objects.for_org(request.org), pk=pk)
        conversation.is_archived = not conversation.is_archived
        conversation.is_unread = False
        conversation.save(update_fields=["is_archived", "is_unread", "updated_at"])
        messages.info(request, "Conversation archived." if conversation.is_archived else "Conversation moved back to the inbox.")
        return redirect("messaging:inbox")


class ConversationCreateContactView(OrgMemberRequiredMixin, View):
    """Unknown sender -> new contact, linked to the conversation and its messages."""

    def post(self, request, pk):
        conversation = get_object_or_404(Conversation.objects.for_org(request.org), pk=pk, contact__isnull=True)
        is_email = conversation.channel == Channel.EMAIL
        contact = (Contact.objects.for_org(request.org).filter(email=conversation.address).first() if is_email else None) or Contact.objects.create(
            org=request.org, owner=request.user, created_by=request.user,
            email=conversation.address if is_email else "", phone="" if is_email else conversation.address,
            first_name=request.POST.get("name", "").strip()[:100],
        )
        conversation.contact = contact
        conversation.save(update_fields=["contact", "updated_at"])
        conversation.messages.update(contact=contact)
        messages.success(request, f"Saved as contact {contact.display_name}.")
        return redirect(reverse("messaging:inbox") + f"?c={conversation.pk}")


# --- Calls -----------------------------------------------------------------------------------------------------------


class CallLogCreateView(OrgMemberRequiredMixin, View):
    template_name = "messaging/call_form.html"

    def get_contact(self, request):
        return get_object_or_404(Contact.objects.for_org(request.org), pk=request.GET.get("contact"))

    def get(self, request):
        contact = self.get_contact(request)
        form = CallLogForm(contact=contact, initial={"deal": request.GET.get("deal")})
        return render(request, self.template_name, {"form": form, "contact": contact})

    def post(self, request):
        contact = self.get_contact(request)
        form = CallLogForm(request.POST, contact=contact)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "contact": contact})
        call = form.save(commit=False)
        call.org, call.contact, call.user = request.org, contact, request.user
        call.save()
        services.log_call(call)
        messages.success(request, "Call logged.")
        return redirect("contacts:contact_detail", pk=contact.pk)


# --- Templates -------------------------------------------------------------------------------------------------------


class TemplateListView(OrgMemberRequiredMixin, ListView):
    template_name = "messaging/template_list.html"
    context_object_name = "templates"

    def get_queryset(self):
        return MessageTemplate.objects.for_org(self.request.org).select_related("created_by")


class TemplateFormMixin(ManagerRequiredMixin, OrgScopedQuerysetMixin, OrgFormKwargsMixin):
    model = MessageTemplate
    form_class = TemplateForm
    template_name = "messaging/template_form.html"
    success_url = reverse_lazy("messaging:template_list")
    extra_context = {"variables": VARIABLES}

    def form_valid(self, form):
        if not form.instance.pk:
            form.instance.created_by = self.request.user
        messages.success(self.request, f"Template “{form.instance.name}” saved.")
        return super().form_valid(form)


class TemplateCreateView(TemplateFormMixin, CreateView):
    def get_initial(self):
        return {"channel": self.request.GET.get("channel", Channel.EMAIL)}


class TemplateUpdateView(TemplateFormMixin, UpdateView):
    pass


class TemplateDeleteView(ManagerRequiredMixin, OrgScopedQuerysetMixin, DeleteView):
    model = MessageTemplate
    template_name = "contacts/confirm_delete.html"
    success_url = reverse_lazy("messaging:template_list")


# --- Channel settings ------------------------------------------------------------------------------------------------

SETTINGS_FORMS = {"email": EmailSettingsForm, "imap": ImapSettingsForm, "sms": SmsSettingsForm}


class ChannelSettingsView(AdminRequiredMixin, View):
    template_name = "messaging/channel_settings.html"

    def render_page(self, request, forms=None, tab="email"):
        channel_settings = ChannelSettings.for_org(request.org)
        forms = forms or {}
        context = {f"{name}_form": forms.get(name) or form_class(instance=channel_settings, prefix=name)
                   for name, form_class in SETTINGS_FORMS.items()}
        return render(request, self.template_name, {
            **context, "tab": tab, "channel_settings": channel_settings, "sender": email_backend.sender(channel_settings),
            "twilio_webhook": request.build_absolute_uri(reverse("messaging:twilio_sms", args=[channel_settings.webhook_token])),
        })

    def get(self, request):
        return self.render_page(request, tab=request.GET.get("tab", "email"))

    def post(self, request):
        section = request.POST.get("section")
        if section not in SETTINGS_FORMS:
            raise Http404
        form = SETTINGS_FORMS[section](request.POST, instance=ChannelSettings.for_org(request.org), prefix=section)
        if not form.is_valid():
            return self.render_page(request, {section: form}, tab=section)
        form.save()
        messages.success(request, "Settings saved.")
        return redirect(reverse("messaging:channel_settings") + f"?tab={section}")


class ChannelTestView(AdminRequiredMixin, View):
    """POST action=email|sms|imap: send a test / check the mailbox now."""

    def post(self, request):
        channel_settings = ChannelSettings.for_org(request.org)
        action = request.POST.get("action")
        try:
            if action == "email":
                from django.core.mail import EmailMessage

                name, address = email_backend.sender(channel_settings)
                EmailMessage("ThingBD CRM test email", "Your email settings work.", f"{name} <{address}>", [request.user.email],
                             connection=email_backend.connection_for(channel_settings)).send(fail_silently=False)
                messages.success(request, f"Test email sent to {request.user.email}.")
            elif action == "sms":
                to = request.POST.get("to") or request.user.phone
                send_sms(channel_settings, to, "ThingBD CRM test SMS: your SMS settings work.")
                messages.success(request, f"Test SMS sent to {to}.")
            elif action == "imap":
                stored = fetch_inbox(channel_settings)
                channel_settings.refresh_from_db()
                if channel_settings.imap_last_error:
                    messages.error(request, f"Mailbox check failed: {channel_settings.imap_last_error}")
                else:
                    messages.success(request, f"Mailbox checked: {stored} new message(s).")
            else:
                raise Http404
        except Exception as exc:  # noqa: BLE001 - shown to the admin testing their settings
            messages.error(request, f"Test failed: {type(exc).__name__}: {exc}")
        tab = {"email": "email", "sms": "sms", "imap": "imap"}.get(action, "email")
        return redirect(reverse("messaging:channel_settings") + f"?tab={tab}")


# --- Public endpoints (no login) --------------------------------------------------------------------------------------


def track_open(request, tracking_id):
    message = Message.objects.filter(tracking_id=tracking_id).select_related("org", "contact", "deal").first()
    if message:
        services.track_open(message)
    response = HttpResponse(PIXEL, content_type="image/gif")
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


def track_click(request, token):
    link = get_object_or_404(TrackedLink.objects.select_related("message__org", "message__contact", "message__deal"), token=token)
    services.track_click(link)
    return redirect(link.url)  # only ever a URL we stored when sending, so this isn't an open redirect


@method_decorator(csrf_exempt, name="dispatch")
class TwilioSmsWebhook(View):
    """Incoming SMS from Twilio. Configure the URL shown in Settings → Email & SMS on your Twilio number."""

    def post(self, request, token):
        channel_settings = ChannelSettings.objects.filter(webhook_token=token, sms_provider=SmsProvider.TWILIO).select_related("org").first()
        if channel_settings is None:
            raise Http404
        params = request.POST.dict()
        if not twilio_signature_valid(channel_settings.twilio_auth_token, request.build_absolute_uri(), params,
                                      request.headers.get("X-Twilio-Signature", "")):
            return HttpResponseForbidden("Bad signature")
        services.ingest_sms(channel_settings.org, params.get("From", ""), params.get("Body", ""), params.get("MessageSid", ""))
        return HttpResponse('<?xml version="1.0" encoding="UTF-8"?><Response></Response>', content_type="text/xml")

