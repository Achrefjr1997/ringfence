"""Call-ledger score recorder (oversight console P1).

The pipeline publishes every decision to ``rf.<tenant>.decision``. This
watches that stream and appends a score/state point to the call ledger, so
the console can draw the escalation graph for every call. It only touches
rows the gateway already opened in :func:`capture` (keyed by session id),
so ``rf.replay.*`` decisions -- which have no capture socket -- are ignored
naturally.

Score and state only; never any call content.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from packages.calls.ledger import CallLedger
from packages.calls.transcripts import TranscriptStore, Turn
from packages.contracts.risk import State
from packages.contracts.settings import get_settings

log = logging.getLogger("ringfence.call_recorder")

_STATES = frozenset({"CALM", "WATCH", "ALERT", "INTERVENE", "RESOLVED"})


class CallLedgerRecorder:
    def __init__(self, ledger: CallLedger) -> None:
        self._ledger = ledger

    async def run(self, bus: Any) -> None:
        async with contextlib.aclosing(bus.subscribe("rf.*.decision")) as stream:
            async for _subject, payload in stream:
                with contextlib.suppress(Exception):
                    self._record(payload)

    def _record(self, payload: dict[str, Any]) -> None:
        session_id = str(payload.get("session_id", ""))
        state = payload.get("state", "CALM")
        if not session_id or state not in _STATES:
            return
        self._ledger.record_score(
            session_id,
            t=float(payload.get("t", 0.0)),
            score=float(payload.get("score", 0.0)),
            state=_as_state(state),
        )


def _as_state(s: object) -> State:
    return s if s in _STATES else "CALM"  # type: ignore[return-value]


_REPLAY_TENANT = "replay"


class TranscriptRecorder:
    """Buffers ``rf.*.turn`` per session and, on ``rf.*.session.closed``,
    flushes the whole transcript to the store -- **only** when
    ``RF_RETAIN_TRANSCRIPTS=true`` (invariant #5). The buffer is always
    cleared on close, retained or not.
    """

    def __init__(self, store: TranscriptStore) -> None:
        self._store = store
        self._buf: dict[str, list[Turn]] = {}
        self._tenant: dict[str, str] = {}

    async def run(self, bus: Any) -> None:
        async with contextlib.aclosing(bus.subscribe("rf.*")) as stream:
            async for subject, payload in stream:
                with contextlib.suppress(Exception):
                    if subject.endswith(".turn"):
                        self._on_turn(subject, payload)
                    elif subject.endswith(".session.closed"):
                        self._on_closed(str(payload.get("session_id", "")))

    def _tenant_of(self, subject: str) -> str:
        parts = subject.split(".")
        return parts[1] if len(parts) > 2 else ""

    def _on_turn(self, subject: str, payload: dict[str, Any]) -> None:
        sid = str(payload.get("session_id", ""))
        if not sid:
            return
        tenant = self._tenant_of(subject)
        if tenant == _REPLAY_TENANT:
            return
        self._tenant[sid] = tenant
        self._buf.setdefault(sid, []).append(
            (
                str(payload.get("role", "UNKNOWN")),
                str(payload.get("text", "")),
                float(payload.get("t_end", payload.get("t_start", 0.0))),
            )
        )

    def _on_closed(self, session_id: str) -> None:
        turns = self._buf.pop(session_id, None)
        tenant = self._tenant.pop(session_id, "")
        if not turns:
            return
        if get_settings().retain_transcripts:
            self._store.save(session_id, tenant, turns)
