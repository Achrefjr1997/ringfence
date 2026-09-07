"""Identity records (T-7.1a).

Pydantic models for the SaaS shell: an :class:`Org` (the billing / tenancy
unit), its :class:`User` accounts, and the :class:`ApiKey` rows the gateway
admits calls against.  No I/O here — persistence is ``identity.store``.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Role = Literal["admin", "operator", "guardian"]

# admin: billing + user/key management. operator: console + case review.
# guardian: notification-only, verdict view, never transcript text.

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _uid() -> str:
    return uuid.uuid4().hex


def _now() -> float:
    return time.time()


class Org(BaseModel):
    id: str = Field(default_factory=_uid)
    name: str
    tenant: str = ""  # the rf.<tenant>.* scope key; set to id at creation
    created_at: float = Field(default_factory=_now)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("org name must not be blank")
        return v.strip()


class User(BaseModel):
    id: str = Field(default_factory=_uid)
    org_id: str
    email: str
    password_hash: str
    role: Role = "operator"
    verified: bool = False
    created_at: float = Field(default_factory=_now)

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError(f"not an email address: {v!r}")
        return v


class ApiKey(BaseModel):
    id: str = Field(default_factory=_uid)
    org_id: str
    name: str  # human label, e.g. "prod gateway"
    prefix: str  # first chars of the plaintext, for display only
    key_hash: str  # sha256 hex of the full plaintext — the plaintext is never stored
    created_at: float = Field(default_factory=_now)
    last_used_at: float | None = None
    revoked: bool = False
