import json

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.messaging.whatsapp import signature_valid
from apps.orgs.mixins import AdminRequiredMixin, OrgMemberRequiredMixin

from . import facebook, webhooks
from .calendar import build_feed
from .forms import ApiKeyForm, CalendarFeedForm, FacebookPageForm, FacebookSettingsForm, WebhookEndpointForm
from .models import (
    ApiKey,
    CalendarFeed,
    FacebookLead,
    FacebookLeadSettings,
    FacebookPage,
    LeadStatus,
    WebhookDelivery,
    WebhookEndpoint,
    new_webhook_secret,
)

TABS = [("api", "REST API"), ("webhooks", "Webhooks"), ("facebook", "Facebook lead ads")]


def _rate_label(rate):
    """DRF rate "120/min" -> "120 requests a minute"."""
    count, _, period = rate.partition("/")
    unit = {"s": "second", "m": "minute", "h": "hour", "d": "day"}.get(period[:1], period)
    return f"{count} requests {'an' if unit == 'hour' else 'a'} {unit}"


def _back(tab):
    return redirect(reverse("integrations:overview") + f"?tab={tab}")


class IntegrationsView(AdminRequiredMixin, View):
    template_name = "integrations/settings.html"

    def render_page(self, request, tab, **extra):
        org = request.org
        context = {"tab": tab, "tabs": TABS}
        if tab == "api":
            context.update(keys=ApiKey.objects.for_org(org).select_related("created_by"), key_form=extra.pop("key_form", None) or ApiKeyForm(),
                           api_base=request.build_absolute_uri("/api/v1/"), docs_url=reverse("api:docs"),
                           rate_limit=_rate_label(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["api_key"]))
        elif tab == "webhooks":
            context.update(endpoints=WebhookEndpoint.objects.for_org(org))
        else:
            fb = FacebookLeadSettings.for_org(org)
            context.update(
                fb=fb, fb_form=extra.pop("fb_form", None) or FacebookSettingsForm(instance=fb, org=org),
                pages=FacebookPage.objects.for_org(org),
                leads=FacebookLead.objects.for_org(org).select_related("page", "contact")[:50],
                fb_webhook=request.build_absolute_uri(reverse("integrations:facebook_webhook", args=[fb.webhook_token])),
            )
        return render(request, self.template_name, {**context, **extra})

    def get(self, request):
        tab = request.GET.get("tab", "api")
        if tab not in dict(TABS):
            tab = "api"
        return self.render_page(request, tab)


class ApiKeyCreateView(IntegrationsView):
    def post(self, request):
        form = ApiKeyForm(request.POST)
        if not form.is_valid():
            return self.render_page(request, "api", key_form=form)
        key, raw = ApiKey.issue(org=request.org, name=form.cleaned_data["name"], scope=form.cleaned_data["scope"], user=request.user)
        # Rendered (not redirected) so the full key is shown exactly once and never stored.
        return self.render_page(request, "api", new_key=raw, new_key_obj=key)


class ApiKeyRevokeView(AdminRequiredMixin, View):
    def post(self, request, pk):
        key = get_object_or_404(ApiKey.objects.for_org(request.org), pk=pk, revoked_at__isnull=True)
        key.revoke()
        messages.success(request, f"Revoked “{key.name}”. Anything using it stops working now.")
        return _back("api")


# --- Webhooks -------------------------------------------------------------------------------------------------------


class WebhookEditView(AdminRequiredMixin, View):
    template_name = "integrations/webhook_form.html"

    def get_object(self, request, pk):
        return get_object_or_404(WebhookEndpoint.objects.for_org(request.org), pk=pk) if pk else None

    def get(self, request, pk=None):
        endpoint = self.get_object(request, pk)
        return render(request, self.template_name, {"form": WebhookEndpointForm(instance=endpoint, org=request.org), "object": endpoint})

    def post(self, request, pk=None):
        endpoint = self.get_object(request, pk)
        form = WebhookEndpointForm(request.POST, instance=endpoint, org=request.org)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "object": endpoint})
        if endpoint is None:
            form.instance.created_by = request.user
        endpoint = form.save()
        messages.success(request, "Webhook saved. Send a test event to check it.")
        return redirect("integrations:webhook_detail", pk=endpoint.pk)


class WebhookDetailView(AdminRequiredMixin, View):
    template_name = "integrations/webhook_detail.html"

    def get(self, request, pk):
        endpoint = get_object_or_404(WebhookEndpoint.objects.for_org(request.org), pk=pk)
        labels = dict(webhooks.event_choices())
        return render(request, self.template_name, {
            "endpoint": endpoint, "deliveries": endpoint.deliveries.all()[:100],
            "event_labels": [labels.get(e, e) for e in endpoint.events],
        })


class WebhookActionView(AdminRequiredMixin, View):
    """POST action=test|rotate|delete on an endpoint, or action=redeliver&delivery=ID."""

    def post(self, request, pk):
        endpoint = get_object_or_404(WebhookEndpoint.objects.for_org(request.org), pk=pk)
        action = request.POST.get("action")
        if action == "test":
            delivery = webhooks.send_ping(endpoint)
            if delivery.status == "success":
                messages.success(request, f"Test event delivered (HTTP {delivery.response_status}).")
            else:
                messages.error(request, f"Test event failed: {delivery.error}")
        elif action == "redeliver":
            original = get_object_or_404(WebhookDelivery.objects.filter(endpoint=endpoint), pk=request.POST.get("delivery"))
            delivery = webhooks.redeliver(original)
            (messages.success if delivery.status == "success" else messages.error)(
                request, f"Redelivered: {'HTTP ' + str(delivery.response_status) if delivery.response_status else delivery.error}")
        elif action == "rotate":
            endpoint.secret = new_webhook_secret()
            endpoint.save(update_fields=["secret", "updated_at"])
            messages.success(request, "New signing secret created. Update it in the receiving system now.")
        elif action == "delete":
            endpoint.delete()
            messages.success(request, "Webhook deleted.")
            return _back("webhooks")
        else:
            raise Http404
        return redirect("integrations:webhook_detail", pk=endpoint.pk)


