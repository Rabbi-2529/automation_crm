"""Builds and sends outbound email: per-workspace SMTP, threading headers, open pixel and click tracking."""

import re
from email.utils import formataddr, make_msgid, parseaddr

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.urls import reverse
from django.utils.html import escape

from .models import ChannelSettings, TrackedLink

URL = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)


def sender(channel_settings):
    """(display name, address) used in From."""
    default_name, default_address = parseaddr(settings.DEFAULT_FROM_EMAIL)
    address = channel_settings.email_from_address or default_address
    name = channel_settings.email_from_name or channel_settings.org.name or default_name
    return name, address


def new_message_id(channel_settings):
    domain = sender(channel_settings)[1].rpartition("@")[2] or "localhost"
    return make_msgid(domain=domain)


def connection_for(channel_settings):
    if not channel_settings.uses_custom_smtp:
        return get_connection()
    return get_connection(
        "django.core.mail.backends.smtp.EmailBackend",
        host=channel_settings.smtp_host, port=channel_settings.smtp_port,
        username=channel_settings.smtp_username or None, password=channel_settings.smtp_password or None,
        use_tls=channel_settings.smtp_security == "starttls", use_ssl=channel_settings.smtp_security == "ssl",
        timeout=20,
    )


def _absolute(path):
    return settings.SITE_URL.rstrip("/") + path


def text_to_html(message, text, channel_settings):
    """Escapes the plain-text body, turns URLs into (tracked) links and blank lines into paragraphs."""
    track = channel_settings.track_clicks

    def linkify(chunk):
        out, last = [], 0
        for match in URL.finditer(chunk):
            url = match.group(0).rstrip(".,;:!?")
            out.append(escape(chunk[last:match.start()]))
            href = url
            if track:
                link = TrackedLink.objects.create(message=message, url=url)
                href = _absolute(reverse("messaging:track_click", args=[link.token]))
            out.append(f'<a href="{escape(href)}">{escape(url)}</a>')
            last = match.start() + len(url)
        out.append(escape(chunk[last:]))
        return "".join(out)

    paragraphs = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    html = "".join(f"<p>{linkify(p).replace(chr(10), '<br>')}</p>" for p in paragraphs)
    if channel_settings.track_opens:
        pixel = _absolute(reverse("messaging:track_open", args=[message.tracking_id]))
        html += f'<img src="{pixel}" width="1" height="1" alt="" style="display:block;border:0">'
    return f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;color:#0f172a">{html}</div>'


def send_email(message):
    """Sends `message` (status/bookkeeping is the caller's job). Raises on SMTP errors."""
    channel_settings = ChannelSettings.for_org(message.org)
    body = message.body_text
    if channel_settings.email_signature:
        body = f"{body}\n\n--\n{channel_settings.email_signature}"
    message.body_html = text_to_html(message, body, channel_settings)
    name, address = sender(channel_settings)
    message.from_address = address

    headers = {"Message-ID": message.message_id}
    if message.in_reply_to:
        headers["In-Reply-To"] = message.in_reply_to
        headers["References"] = message.in_reply_to
    email = EmailMultiAlternatives(
        subject=message.subject, body=body, from_email=formataddr((name, address)), to=[message.to_address],
        reply_to=[channel_settings.email_reply_to] if channel_settings.email_reply_to else None,
        headers=headers, connection=connection_for(channel_settings),
    )
    email.attach_alternative(message.body_html, "text/html")
    email.send(fail_silently=False)
