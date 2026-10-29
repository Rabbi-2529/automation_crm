"""WhatsApp through Meta's WhatsApp Cloud API: sending, webhook signatures and parsing inbound payloads.

Meta only lets a business send free text within 24 hours of the contact's last message. Outside that window
the message must use a template approved in WhatsApp Manager (MessageTemplate.whatsapp_template).
"""

import hashlib
import hmac
import uuid
from datetime import timedelta

import requests
from django.utils import timezone

from .models import WhatsAppProvider
from .sms import _console_write, _json, normalize_phone

GRAPH_URL = "https://graph.facebook.com/v21.0"
TIMEOUT = 15
WINDOW = timedelta(hours=24)
# https://developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes
OUTSIDE_WINDOW_CODES = {131047, 470}


class WhatsAppError(Exception):
    pass


def send_whatsapp(channel_settings, to, body, meta=None):
    """Sends text, or the approved template named in `meta` (wa_template/wa_language/wa_params). Returns the wamid."""
    number = normalize_phone(to)
    if len(number) < 8:
        raise WhatsAppError(f"'{to}' doesn't look like a phone number")
    meta = meta or {}
    provider = channel_settings.whatsapp_provider
    if provider == WhatsAppProvider.CONSOLE:
        label = f"template {meta['wa_template']} {meta.get('wa_params', [])}" if meta.get("wa_template") else body
        _console_write(f"[WhatsApp console] to {number}: {label}")
        return f"wamid.console-{uuid.uuid4().hex[:12]}"
    if provider != WhatsAppProvider.CLOUD:
        raise WhatsAppError("WhatsApp isn't set up for this workspace yet (Settings → Messaging channels → WhatsApp).")

    payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": number}
    if meta.get("wa_template"):
        template = {"name": meta["wa_template"], "language": {"code": meta.get("wa_language") or "en"}}
        if params := meta.get("wa_params"):
            template["components"] = [{"type": "body", "parameters": [{"type": "text", "text": p or "-"} for p in params]}]
        payload.update(type="template", template=template)
    else:
        payload.update(type="text", text={"preview_url": True, "body": body})
    url = f"{GRAPH_URL}/{channel_settings.whatsapp_phone_number_id}/messages"
    try:
        response = requests.post(url, json=payload, timeout=TIMEOUT,
                                 headers={"Authorization": f"Bearer {channel_settings.whatsapp_access_token}"})
    except requests.RequestException as exc:
        raise WhatsAppError(f"WhatsApp API unreachable: {exc}") from exc
    data = _json(response)
    if response.status_code >= 400:
        error = data.get("error") or {}
        if error.get("code") in OUTSIDE_WINDOW_CODES:
            raise WhatsAppError("The contact hasn't messaged you in the last 24 hours, so WhatsApp only allows an approved "
                                "template. Pick a template that has a WhatsApp template name.")
        raise WhatsAppError(f"WhatsApp error {error.get('code', response.status_code)}: {error.get('message', response.text[:200])}")
    messages = data.get("messages") or [{}]
    return messages[0].get("id", "")


def signature_valid(app_secret, raw_body, header):
    """Meta signs webhook bodies with the app secret: X-Hub-Signature-256: sha256=<hex>."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def window_open(conversation, now=None):
    """True while free-text replies are allowed: the contact wrote within the last 24 hours."""
    from .models import Direction

    if conversation is None:
        return False
    last_in = conversation.messages.filter(direction=Direction.INBOUND).order_by("-created_at").values_list("created_at", flat=True).first()
    return bool(last_in and (now or timezone.now()) - last_in < WINDOW)


def _text_of(message):
    kind = message.get("type", "text")
    if kind == "text":
        return (message.get("text") or {}).get("body", "")
    if kind in ("image", "video", "document", "audio", "sticker"):
        part = message.get(kind) or {}
        caption = part.get("caption") or part.get("filename") or ""
        return f"[{kind}] {caption}".strip()
    if kind == "button":
        return (message.get("button") or {}).get("text", "[button]")
    if kind == "interactive":
        reply = (message.get("interactive") or {})
        chosen = reply.get("button_reply") or reply.get("list_reply") or {}
        return chosen.get("title", "[interactive reply]")
    if kind == "location":
        loc = message.get("location") or {}
        return f"[location] {loc.get('name') or ''} {loc.get('latitude', '')},{loc.get('longitude', '')}".strip()
    return f"[{kind}]"


def parse_webhook(payload):
    """Flattens a Cloud API webhook body into (messages, statuses).

    messages: [{"from", "id", "body", "name"}]; statuses: [{"id", "status", "error"}].
    """
    messages, statuses = [], []
    for entry in (payload or {}).get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name", "") for c in value.get("contacts") or []}
            for message in value.get("messages") or []:
                messages.append({"from": message.get("from", ""), "id": message.get("id", ""), "body": _text_of(message),
                                 "name": names.get(message.get("from"), "")})
            for status in value.get("statuses") or []:
                errors = status.get("errors") or [{}]
                error = (errors[0].get("message") or errors[0].get("title") or "Failed") if status.get("status") == "failed" else ""
                statuses.append({"id": status.get("id", ""), "status": status.get("status", ""), "error": error})
    return messages, statuses
