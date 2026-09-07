"""API key minting and hashing (T-7.1a).

Plaintext form: ``rf_<43 url-safe chars>``.  Shown to the caller exactly
once, at creation.  Only :func:`hash_key` of the plaintext is persisted
(``ApiKey.key_hash``); a leak of the store never yields a usable key.
"""

from __future__ import annotations

import hashlib
import secrets

_PREFIX = "rf_"
_NBYTES = 32  # secrets.token_urlsafe(32) -> 43 chars
_DISPLAY = len(_PREFIX) + 6


def mint_api_key() -> tuple[str, str, str]:
    """Return ``(plaintext, display_prefix, key_hash)``."""
    plaintext = _PREFIX + secrets.token_urlsafe(_NBYTES)
    return plaintext, plaintext[:_DISPLAY], hash_key(plaintext)


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()
