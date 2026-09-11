"""Telnyx webhook signature verification.

Telnyx signs with **Ed25519**, not Twilio's HMAC-SHA1:

* ``telnyx-signature-ed25519`` — base64 signature
* ``telnyx-timestamp`` — unix seconds
* signed message is ``f"{timestamp}|{raw_body}"``, verified against the
  account public key from Portal → Account Settings → Keys & Credentials.

Two things this must get right. The body has to be the **raw bytes** — any
re-serialisation changes the signature. And the timestamp needs a tolerance
so a captured request cannot be replayed later; Telnyx's own SDKs use five
minutes.

Ed25519 comes from ``cryptography``, which the project already depends on
for at-rest column encryption (``packages/db/crypto.py``). It is declared in
the ``db`` extra, so it is imported lazily here and the failure is explicit
rather than an ImportError at startup.
"""

from __future__ import annotations

import base64
import binascii
import time

_TOLERANCE_S = 300.0  # Telnyx SDK default; blocks replay of a captured POST


class SignatureUnavailableError(RuntimeError):
    """``cryptography`` is not installed, so signatures cannot be checked.

    Raised rather than returning False: "we cannot verify" and "this is
    forged" are different facts and must not look the same.
    """


def verify_signature(
    *,
    public_key: str,
    body: bytes,
    signature: str,
    timestamp: str,
    now: float | None = None,
    tolerance_s: float = _TOLERANCE_S,
) -> bool:
    if not public_key or not signature or not timestamp:
        return False

    try:
        sent_at = float(timestamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - sent_at) > tolerance_s:
        return False  # too old (or too far future) to be a live request

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise SignatureUnavailableError(
            "Telnyx webhook verification needs 'cryptography' (the 'db' extra)"
        ) from exc

    try:
        key_bytes = base64.b64decode(public_key, validate=True)
        sig_bytes = base64.b64decode(signature, validate=True)
    except (binascii.Error, ValueError):
        return False
    if len(key_bytes) != 32 or len(sig_bytes) != 64:
        return False

    signed = timestamp.encode() + b"|" + body
    try:
        ed25519.Ed25519PublicKey.from_public_bytes(key_bytes).verify(sig_bytes, signed)
    except InvalidSignature:
        return False
    return True
