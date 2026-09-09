"""Call ledger -- the backbone of the oversight console (P1).

One :class:`CallRecord` per capture session (opened when the socket is
admitted, closed when the last leg drops) plus a :class:`ScorePoint` per
decision, so the console can list *every* call and draw its escalation
graph -- not only the ALERT+ calls that open a Case.

Metadata only: session id, tenant, the API key the session was admitted
with, start/end, peak state + score, leg/turn counts. Transcript and audio
live in their own stores behind ``RF_RETAIN_TRANSCRIPTS`` /
``RF_RETAIN_AUDIO``; nothing here is gated because none of it is call
content.

``CallLedger`` is a sync Protocol with an in-memory default and a Postgres
implementation (:class:`~packages.calls.pg_ledger.PgCallLedger`), same
shape as the identity / case / billing stores.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Protocol

from packages.contracts.risk import State

_ORDER: dict[State, int] = {
    "CALM": 0,
    "WATCH": 1,
    "ALERT": 2,
    "INTERVENE": 3,
    "RESOLVED": 2,
}


@dataclass(frozen=True, slots=True)
class ScorePoint:
    t: float  # seconds into the call
    score: float
    state: State


@dataclass(frozen=True, slots=True)
class CallRecord:
    session_id: str
    tenant: str
    api_key_id: str | None
    started_at: float
    ended_at: float | None = None
    peak_state: State = "CALM"
    peak_score: float = 0.0
    leg_count: int = 0
    turn_count: int = 0
    scores: tuple[ScorePoint, ...] = ()

    @property
    def duration_s(self) -> float:
        return (self.ended_at - self.started_at) if self.ended_at else 0.0


class CallLedger(Protocol):
    def open(
        self,
        session_id: str,
        *,
        tenant: str,
        api_key_id: str | None,
        started_at: float | None = None,
    ) -> None: ...

    def record_score(self, session_id: str, *, t: float, score: float, state: State) -> None: ...

    def close(
        self,
        session_id: str,
        *,
        ended_at: float | None = None,
        leg_count: int = 0,
        turn_count: int = 0,
    ) -> None: ...

    def get(self, session_id: str) -> CallRecord | None: ...

    def list(
        self,
        tenant: str,
        *,
        api_key_id: str | None = None,
        since: float | None = None,
        until: float | None = None,
        min_state: State | None = None,
        limit: int = 100,
    ) -> list[CallRecord]: ...


def _peak(a: State, b: State) -> State:
    return a if _ORDER[a] >= _ORDER[b] else b


class InMemoryCallLedger:
    def __init__(self) -> None:
        self._calls: dict[str, CallRecord] = {}
        self._scores: dict[str, list[ScorePoint]] = {}

    def open(
        self,
        session_id: str,
        *,
        tenant: str,
        api_key_id: str | None,
        started_at: float | None = None,
    ) -> None:
        if session_id in self._calls:
            return
        self._calls[session_id] = CallRecord(
            session_id=session_id,
            tenant=tenant,
            api_key_id=api_key_id,
            started_at=started_at if started_at is not None else time.time(),
        )
        self._scores[session_id] = []

    def record_score(self, session_id: str, *, t: float, score: float, state: State) -> None:
        cur = self._calls.get(session_id)
        if cur is None:
            return
        self._scores.setdefault(session_id, []).append(ScorePoint(t=t, score=score, state=state))
        self._calls[session_id] = replace(
            cur,
            peak_state=_peak(cur.peak_state, state),
            peak_score=max(cur.peak_score, score),
        )

    def close(
        self,
        session_id: str,
        *,
        ended_at: float | None = None,
        leg_count: int = 0,
        turn_count: int = 0,
    ) -> None:
        cur = self._calls.get(session_id)
        if cur is None:
            return
        self._calls[session_id] = replace(
            cur,
            ended_at=ended_at if ended_at is not None else time.time(),
            leg_count=leg_count or cur.leg_count,
            turn_count=turn_count or cur.turn_count,
        )

    def get(self, session_id: str) -> CallRecord | None:
        cur = self._calls.get(session_id)
        if cur is None:
            return None
        return replace(cur, scores=tuple(self._scores.get(session_id, ())))

    def list(
        self,
        tenant: str,
        *,
        api_key_id: str | None = None,
        since: float | None = None,
        until: float | None = None,
        min_state: State | None = None,
        limit: int = 100,
    ) -> list[CallRecord]:
        floor = _ORDER[min_state] if min_state is not None else -1
        out = [
            replace(c, scores=())
            for c in self._calls.values()
            if c.tenant == tenant
            and (api_key_id is None or c.api_key_id == api_key_id)
            and (since is None or c.started_at >= since)
            and (until is None or c.started_at < until)
            and _ORDER[c.peak_state] >= floor
        ]
        out.sort(key=lambda c: c.started_at, reverse=True)
        return out[: max(0, limit)]
