"""Postgres-backed :class:`CaseStore` (T-7.2b).

Same synchronous interface as the in-memory
:class:`~packages.intervene.cases.CaseStore` -- ``Pipeline`` still calls
``record()`` from async code and the gateway still calls ``get`` / ``list``
/ ``set_feedback`` unchanged.  asyncpg runs on a shared
:class:`~packages.db.loop.LoopThread`; ``record()`` fires once per ALERT+
decision, never per frame.

Invariant #5 -- transcripts never reach disk unless
``RF_RETAIN_TRANSCRIPTS=true``:

* ``cases.transcript`` is written only when the flag is on; otherwise the
  column stays NULL and ``get()`` returns an empty transcript.
* Only verdict-level contribution fields are persisted
  (``source``/``id``/``value``/``role``/``t``).  The ``evidence`` span and
  the judge ``detail`` string -- the two that can carry quoted call
  content -- are dropped before the row is written.

Enable with the ``db`` extra and ``RF_DATABASE_URL``; otherwise the gateway
keeps the in-memory store.
"""

from __future__ import annotations

import json
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar, cast

from packages.contracts.risk import Contribution, Decision
from packages.contracts.settings import get_settings
from packages.contracts.transcript import Role
from packages.db.crypto import column_cipher
from packages.db.loop import LoopThread
from packages.intervene.cases import _FEEDBACK, Case, FeedbackLabel

_R = TypeVar("_R")

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_DECISION_COLS = (
    "decision_id, session_id, seq, t, state, score, policy_pack, counterfactual, contributions"
)


def schema_sql() -> str:
    return _SCHEMA_PATH.read_text(encoding="utf-8")


def _contrib_rows(decision: Decision) -> str:
    """Verdict-level contributions only -- no evidence / detail text."""
    return json.dumps(
        [
            {"source": c.source, "id": c.id, "value": c.value, "role": c.role, "t": c.t}
            for c in decision.contributions
        ]
    )


def _decision(row: Any) -> Decision:
    contribs = tuple(
        Contribution(source=c["source"], id=c["id"], value=c["value"], role=c["role"], t=c["t"])
        for c in json.loads(row["contributions"])
    )
    return Decision(
        decision_id=row["decision_id"],
        session_id=row["session_id"],
        t=row["t"],
        state=row["state"],
        score=row["score"],
        policy_pack=row["policy_pack"],
        contributions=contribs,
        counterfactual=row["counterfactual"],
    )


def _transcript(plaintext: str | None) -> list[tuple[Role, str, float]]:
    if not plaintext:
        return []
    return [(r, t, ts) for (r, t, ts) in json.loads(plaintext)]


