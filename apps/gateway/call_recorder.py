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
from packages.contracts.risk import State

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
