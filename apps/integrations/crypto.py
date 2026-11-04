"""Encryption for integration secrets (API keys, bot tokens) stored in the database.

The key comes from ``FIELD_ENCRYPTION_KEY`` (a Fernet key). When it is not set,
a key is derived from ``SECRET_KEY`` so development works, but production should
set its own key so rotating ``SECRET_KEY`` does not lock the secrets.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet():
    key = getattr(settings, "FIELD_ENCRYPTION_KEY", "") or ""
    if not key:
        key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest()).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return ""