class PgCaseStore:
    """Drop-in for ``CaseStore`` backed by Postgres."""

    def __init__(
        self, dsn: str, *, min_size: int = 1, max_size: int = 8, op_timeout_s: float = 10.0
    ) -> None:
        try:
            import asyncpg  # noqa: PLC0415 - optional 'db' extra
        except ModuleNotFoundError as exc:  # pragma: no cover - trivial guard
            raise RuntimeError("PgCaseStore needs the 'db' extra: pip install -e '.[db]'") from exc

        self._timeout_s = op_timeout_s
        self._closed = False
        self._cipher = column_cipher()  # encrypts feedback_note + retained transcript
        self._loop = LoopThread()
        try:

            async def _open() -> Any:
                return await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size)

            self._pool: Any = self._loop.run(_open(), timeout_s=op_timeout_s)
            self._loop.run(self._apply_schema(), timeout_s=op_timeout_s)
        except BaseException:
            self._loop.close()
            raise

    # -- lifecycle ------------------------------------------------------

    def close(self) -> None:
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

    def _build_case(self, crow: Any, drows: list[Any]) -> Case:
        return Case(
            session_id=crow["session_id"],
            opened_at=crow["opened_at"],
            tenant=crow["tenant"],
            decisions=[_decision(d) for d in drows],
            transcript=_transcript(self._cipher.decrypt(crow["transcript"])),
            feedback=cast(FeedbackLabel | None, crow["feedback"]),
            feedback_note=self._cipher.decrypt(crow["feedback_note"]) or "",
        )

    # -- writes -------------------------------------------------------

    def record(
        self,
        session_id: str,
        decision: Decision,
        transcript: list[tuple[Role, str, float]],
        *,
        tenant: str = "",
    ) -> Case:
        return self._run(self._record(session_id, decision, transcript, tenant))

    async def _record(
        self,
        session_id: str,
        decision: Decision,
        transcript: list[tuple[Role, str, float]],
        tenant: str,
    ) -> Case:
        retain = get_settings().retain_transcripts
        transcript_col = (
            self._cipher.encrypt(json.dumps([list(turn) for turn in transcript]))
            if retain
            else None
        )
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO cases (session_id, tenant, opened_at) VALUES ($1, $2, $3) "
                "ON CONFLICT (session_id) DO NOTHING",
                session_id,
                tenant,
                decision.t,
            )
            if retain:
                await conn.execute(
                    "UPDATE cases SET transcript = $2 WHERE session_id = $1",
                    session_id,
                    transcript_col,
                )
            await conn.execute(
                f"INSERT INTO case_decisions ({_DECISION_COLS}) VALUES ("
                "$1, $2, (SELECT COALESCE(MAX(seq), -1) + 1 FROM case_decisions WHERE session_id = $2), "
                "$3, $4, $5, $6, $7, $8::jsonb) ON CONFLICT (decision_id) DO NOTHING",
                decision.decision_id,
                session_id,
                decision.t,
                decision.state,
                decision.score,
                decision.policy_pack,
                decision.counterfactual,
                _contrib_rows(decision),
            )
        case = await self._get(session_id)
        assert case is not None  # just wrote it
        return case

    def set_feedback(self, session_id: str, label: str, note: str = "") -> Case:
        return self._run(self._set_feedback(session_id, label, note))

    async def _set_feedback(self, session_id: str, label: str, note: str) -> Case:
        if label not in _FEEDBACK:
            raise ValueError(f"label must be one of {sorted(_FEEDBACK)}, got {label!r}")
        async with self._pool.acquire() as conn:
            hit = await conn.fetchval(
                "UPDATE cases SET feedback = $2, feedback_note = $3 WHERE session_id = $1 "
                "RETURNING session_id",
                session_id,
                label,
                self._cipher.encrypt(note),
            )
        if hit is None:
            raise KeyError(session_id)
        case = await self._get(session_id)
        assert case is not None
        return case

    # -- reads -------------------------------------------------------

    def get(self, session_id: str) -> Case | None:
        return self._run(self._get(session_id))

    async def _get(self, session_id: str) -> Case | None:
        async with self._pool.acquire() as conn:
            crow = await conn.fetchrow(
                "SELECT session_id, tenant, opened_at, feedback, feedback_note, transcript "
                "FROM cases WHERE session_id = $1",
                session_id,
            )
            if crow is None:
                return None
            drows = await conn.fetch(
                f"SELECT {_DECISION_COLS} FROM case_decisions WHERE session_id = $1 ORDER BY seq",
                session_id,
            )
        return self._build_case(crow, drows)

    async def _list_all(self) -> list[Case]:
        async with self._pool.acquire() as conn:
            crows = await conn.fetch(
                "SELECT session_id, tenant, opened_at, feedback, feedback_note, transcript "
                "FROM cases ORDER BY opened_at"
            )
            drows = await conn.fetch(
                f"SELECT {_DECISION_COLS} FROM case_decisions ORDER BY session_id, seq"
            )
        by_session: dict[str, list[Any]] = {}
        for d in drows:
            by_session.setdefault(d["session_id"], []).append(d)
        return [self._build_case(c, by_session.get(c["session_id"], [])) for c in crows]

    def list(self) -> list[Case]:
        return self._run(self._list_all())
