"""Per-call transcript store (oversight console P6).

Every call's transcript, not just the ALERT+ ones that open a Case. Still
governed by invariant #5: nothing is written unless
``RF_RETAIN_TRANSCRIPTS=true`` -- the gate lives in
:class:`~apps.gateway.call_recorder.TranscriptRecorder`, which decides
whether to call :meth:`TranscriptStore.save` at all.

``TranscriptStore`` is a sync Protocol -- in-memory default,
:class:`~packages.calls.pg_transcripts.PgTranscriptStore` for Postgres.
"""

from __future__ import annotations

from typing import Protocol

# (role, text, t) -- role is "CALLER" | "CALLEE" | "UNKNOWN"
Turn = tuple[str, str, float]


class TranscriptStore(Protocol):
    def save(self, session_id: str, tenant: str, turns: list[Turn]) -> None: ...

    def get(self, session_id: str) -> list[Turn]: ...


class InMemoryTranscriptStore:
    def __init__(self) -> None:
        self._by_session: dict[str, list[Turn]] = {}

    def save(self, session_id: str, tenant: str, turns: list[Turn]) -> None:
        if not turns:
            return
        self._by_session[session_id] = list(turns)

    def get(self, session_id: str) -> list[Turn]:
        return list(self._by_session.get(session_id, ()))
