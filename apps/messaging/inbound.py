"""Polls a workspace's IMAP mailbox and verifies Twilio webhooks."""

import base64
import hashlib
import hmac
import imaplib
import logging

from django.utils import timezone

from .services import ingest_email

logger = logging.getLogger(__name__)
MAX_PER_RUN = 200


def fetch_inbox(channel_settings):
    """Pulls new mail (by UID) into the inbox. Returns how many messages were stored."""
    cs = channel_settings
    stored = 0
    try:
        client = imaplib.IMAP4_SSL(cs.imap_host, cs.imap_port, timeout=30)
        try:
            client.login(cs.imap_username, cs.imap_password)
            status, _ = client.select(cs.imap_folder or "INBOX", readonly=True)
            if status != "OK":
                raise imaplib.IMAP4.error(f"Can't open folder {cs.imap_folder!r}")
            if cs.imap_last_uid == 0:
                # First run: start from now instead of importing the whole mailbox history.
                _, data = client.uid("search", None, "ALL")
                uids = [int(u) for u in (data[0] or b"").split()]
                cs.imap_last_uid = max(uids) if uids else 0
                uids = []
            else:
                _, data = client.uid("search", None, f"UID {cs.imap_last_uid + 1}:*")
                uids = sorted(int(u) for u in (data[0] or b"").split() if int(u) > cs.imap_last_uid)[:MAX_PER_RUN]
            for uid in uids:
                _, fetched = client.uid("fetch", str(uid), "(BODY.PEEK[])")
                raw = next((part[1] for part in fetched if isinstance(part, tuple)), None)
                if raw and ingest_email(cs.org, raw):
                    stored += 1
                cs.imap_last_uid = uid
        finally:
            try:
                client.logout()
            except Exception:  # noqa: BLE001 - connection already gone
                pass
        cs.imap_last_error = ""
    except Exception as exc:  # noqa: BLE001 - surfaced on the settings page
        logger.warning("IMAP fetch failed for org %s: %s", cs.org_id, exc)
        cs.imap_last_error = f"{type(exc).__name__}: {exc}"[:1000]
    cs.imap_last_checked_at = timezone.now()
    cs.save(update_fields=["imap_last_uid", "imap_last_error", "imap_last_checked_at", "updated_at"])
    return stored


def twilio_signature_valid(auth_token, url, params, signature):
    """https://www.twilio.com/docs/usage/security#validating-requests"""
    if not auth_token or not signature:
        return False
    payload = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)
