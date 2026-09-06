"""Session and leg lifecycle (production §4.3).

    Admitted --> Streaming : first frame
    Streaming <-> Degraded : ASR down / recovered
    Streaming|Degraded --> Draining : hangup / EOS
    Draining --> Closed : final turn flushed, case written
    Admitted --> Closed : admission timeout (10 s, no media)
    Streaming --> Closed : media timeout (8 s silence)

The store is an interface with an in-memory implementation; Redis lands
later.  Detection state deliberately does **not** live here — only what the
media plane needs (leg map, counters, watermarks), so a store outage
degrades new admissions rather than corrupting in-flight scoring.

Timeouts are driven by :meth:`SessionManager.poll`, called on a timer by
the owner, with an injectable clock — no hidden asyncio timers.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from packages.contracts.audio import Frame, Mode, RoleHint, SessionDescriptor
from packages.contracts.events import EventBus


class SessionState(str, Enum):
    ADMITTED = "admitted"
    STREAMING = "streaming"
    DEGRADED = "degraded"
    DRAINING = "draining"
    CLOSED = "closed"


class CloseReason(str, Enum):
    HANGUP = "hangup"
    ADMISSION_TIMEOUT = "admission_timeout"
    MEDIA_TIMEOUT = "media_timeout"
    ERROR = "error"


class SessionError(RuntimeError):
    pass


@dataclass
class LegState:
    leg_id: str
    role_hint: RoleHint
    sample_rate: int
    frames: int = 0
    bytes_in: int = 0
    last_frame_at: float | None = None


@dataclass
class Session:
    session_id: str
    tenant_id: str
    mode: Mode
    state: SessionState
    admitted_at: float
    legs: dict[str, LegState] = field(default_factory=dict)
    first_frame_at: float | None = None
    last_media_at: float | None = None
    degraded_since: float | None = None
    closed_at: float | None = None
    close_reason: CloseReason | None = None


class SessionStore(Protocol):
    def get(self, session_id: str) -> Session | None: ...

    def put(self, session: Session) -> None: ...

    def delete(self, session_id: str) -> None: ...

    def active(self) -> list[Session]: ...

    def all(self) -> list[Session]: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._d: dict[str, Session] = {}

    def get(self, session_id: str) -> Session | None:
        return self._d.get(session_id)

    def put(self, session: Session) -> None:
        self._d[session.session_id] = session

    def delete(self, session_id: str) -> None:
        self._d.pop(session_id, None)

    def active(self) -> list[Session]:
        return [s for s in self._d.values() if s.state is not SessionState.CLOSED]

    def all(self) -> list[Session]:
        return list(self._d.values())


# op -> the states it may be invoked from
_ALLOWED: dict[str, set[SessionState]] = {
    "on_asr_down": {SessionState.STREAMING},
    "on_asr_up": {SessionState.DEGRADED},
    "on_hangup": {SessionState.STREAMING, SessionState.DEGRADED},
    "on_drained": {SessionState.DRAINING},
}


class SessionManager:
    def __init__(
        self,
        store: SessionStore | None = None,
        *,
        admission_timeout_s: float = 10.0,
        media_timeout_s: float = 8.0,
        time_fn: Callable[[], float] = time.monotonic,
        bus: EventBus | None = None,
    ) -> None:
        self._store: SessionStore = store or InMemorySessionStore()
        self._admit_to = admission_timeout_s
        self._media_to = media_timeout_s
        self._now = time_fn
        self._bus = bus

    @property
    def store(self) -> SessionStore:
        return self._store

    def get(self, session_id: str) -> Session:
        s = self._store.get(session_id)
        if s is None:
            raise SessionError(f"unknown session {session_id}")
        return s

    # -- lifecycle -----------------------------------------------------

    async def admit(self, desc: SessionDescriptor) -> Session:
        if self._store.get(desc.session_id) is not None:
            raise SessionError(f"session {desc.session_id} already admitted")
        s = Session(
            session_id=desc.session_id,
            tenant_id=desc.tenant_id,
            mode=desc.mode,
            state=SessionState.ADMITTED,
            admitted_at=self._now(),
            legs={
                leg.leg_id: LegState(leg.leg_id, leg.role_hint, leg.sample_rate)
                for leg in desc.legs
            },
        )
        self._store.put(s)
        await self._emit(s, "admitted")
        return s

    async def on_frame(self, frame: Frame) -> Session:
        s = self.get(frame.session_id)
        if s.state in (SessionState.DRAINING, SessionState.CLOSED):
            return s  # late frame after hangup — ignore, do not resurrect
        leg = s.legs.get(frame.leg_id)
        if leg is None:
            raise SessionError(f"frame for unknown leg {frame.leg_id} on {s.session_id}")

        now = self._now()
        leg.frames += 1
        leg.bytes_in += len(frame.pcm)
        leg.last_frame_at = now
        s.last_media_at = now

        if s.state is SessionState.ADMITTED:
            s.first_frame_at = now
            await self._transition(s, SessionState.STREAMING, "streaming")
        else:  # STREAMING or DEGRADED — counters only
            self._store.put(s)
        return s

    async def on_asr_down(self, session_id: str) -> Session:
        s = self._checked("on_asr_down", session_id)
        s.degraded_since = self._now()
        await self._transition(s, SessionState.DEGRADED, "degraded")
        return s

    async def on_asr_up(self, session_id: str) -> Session:
        s = self._checked("on_asr_up", session_id)
        s.degraded_since = None
        await self._transition(s, SessionState.STREAMING, "recovered")
        return s

    async def on_hangup(self, session_id: str) -> Session:
        s = self._checked("on_hangup", session_id)
        await self._transition(s, SessionState.DRAINING, "draining")
        return s

    async def on_drained(self, session_id: str) -> Session:
        s = self._checked("on_drained", session_id)
        await self._close(s, CloseReason.HANGUP)
        return s

    async def close(self, session_id: str, reason: CloseReason = CloseReason.ERROR) -> Session:
        s = self.get(session_id)
        if s.state is not SessionState.CLOSED:
            await self._close(s, reason)
        return s

    async def poll(self, now: float | None = None) -> list[Session]:
        """Fire the two timeouts.  Returns the sessions closed this call."""
        at = self._now() if now is None else now
        closed: list[Session] = []
        for s in list(self._store.active()):
            if s.state is SessionState.ADMITTED and at - s.admitted_at >= self._admit_to:
                await self._close(s, CloseReason.ADMISSION_TIMEOUT)
                closed.append(s)
            elif (
                s.state is SessionState.STREAMING
                and s.last_media_at is not None
                and at - s.last_media_at >= self._media_to
            ):
                await self._close(s, CloseReason.MEDIA_TIMEOUT)
                closed.append(s)
        return closed

    # -- internal ----------------------------------------------------

    def _checked(self, op: str, session_id: str) -> Session:
        s = self.get(session_id)
        if s.state not in _ALLOWED[op]:
            raise SessionError(f"{op} not allowed from {s.state.value} ({session_id})")
        return s

    async def _transition(self, s: Session, new: SessionState, topic: str) -> None:
        s.state = new
        self._store.put(s)
        await self._emit(s, topic)

    async def _close(self, s: Session, reason: CloseReason) -> None:
        s.state = SessionState.CLOSED
        s.closed_at = self._now()
        s.close_reason = reason
        self._store.put(s)
        await self._emit(s, "closed")

    async def _emit(self, s: Session, topic: str) -> None:
        if self._bus is None:
            return
        await self._bus.publish(
            f"rf.{s.tenant_id}.session.{topic}",
            {
                "session_id": s.session_id,
                "state": s.state.value,
                "reason": s.close_reason.value if s.close_reason else None,
            },
        )
