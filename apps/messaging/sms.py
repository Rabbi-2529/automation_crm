"""SMS providers. Each returns the provider's message reference or raises SmsError."""

import re
import sys
import uuid
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

import requests

from .models import SmsProvider

TIMEOUT = 15


class SmsError(Exception):
    pass


def normalize_phone(phone, country_code="880"):
    """Digits in international form without '+': 01711-000000 / +880 1711 000000 -> 8801711000000."""
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 11 and country_code == "880":
        digits = country_code + digits[1:]
    return digits


def send_sms(channel_settings, to, body):
    number = normalize_phone(to)
    if len(number) < 8:
        raise SmsError(f"'{to}' doesn't look like a phone number")
    provider = channel_settings.sms_provider
    if provider == SmsProvider.CONSOLE:
        _console_write(f"[SMS console] to {number}: {body}")
        return f"console-{uuid.uuid4().hex[:12]}"
    if provider == SmsProvider.TWILIO:
        return _twilio(channel_settings, number, body)
    if provider == SmsProvider.HTTP:
        return _http(channel_settings, number, body)
    raise SmsError("SMS isn't set up for this workspace yet (Settings → Messaging channels).")


def _twilio(cs, number, body):
    url = f"https://api.twilio.com/2010-04-01/Accounts/{cs.twilio_account_sid}/Messages.json"
    data = {"To": f"+{number}", "From": cs.twilio_from_number or cs.sms_sender_id, "Body": body}
    try:
        response = requests.post(url, data=data, auth=(cs.twilio_account_sid, cs.twilio_auth_token), timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise SmsError(f"Twilio unreachable: {exc}") from exc
    payload = _json(response)
    if response.status_code >= 400:
        raise SmsError(f"Twilio error {response.status_code}: {payload.get('message', response.text[:200])}")
    return payload.get("sid", "")


def _http(cs, number, body):
    url = cs.http_url_template.format(
        to=quote(number), message=quote(body), sender=quote(cs.sms_sender_id), api_key=quote(cs.http_api_key),
    )
    try:
        if cs.http_method == "POST":
            parts = urlsplit(url)
            response = requests.post(urlunsplit(parts._replace(query="")), data=dict(parse_qsl(parts.query)), timeout=TIMEOUT)
        else:
            response = requests.get(url, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise SmsError(f"SMS gateway unreachable: {exc}") from exc
    text = response.text[:500]
    if response.status_code >= 400:
        raise SmsError(f"SMS gateway error {response.status_code}: {text[:200]}")
    if cs.http_success_text and cs.http_success_text not in text:
        raise SmsError(f"SMS gateway didn't confirm: {text[:200]}")
    return text[:100]


def _console_write(text):
    """Dev provider output. Windows consoles often can't encode Bangla, which must not make the send fail."""
    stream = sys.stdout
    line = text + "\n"
    try:
        stream.write(line)
    except UnicodeEncodeError:
        encoding = stream.encoding or "ascii"
        stream.write(line.encode(encoding, "backslashreplace").decode(encoding))
    stream.flush()


def _json(response):
    try:
        return response.json()
    except ValueError:
        return {}
