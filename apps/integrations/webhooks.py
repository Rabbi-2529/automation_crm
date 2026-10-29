"""Outgoing webhooks: a signed JSON POST to each subscribed endpoint for every matching timeline event.

Signature (like Stripe's): X-ThingBD-Signature: t=<unix time>,v1=<hex HMAC-SHA256 of "<t>.<raw body>" with the endpoint secret>.
Failed deliveries are retried with backoff; an endpoint that keeps failing is switched off.
"""

import hashlib
import hmac
import json
import time
from datetime import timedelta

import requests
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import F
from django.utils import timezone

from apps.activities.models import ActivityKind
from apps.core.net import check_public_url

from .models import DeliveryStatus, WebhookDelivery, WebhookEndpoint

TIMEOUT = 10
RETRY_DELAYS = [60, 5 * 60, 30 * 60, 2 * 3600, 6 * 3600]  # after attempts 1-5; attempt 6 is the last
LEASE = timedelta(minutes=5)  # a claimed delivery isn't picked up again for this long
DISABLE_AFTER = 50  # consecutive failed attempts
USER_AGENT = "ThingBD-CRM-Webhooks/1"
PING = "ping"


def event_choices():
    return [(k, label) for k, label in ActivityKind.choices if k != ActivityKind.AUTOMATION]


def build_payload(activity):
    from .api.serializers import ActivitySerializer, CompanySerializer, ContactSerializer, DealSerializer

    context = {"org": activity.org}
    data = {"activity": ActivitySerializer(activity, context=context).data}
    for name, serializer in (("contact", ContactSerializer), ("company", CompanySerializer), ("deal", DealSerializer)):
        obj = getattr(activity, name)
        data[name] = serializer(obj, context=context).data if obj is not None else None
    return {"id": f"evt_{activity.pk}", "event": activity.kind, "created_at": activity.created_at,
            "workspace": {"id": activity.org_id, "name": activity.org.name}, "data": data}


def dispatch(activity):
    """After-commit hook from activities.record(): queue a delivery per endpoint that wants this kind."""
    endpoints = [e for e in WebhookEndpoint.objects.filter(org_id=activity.org_id, is_active=True) if e.wants(activity.kind)]
    if not endpoints:
        return
    from .tasks import deliver_webhook

    payload = json.loads(json.dumps(build_payload(activity), cls=DjangoJSONEncoder))
    for endpoint in endpoints:
        delivery = WebhookDelivery.objects.create(org_id=activity.org_id, endpoint=endpoint, event=activity.kind,
                                                  payload=payload, next_attempt_at=timezone.now())
        deliver_webhook.delay(delivery.pk)


def sign(secret, timestamp, body):
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def _claim(delivery, now):
    """True if this worker got the delivery (guards against the queued task and the retry scan both sending it)."""
    return bool(WebhookDelivery.objects.filter(pk=delivery.pk, status=DeliveryStatus.PENDING, next_attempt_at__lte=now)
                .update(next_attempt_at=now + LEASE))


def deliver(delivery, claim=True, now=None):
    if delivery.status != DeliveryStatus.PENDING or (claim and not _claim(delivery, now or timezone.now())):
        return delivery
    endpoint = delivery.endpoint
    body = json.dumps(delivery.payload, cls=DjangoJSONEncoder, separators=(",", ":")).encode()
    timestamp = int(time.time())
    delivery.attempts += 1
    delivery.response_status, delivery.response_body, delivery.error = None, "", ""
    try:
        if not endpoint.is_active and delivery.event != PING:
            raise RuntimeError("Endpoint is switched off")
        check_public_url(endpoint.url)
        response = requests.post(endpoint.url, data=body, timeout=TIMEOUT, allow_redirects=False, headers={
            "Content-Type": "application/json", "User-Agent": USER_AGENT, "X-ThingBD-Event": delivery.event,
            "X-ThingBD-Delivery": str(delivery.event_id), "X-ThingBD-Signature": f"t={timestamp},v1={sign(endpoint.secret, timestamp, body)}",
        })
        delivery.response_status = response.status_code
        delivery.response_body = response.text[:1000]
        ok = 200 <= response.status_code < 300
        if not ok:
            delivery.error = f"HTTP {response.status_code}"
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        ok = False
        delivery.error = f"{type(exc).__name__}: {exc}"[:500]

    now = timezone.now()
    if ok:
        delivery.status, delivery.delivered_at, delivery.next_attempt_at = DeliveryStatus.SUCCESS, now, None
        WebhookEndpoint.objects.filter(pk=endpoint.pk).update(consecutive_failures=0, last_delivery_at=now)
    else:
        if delivery.attempts > len(RETRY_DELAYS) or delivery.event == PING:
            delivery.status, delivery.next_attempt_at = DeliveryStatus.FAILED, None
        else:
            delivery.next_attempt_at = now + timedelta(seconds=RETRY_DELAYS[delivery.attempts - 1])
        WebhookEndpoint.objects.filter(pk=endpoint.pk).update(consecutive_failures=F("consecutive_failures") + 1)
        WebhookEndpoint.objects.filter(pk=endpoint.pk, is_active=True, consecutive_failures__gte=DISABLE_AFTER).update(
            is_active=False, disabled_reason=f"Switched off after {DISABLE_AFTER} failed attempts in a row ({delivery.error})"[:255])
    delivery.save(update_fields=["attempts", "status", "next_attempt_at", "response_status", "response_body", "error",
                                 "delivered_at", "updated_at"])
    return delivery


def retry_due(now=None, limit=200):
    now = now or timezone.now()
    sent = 0
    due = (WebhookDelivery.objects.filter(status=DeliveryStatus.PENDING, next_attempt_at__lte=now, endpoint__is_active=True)
           .select_related("endpoint").order_by("next_attempt_at")[:limit])
    for delivery in due:
        deliver(delivery, now=now)
        sent += 1
    # Deliveries for endpoints that were switched off are given up on.
    WebhookDelivery.objects.filter(status=DeliveryStatus.PENDING, endpoint__is_active=False).update(
        status=DeliveryStatus.FAILED, next_attempt_at=None, error="Endpoint is switched off")
    return sent


def send_ping(endpoint):
    """A test event, sent right away (not retried) so the settings page can show the result."""
    payload = {"id": f"evt_ping_{int(time.time())}", "event": PING, "created_at": timezone.now(),
               "workspace": {"id": endpoint.org_id, "name": endpoint.org.name},
               "data": {"message": "Test event from ThingBD CRM. Reply with any 2xx status."}}
    payload = json.loads(json.dumps(payload, cls=DjangoJSONEncoder))
    delivery = WebhookDelivery.objects.create(org_id=endpoint.org_id, endpoint=endpoint, event=PING, payload=payload,
                                              next_attempt_at=timezone.now())
    return deliver(delivery, claim=False)


def redeliver(delivery):
    copy = WebhookDelivery.objects.create(org_id=delivery.org_id, endpoint=delivery.endpoint, event=delivery.event,
                                          payload=delivery.payload, next_attempt_at=timezone.now())
    return deliver(copy, claim=False)


def prune(now=None):
    now = now or timezone.now()
    old_ok = WebhookDelivery.objects.filter(status=DeliveryStatus.SUCCESS, created_at__lt=now - timedelta(days=30))
    old_failed = WebhookDelivery.objects.filter(status=DeliveryStatus.FAILED, created_at__lt=now - timedelta(days=90))
    return old_ok.delete()[0] + old_failed.delete()[0]