# --- Facebook lead ads -------------------------------------------------------------------------------------------------


class FacebookSettingsView(AdminRequiredMixin, View):
    def post(self, request):
        fb = FacebookLeadSettings.for_org(request.org)
        form = FacebookSettingsForm(request.POST, instance=fb, org=request.org)
        if not form.is_valid():
            return IntegrationsView().render_page(request, "facebook", fb_form=form)
        form.save()
        messages.success(request, "Facebook lead ads settings saved.")
        return _back("facebook")


class FacebookPageEditView(AdminRequiredMixin, View):
    template_name = "integrations/facebook_page_form.html"

    def get_object(self, request, pk):
        return get_object_or_404(FacebookPage.objects.for_org(request.org), pk=pk) if pk else None

    def get(self, request, pk=None):
        page = self.get_object(request, pk)
        return render(request, self.template_name, {"form": FacebookPageForm(instance=page, org=request.org), "object": page})

    def post(self, request, pk=None):
        page = self.get_object(request, pk)
        if request.POST.get("action") == "delete" and page:
            page.delete()
            messages.success(request, f"Disconnected {page.name}.")
            return _back("facebook")
        form = FacebookPageForm(request.POST, instance=page, org=request.org)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "object": page})
        form.save()
        messages.success(request, "Page saved. New leads from it are imported automatically.")
        return _back("facebook")


class FacebookLeadRetryView(AdminRequiredMixin, View):
    def post(self, request, pk):
        lead = get_object_or_404(FacebookLead.objects.for_org(request.org).select_related("page"), pk=pk)
        lead = facebook.process(lead)
        if lead.status == LeadStatus.IMPORTED:
            messages.success(request, f"Imported lead {lead.leadgen_id}.")
        else:
            messages.error(request, f"Still failing: {lead.error}")
        return _back("facebook")


@method_decorator(csrf_exempt, name="dispatch")
class FacebookWebhook(View):
    """Meta Page webhook (field "leadgen"). GET answers the subscription check; POST is signed with the app secret."""

    def settings_for(self, token):
        fb = FacebookLeadSettings.objects.filter(webhook_token=token).select_related("org").first()
        if fb is None or not fb.org.is_active:
            raise Http404
        return fb

    def get(self, request, token):
        fb = self.settings_for(token)
        if request.GET.get("hub.mode") == "subscribe" and request.GET.get("hub.verify_token") == fb.verify_token:
            return HttpResponse(request.GET.get("hub.challenge", ""), content_type="text/plain")
        return HttpResponseForbidden("Verify token doesn't match")

    def post(self, request, token):
        fb = self.settings_for(token)
        if not signature_valid(fb.app_secret, request.body, request.headers.get("X-Hub-Signature-256", "")):
            return HttpResponseForbidden("Bad signature")
        try:
            payload = json.loads(request.body or b"{}")
        except ValueError:
            return HttpResponse(status=400)
        facebook.receive(fb, payload)
        return HttpResponse("ok", content_type="text/plain")


# --- Calendar feed -----------------------------------------------------------------------------------------------------


class CalendarSyncView(OrgMemberRequiredMixin, View):
    """Each person's private calendar subscription link (Google Calendar, Outlook, Apple Calendar)."""

    template_name = "integrations/calendar_sync.html"

    def feed(self, request):
        return CalendarFeed.objects.filter(org=request.org, user=request.user).first()

    def get(self, request):
        feed = self.feed(request)
        context = {"feed": feed, "form": CalendarFeedForm(instance=feed)}
        if feed:
            url = request.build_absolute_uri(reverse("tasks:calendar_feed", args=[feed.token]))
            webcal = "webcal://" + url.split("://", 1)[1]
            context.update(feed_url=url, webcal_url=webcal, google_url=f"https://calendar.google.com/calendar/r?cid={webcal}")
        return render(request, self.template_name, context)

    def post(self, request):
        feed = self.feed(request)
        action = request.POST.get("action")
        if action == "create" and feed is None:
            CalendarFeed.objects.create(org=request.org, user=request.user)
            messages.success(request, "Your calendar link is ready.")
        elif action == "rotate" and feed:
            feed.rotate()
            messages.success(request, "Made a new link. The old one stopped working; subscribe again with the new one.")
        elif action == "delete" and feed:
            feed.delete()
            messages.success(request, "Calendar link turned off.")
        elif action == "save" and feed:
            CalendarFeedForm(request.POST, instance=feed).save()
            messages.success(request, "Saved.")
        return redirect("tasks:calendar_sync")


def calendar_feed(request, token):
    feed = CalendarFeed.objects.filter(token=token).select_related("org", "user").first()
    if feed is None or not feed.org.is_active or not feed.user.memberships.filter(org=feed.org, is_active=True).exists():
        raise Http404
    now = timezone.now()
    if feed.last_fetched_at is None or (now - feed.last_fetched_at).total_seconds() > 300:
        CalendarFeed.objects.filter(pk=feed.pk).update(last_fetched_at=now)
    response = HttpResponse(build_feed(feed, now), content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = 'inline; filename="thingbd-crm.ics"'
    response["Cache-Control"] = "private, max-age=300"
    return response
