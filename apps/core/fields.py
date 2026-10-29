"""Encrypted text field for secrets the app must read back (SMTP/IMAP passwords, SMS API keys).

Values are Fernet-encrypted with FIELD_ENCRYPTION_KEY (falls back to a key derived from SECRET_KEY in dev).
Rotating the key makes existing values unreadable, so set FIELD_ENCRYPTION_KEY once per environment.
"""

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models

PREFIX = "enc:"


@lru_cache(maxsize=1)
def _fernet():
    key = getattr(settings, "FIELD_ENCRYPTION_KEY", "") or base64.urlsafe_b64encode(
        hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    ).decode()
    return Fernet(key)


def encrypt(value):
    return PREFIX + _fernet().encrypt(value.encode()).decode()


def decrypt(value):
    if not value.startswith(PREFIX):
        return value  # written before encryption was enabled
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken:
        return ""  # key changed: treat as unset so the admin re-enters it


class EncryptedTextField(models.TextField):
    def from_db_value(self, value, expression, connection):
        return decrypt(value) if value else value

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return encrypt(value) if value else value
