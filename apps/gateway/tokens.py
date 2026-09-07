"""Stateless session tokens for the SaaS auth layer (T-7.1b).

Format: ``<payload_b64url>.<sig_b64url>`` where payload is compact JSON
``{"uid","org","role","exp"}`` signed HMAC-SHA256 with the gateway's
session secret (same idiom as ``intervene.webhook.sign``).

Stateless by design: ``/auth/logout`` is advisory, and rotating the
secret invalidates every outstanding token at once.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

DEFAULT_TTL_S = 24 * 3600.0


@dataclass(frozen=True, slots=True)
class TokenClaims:
    user_id: str
    org_id: str
    role: str
    expires_at: float


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(secret: str, payload_b64: str) -> str:
    return _b64u(hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest())


def issue_token(
    *,
    user_id: str,
    org_id: str,
    role: str,
    secret: str,
    ttl_s: float = DEFAULT_TTL_S,
    now: float | None = None,
) -> str:
    exp = (time.time() if now is None else now) + ttl_s
    payload = _b64u(
        json.dumps(
            {"uid": user_id, "org": org_id, "role": role, "exp": exp},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    return f"{payload}.{_sign(secret, payload)}"


def read_token(token: str, *, secret: str, now: float | None = None) -> TokenClaims | None:
    """Parsed claims, or ``None`` if the token is malformed, mis-signed, or expired."""
    try:
        payload_b64, sig = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(_sign(secret, payload_b64), sig):
        return None
    try:
        data = json.loads(_unb64u(payload_b64))
        claims = TokenClaims(
            str(data["uid"]), str(data["org"]), str(data["role"]), float(data["exp"])
        )
    except (ValueError, KeyError, TypeError):
        return None
    if (time.time() if now is None else now) >= claims.expires_at:
        return None
    return claims
