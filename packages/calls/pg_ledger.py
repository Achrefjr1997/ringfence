"""Postgres-backed :class:`CallLedger` (oversight console P1).

Same synchronous interface as :class:`InMemoryCallLedger`, driven from the
shared :class:`~packages.db.loop.LoopThread`. ``open`` fires once per
session admit, ``close`` once per session close; ``record_score`` once per
decision (already throttled by the pipeline, never per frame).
"""

from __future__ import annotations

import time
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar

from packages.calls.ledger import CallRecord, ScorePoint
from packages.contracts.risk import State
from packages.db.loop import LoopThread

_R = TypeVar("_R")
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_ORDER: dict[str, int] = {"CALM": 0, "WATCH": 1, "ALERT": 2, "INTERVENE": 3, "RESOLVED": 2}


def schema_sql() -> str:
    return _SCHEMA_PATH.read_text(encoding="utf-8")


class PgCallLedger:
    def __init__(
        self, dsn: str, *, min_size: int = 1, max_size: int = 4, op_timeout_s: float = 10.0
    ) -> None:
        try:
            import asyncpg  # noqa: PLC0415 - optional 'db' extra
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise RuntimeError("PgCallLedger needs the 'db' extra: pip install -e '.[db]'") from exc

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

    # -- writes ---------------------------------------------------

    def open(
        self,
        session_id: str,
        *,
        tenant: str,
        api_key_id: str | None,
        started_at: float | None = None,
    ) -> None:
        self._run(self._open(session_id, tenant, api_key_id, started_at or time.time()))

    async def _open(
        self, session_id: str, tenant: str, api_key_id: str | None, started_at: float
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO call_ledger (session_id, tenant, api_key_id, started_at) "
                "VALUES ($1, $2, $3, $4) ON CONFLICT (session_id) DO NOTHING",
                session_id,
                tenant,
                api_key_id,
                started_at,
            )

    def record_score(self, session_id: str, *, t: float, score: float, state: State) -> None:
        self._run(self._record_score(session_id, t, score, state))

    async def _record_score(self, session_id: str, t: float, score: float, state: State) -> None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT peak_state, peak_score FROM call_ledger WHERE session_id = $1",
                session_id,
            )
            if row is None:
                return
            await conn.execute(
                "INSERT INTO call_scores (session_id, t, score, state) VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (session_id, t) DO UPDATE SET score = EXCLUDED.score, state = EXCLUDED.state",
                session_id,
                t,
                score,
                state,
            )
            higher = _ORDER[state] > _ORDER[str(row["peak_state"])]
            await conn.execute(
                "UPDATE call_ledger SET "
                "peak_score = GREATEST(peak_score, $2), "
                "peak_state = CASE WHEN $3 THEN $4 ELSE peak_state END "
                "WHERE session_id = $1",
                session_id,
                score,
                higher,
                state,
            )

    def close(
        self,
        session_id: str,
        *,
        ended_at: float | None = None,
        leg_count: int = 0,
        turn_count: int = 0,
    ) -> None:
        self._run(self._close(session_id, ended_at or time.time(), leg_count, turn_count))

    async def _close(
        self, session_id: str, ended_at: float, leg_count: int, turn_count: int
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE call_ledger SET ended_at = $2, "
                "leg_count = GREATEST(leg_count, $3), turn_count = GREATEST(turn_count, $4) "
                "WHERE session_id = $1",
                session_id,
                ended_at,
                leg_count,
                turn_count,
            )

    # -- reads ----------------------------------------------------

    def get(self, session_id: str) -> CallRecord | None:
        return self._run(self._get(session_id))

    async def _get(self, session_id: str) -> CallRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM call_ledger WHERE session_id = $1", session_id)
            if row is None:
                return None
            pts = await conn.fetch(
                "SELECT t, score, state FROM call_scores WHERE session_id = $1 ORDER BY t",
                session_id,
            )
        return _record(
            row,
            tuple(ScorePoint(t=p["t"], score=p["score"], state=p["state"]) for p in pts),
        )

    async def _list(
        self,
        tenant: str,
        api_key_id: str | None,
        since: float | None,
        until: float | None,
        min_state: State | None,
        limit: int,
    ) -> list[CallRecord]:
        clauses = ["tenant = $1"]
        params: list[Any] = [tenant]
        if api_key_id is not None:
            params.append(api_key_id)
            clauses.append(f"api_key_id = ${len(params)}")
        if since is not None:
            params.append(since)
            clauses.append(f"started_at >= ${len(params)}")
        if until is not None:
            params.append(until)
            clauses.append(f"started_at < ${len(params)}")
        if min_state is not None:
            allowed = [s for s, n in _ORDER.items() if n >= _ORDER[min_state]]
            params.append(allowed)
            clauses.append(f"peak_state = ANY(${len(params)})")
        params.append(limit)
        sql = (
            "SELECT * FROM call_ledger WHERE "
            + " AND ".join(clauses)
            + f" ORDER BY started_at DESC LIMIT ${len(params)}"
        )
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_record(r, ()) for r in rows]

    # defined last: the name `list` shadows the builtin in class scope, so
    # nothing after this may use `list[...]` in an annotation (see PgCaseStore)
    def list(  # noqa: A003 - matches the CallLedger protocol
        self,
        tenant: str,
        *,
        api_key_id: str | None = None,
        since: float | None = None,
        until: float | None = None,
        min_state: State | None = None,
        limit: int = 100,
    ) -> list[CallRecord]:
        return self._run(self._list(tenant, api_key_id, since, until, min_state, max(0, limit)))


def _record(row: Any, scores: tuple[ScorePoint, ...]) -> CallRecord:
    return CallRecord(
        session_id=row["session_id"],
        tenant=row["tenant"],
        api_key_id=row["api_key_id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        peak_state=row["peak_state"],
        peak_score=row["peak_score"],
        leg_count=row["leg_count"],
        turn_count=row["turn_count"],
        scores=scores,
    )
