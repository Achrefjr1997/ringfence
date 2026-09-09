"""Call access audit log (oversight console P5).

An append-only record of who looked at, discussed, or changed the sharing
of a call. There is no update or delete path -- deliberately, because this
is the trail an auditor or regulator asks for, and it is a hard
prerequisite for audio playback (P7).

``AuditLog`` is a sync Protocol -- in-memory default,
:class:`~packages.calls.pg_audit.PgAuditLog` for Postgres.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Literal, Protocol

Action = Literal["list", "view", "play", "download", "comment", "share", "set_private"]
_ACTIONS: frozenset[str] = frozenset(
    {"list", "view", "play", "download", "comment", "share", "set_private"}
)


@dataclass(frozen=True, slots=True)
class AuditEntry:
    id: str
    session_id: str  # "" for a tenant-wide action such as `list`
    tenant: str
    actor_id: str
    actor_email: str
    action: Action
    at: float
    ip: str | None = None


class AuditLog(Protocol):
    def record(
        self,
        *,
        session_id: str,
        tenant: str,
        actor_id: str,
        actor_email: str,
        action: Action,
        ip: str | None = None,
    ) -> None: ...

    def for_call(self, session_id: str) -> list[AuditEntry]: ...

    def query(
        self,
        tenant: str,
        *,
        actor_id: str | None = None,
        action: Action | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 200,
    ) -> list[AuditEntry]: ...


def _entry(
    *,
    session_id: str,
    tenant: str,
    actor_id: str,
    actor_email: str,
    action: Action,
    ip: str | None,
) -> AuditEntry:
    return AuditEntry(
        id=uuid.uuid4().hex,
        session_id=session_id,
        tenant=tenant,
        actor_id=actor_id,
        actor_email=actor_email,
        action=action,
        at=time.time(),
        ip=ip,
    )


class InMemoryAuditLog:
    def __init__(self) -> None:
        self._rows: list[AuditEntry] = []

    def record(
        self,
        *,
        session_id: str,
        tenant: str,
        actor_id: str,
        actor_email: str,
        action: Action,
        ip: str | None = None,
    ) -> None:
        if action not in _ACTIONS:
            return
        self._rows.append(
            _entry(
                session_id=session_id,
                tenant=tenant,
                actor_id=actor_id,
                actor_email=actor_email,
                action=action,
                ip=ip,
            )
        )

    def for_call(self, session_id: str) -> list[AuditEntry]:
        return sorted(
            (r for r in self._rows if r.session_id == session_id),
            key=lambda r: r.at,
            reverse=True,
        )

    def query(
        self,
        tenant: str,
        *,
        actor_id: str | None = None,
        action: Action | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 200,
    ) -> list[AuditEntry]:
        rows = [
            r
            for r in self._rows
            if r.tenant == tenant
            and (actor_id is None or r.actor_id == actor_id)
            and (action is None or r.action == action)
            and (since is None or r.at >= since)
            and (until is None or r.at < until)
        ]
        rows.sort(key=lambda r: r.at, reverse=True)
        return rows[: max(0, limit)]
