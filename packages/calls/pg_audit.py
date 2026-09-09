"""Postgres-backed :class:`AuditLog` (oversight console P5).

Append-only. ``record`` is one INSERT; the reads are one SELECT each. On
the shared :class:`~packages.db.loop.LoopThread`.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar

from packages.calls.audit import _ACTIONS, Action, AuditEntry

_R = TypeVar("_R")
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def schema_sql() -> str:
    return _SCHEMA_PATH.read_text(encoding="utf-8")


class PgAuditLog:
    def __init__(
        self, dsn: str, *, min_size: int = 1, max_size: int = 4, op_timeout_s: float = 10.0
    ) -> None:
        try:
            import asyncpg  # noqa: PLC0415 - optional 'db' extra
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise RuntimeError("PgAuditLog needs the 'db' extra: pip install -e '.[db]'") from exc

        from packages.db.loop import LoopThread  # noqa: PLC0415

        self._timeout_s = op_timeout_s
        self._closed = False
        self._loop = LoopThread()
        try:

            async def _open() -> Any:
                return await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size)

            self._pool: Any = self._loop.run(_open(), timeout_s=op_timeout_s)
            self._loop.run(self._apply_schema(), timeout_s=op_timeout_s)
        except BaseException:
            self._loop.close()
            raise

    def close_pool(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:

            async def _shut() -> None:
                await self._pool.close()

            self._loop.run(_shut(), timeout_s=self._timeout_s)
        finally:
            self._loop.close()

    async def _apply_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(schema_sql())

    def _run(self, coro: Coroutine[object, object, _R]) -> _R:
        return self._loop.run(coro, timeout_s=self._timeout_s)

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
        self._run(self._record(session_id, tenant, actor_id, actor_email, action, ip))

    async def _record(
        self,
        session_id: str,
        tenant: str,
        actor_id: str,
        actor_email: str,
        action: str,
        ip: str | None,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO call_access_log "
                "(id, session_id, tenant, actor_id, actor_email, action, at, ip) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                uuid.uuid4().hex,
                session_id,
                tenant,
                actor_id,
                actor_email,
                action,
                time.time(),
                ip,
            )

    def for_call(self, session_id: str) -> list[AuditEntry]:
        return self._run(self._for_call(session_id))

    async def _for_call(self, session_id: str) -> list[AuditEntry]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM call_access_log WHERE session_id = $1 ORDER BY at DESC",
                session_id,
            )
        return [_row(r) for r in rows]

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
        return self._run(self._query(tenant, actor_id, action, since, until, max(0, limit)))

    async def _query(
        self,
        tenant: str,
        actor_id: str | None,
        action: str | None,
        since: float | None,
        until: float | None,
        limit: int,
    ) -> list[AuditEntry]:
        clauses = ["tenant = $1"]
        params: list[Any] = [tenant]
        if actor_id is not None:
            params.append(actor_id)
            clauses.append(f"actor_id = ${len(params)}")
        if action is not None:
            params.append(action)
            clauses.append(f"action = ${len(params)}")
        if since is not None:
            params.append(since)
            clauses.append(f"at >= ${len(params)}")
        if until is not None:
            params.append(until)
            clauses.append(f"at < ${len(params)}")
        params.append(limit)
        sql = (
            "SELECT * FROM call_access_log WHERE "
            + " AND ".join(clauses)
            + f" ORDER BY at DESC LIMIT ${len(params)}"
        )
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_row(r) for r in rows]


def _row(r: Any) -> AuditEntry:
    return AuditEntry(
        id=r["id"],
        session_id=r["session_id"],
        tenant=r["tenant"],
        actor_id=r["actor_id"],
        actor_email=r["actor_email"],
        action=r["action"],
        at=r["at"],
        ip=r["ip"],
    )
