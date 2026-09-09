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

from packages.calls.ledger import CallRecord, ScorePoint, UserCallSummary
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
        user_ref: str | None = None,
        user_label: str | None = None,
        started_at: float | None = None,
    ) -> None:
        self._run(
            self._open(
                session_id,
                tenant,
                api_key_id,
                user_ref or None,
                user_label or None,
                started_at or time.time(),
            )
        )

    async def _open(
        self,
        session_id: str,
        tenant: str,
        api_key_id: str | None,
        user_ref: str | None,
        user_label: str | None,
        started_at: float,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO call_ledger "
                "(session_id, tenant, api_key_id, user_ref, user_label, started_at) "
                "VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (session_id) DO NOTHING",
                session_id,
                tenant,
                api_key_id,
                user_ref,
                user_label,
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

    def set_private(self, session_id: str, private: bool) -> None:
        self._run(
            self._exec(
                "UPDATE call_ledger SET private = $2 WHERE session_id = $1", session_id, private
            )
        )

    def share(self, session_id: str, *, user_id: str, by: str) -> None:
        self._run(
            self._exec(
                "INSERT INTO call_shares (session_id, shared_with_user, shared_by, shared_at) "
                "VALUES ($1, $2, $3, $4) ON CONFLICT (session_id, shared_with_user) DO NOTHING",
                session_id,
                user_id,
                by,
                time.time(),
            )
        )

    def unshare(self, session_id: str, user_id: str) -> None:
        self._run(
            self._exec(
                "DELETE FROM call_shares WHERE session_id = $1 AND shared_with_user = $2",
                session_id,
                user_id,
            )
        )

    def shares(self, session_id: str) -> list[str]:
        return self._run(self._shares(session_id))

    async def _exec(self, sql: str, *args: object) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(sql, *args)

    async def _shares(self, session_id: str) -> list[str]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT shared_with_user FROM call_shares WHERE session_id = $1 "
                "ORDER BY shared_with_user",
                session_id,
            )
        return [r["shared_with_user"] for r in rows]

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
        user_ref: str | None,
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
        if user_ref is not None:
            params.append(user_ref)
            clauses.append(f"user_ref = ${len(params)}")
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

    async def _user_summaries(self, tenant: str, since: float | None) -> list[UserCallSummary]:
        params: list[Any] = [tenant]
        where = "tenant = $1 AND user_ref IS NOT NULL"
        if since is not None:
            params.append(since)
            where += " AND started_at >= $2"
        sql = (
            "SELECT user_ref, "
            "max(user_label) AS user_label, "
            "count(*) AS calls, "
            "count(*) FILTER (WHERE peak_state IN ('ALERT','INTERVENE','RESOLVED')) AS alerts, "
            "count(*) FILTER (WHERE peak_state = 'INTERVENE') AS interventions, "
            "count(*) FILTER (WHERE peak_state = 'WATCH') AS watches, "
            "max(started_at) AS last_at "
            f"FROM call_ledger WHERE {where} GROUP BY user_ref ORDER BY last_at DESC"
        )
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        out: list[UserCallSummary] = []
        for r in rows:
            alerts, interventions, watches = r["alerts"], r["interventions"], r["watches"]
            peak: State = (
                "INTERVENE"
                if interventions
                else "ALERT"
                if alerts
                else "WATCH"
                if watches
                else "CALM"
            )
            out.append(
                UserCallSummary(
                    user_ref=r["user_ref"],
                    user_label=r["user_label"],
                    calls=int(r["calls"]),
                    alerts=int(alerts),
                    interventions=int(interventions),
                    last_at=float(r["last_at"]),
                    peak_state=peak,
                )
            )
        return out

    def user_summaries(self, tenant: str, *, since: float | None = None) -> list[UserCallSummary]:
        return self._run(self._user_summaries(tenant, since))

    # defined last: the name `list` shadows the builtin in class scope, so
    # nothing after this may use `list[...]` in an annotation (see PgCaseStore)
    def list(  # noqa: A003 - matches the CallLedger protocol
        self,
        tenant: str,
        *,
        api_key_id: str | None = None,
        user_ref: str | None = None,
        since: float | None = None,
        until: float | None = None,
        min_state: State | None = None,
        limit: int = 100,
    ) -> list[CallRecord]:
        return self._run(
            self._list(tenant, api_key_id, user_ref, since, until, min_state, max(0, limit))
        )


def _record(row: Any, scores: tuple[ScorePoint, ...]) -> CallRecord:
    return CallRecord(
        session_id=row["session_id"],
        tenant=row["tenant"],
        api_key_id=row["api_key_id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        user_ref=row["user_ref"],
        user_label=row["user_label"],
        peak_state=row["peak_state"],
        peak_score=row["peak_score"],
        leg_count=row["leg_count"],
        turn_count=row["turn_count"],
        private=row["private"],
        scores=scores,
    )
