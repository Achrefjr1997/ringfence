"""Single-purpose, stateless action tokens (T-7.1d).

Email verification, password reset and operator/guardian invites all need
a token that says "the holder may do exactly one thing, once, before it
expires".  Same HMAC construction as :mod:`apps.gateway.tokens`, so no
storage and no token table:

    <payload_b64url>.<sig_b64url>
    payload = {"p": purpose, "s": subject, "b": bind, "exp": epoch}

``bind`` is an optional value the caller ties the token to -- for reset and
invite it is a slice of the account's current ``password_hash``.  The
moment the password changes the bind no longer matches, so a used token
cannot be replayed.  ``verify`` tokens carry no bind and simply lapse.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Literal

Purpose = Literal["verify", "reset", "invite"]

_TTL_S: dict[Purpose, float] = {
    "verify": 3 * 24 * 3600.0,
    "reset": 3600.0,
    "invite": 7 * 24 * 3600.0,
}


@dataclass(frozen=True, slots=True)
class PurposeClaims:
    purpose: Purpose
    subject: str
    bind: str
    expires_at: float


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(secret: str, payload_b64: str) -> str:
    return _b64u(hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest())


def bind_for(password_hash: str) -> str:
    """The binding value for reset / invite tokens: a stable, non-reversible
    slice of the stored hash that changes whenever the password changes."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def issue_purpose_token(
    *,
    purpose: Purpose,
    subject: str,
    secret: str,
    bind: str = "",
    ttl_s: float | None = None,
    now: float | None = None,
) -> str:
    exp = (time.time() if now is None else now) + (_TTL_S[purpose] if ttl_s is None else ttl_s)
    payload = _b64u(
        json.dumps(
            {"p": purpose, "s": subject, "b": bind, "exp": exp},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    return f"{payload}.{_sign(secret, payload)}"


def read_purpose_token(
    token: str, *, purpose: Purpose, secret: str, now: float | None = None
) -> PurposeClaims | None:
    """Parsed claims, or ``None`` if malformed, mis-signed, wrong purpose, or expired."""
    try:
        payload_b64, sig = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(_sign(secret, payload_b64), sig):
        return None
    try:
        data = json.loads(_unb64u(payload_b64))
        claims = PurposeClaims(
            purpose=data["p"],
            subject=str(data["s"]),
            bind=str(data["b"]),
            expires_at=float(data["exp"]),
        )
    except (ValueError, KeyError, TypeError):
        return None
    if claims.purpose != purpose:
        return None
    if (time.time() if now is None else now) >= claims.expires_at:
        return None
    return claims
