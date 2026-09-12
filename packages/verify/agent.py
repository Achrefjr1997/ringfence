"""AssemblyAI Voice Agent session -- the verification conversation.

Opening a session here is not like calling the Tier-2 judge. It costs money
per minute *and* it starts a real conversation with a third party who did not
ask to be contacted. That is an external side effect in precisely the sense
invariant #4 means, so :func:`may_open_session` is consulted before the socket
is dialled, and nothing else in this file runs if it says no.

Why the guard cannot reuse invariant #4's existing mechanism: that test spies
on ``httpx.AsyncClient.post``, and nothing on this path posts. The side effect
is the WebSocket connect, so the guard sits immediately before it and the
invariant spies the injected ``connect`` instead.

Past the guard, one conversation:

* ``session.update`` with the prompt from ``prompt.py`` and the
  ``report_verification`` tool;
* desk audio up as ``input.audio`` (only once ``session.ready`` arrives), agent
  audio down to the desk;
* ``tool.call report_verification`` -> the outcome; the ``tool.result`` is held
  until ``reply.done``, as AssemblyAI recommends, then the agent gets a short
  grace period to say goodbye;
* ``session.end`` **always**, in ``finally`` -- closing the socket alone does
  not stop billing;
* a hard duration cap enforced here, not trusted to the model;
* no retries and no reconnects: a dropped verification is an unanswered one.

It never raises for a conversation that went wrong. Every such path returns a
``VerificationOutcome`` with ``verified=None`` and an ``error`` naming why --
"we could not find out" must never surface as an exception that someone later
reads as "they denied it".
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from packages.contracts.audio import Mode
from packages.contracts.verify import VerificationOutcome
from packages.verify import wire
from packages.verify.directory import Institution
from packages.verify.prompt import build_prompt

log = logging.getLogger("ringfence.verify")

AGENT_WS = "wss://agents.assemblyai.com/v1/ws"
MAX_DURATION_S = 120.0  # ~$0.15 at $4.50/h, worst case
FAREWELL_GRACE_S = 8.0
_END_TIMEOUT_S = 2.0


class AgentSocket(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


class Connect(Protocol):
    """The WebSocket dialer, injected so tests (and the invariant) can watch
    it without a network."""

    async def __call__(self, url: str, *, additional_headers: dict[str, str]) -> AgentSocket: ...


class DeskAudio(Protocol):
    """The far end of the conversation -- see ``desk.py::DeskLine``."""

    async def receive_audio(self) -> bytes | None: ...

    async def send_audio(self, pcm16: bytes) -> None: ...

    async def send_event(self, event: Mapping[str, str]) -> None: ...


# (role, text) with role "agent" or "desk".
OnTranscript = Callable[[str, str], Awaitable[None]]


def may_open_session(*, dry_run: bool, mode: Mode) -> bool:
    """Invariant #4, as a predicate.

    Kept as a free function rather than folded into the session so the
    invariant can assert on the rule itself, not merely on one caller's
    observance of it.
    """
    return not (dry_run or mode is Mode.REPLAY)


@dataclass(slots=True)
class _Conversation:
    started: float
    outcome: VerificationOutcome | None = None
    # tool results owed to the agent, sent at the next reply.done
    owed: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    farewell_deadline: float | None = None
    error: str | None = None


class VoiceAgentSession:
    def __init__(
        self,
        *,
        api_key: str,
        connect: Connect,
        dry_run: bool = True,
        max_duration_s: float = MAX_DURATION_S,
        farewell_grace_s: float = FAREWELL_GRACE_S,
        voice: str = "ivy",
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key:
            # A configuration error, not a guard decision. Raising keeps a
            # missing key from looking like the guard doing its job.
            raise ValueError("VoiceAgentSession needs an api_key")
        self._api_key = api_key
        self._connect = connect
        self._dry_run = dry_run
        self._max_duration_s = max_duration_s
        self._farewell_grace_s = farewell_grace_s
        self._voice = voice
        self._now = now

    def may_open(self, mode: Mode) -> bool:
        return may_open_session(dry_run=self._dry_run, mode=mode)

    async def start(
        self,
        *,
        mode: Mode,
        institution: Institution,
        amount: str | None,
        desk: DeskAudio,
        on_transcript: OnTranscript | None = None,
    ) -> VerificationOutcome | None:
        """``None`` means the guard refused -- the session never opened.

        Distinct from a ``VerificationOutcome`` carrying ``verified=None``,
        which means it *did* try and came back without an answer.
        """
        if not may_open_session(dry_run=self._dry_run, mode=mode):
            return None
        convo = _Conversation(started=self._now())
        try:
            ws = await self._connect(
                AGENT_WS, additional_headers={"Authorization": f"Bearer {self._api_key}"}
            )
        except Exception as exc:  # noqa: BLE001 - an unreachable service is an unanswered check
            log.warning("voice agent connect failed: %s", type(exc).__name__)
            return self._unanswered(
                convo, "could not reach the verification service", f"connect:{type(exc).__name__}"
            )
        try:
            system_prompt, greeting = build_prompt(institution, amount)
            await ws.send(
                wire.session_update(
                    system_prompt=system_prompt, greeting=greeting, voice=self._voice
                )
            )
            return await asyncio.wait_for(
                self._converse(ws, desk, on_transcript, convo), self._max_duration_s
            )
        except TimeoutError:
            return convo.outcome or self._unanswered(
                convo, "no answer before the time limit", "duration_cap"
            )
        except Exception as exc:  # noqa: BLE001 - see the module docstring
            log.warning("voice agent conversation failed: %s", type(exc).__name__)
            return convo.outcome or self._unanswered(
                convo, "the conversation dropped", type(exc).__name__
            )
        finally:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(ws.send(wire.session_end()), _END_TIMEOUT_S)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(ws.close(), _END_TIMEOUT_S)

    # -- the conversation ---------------------------------------------------

    async def _converse(
        self,
        ws: AgentSocket,
        desk: DeskAudio,
        on_transcript: OnTranscript | None,
        convo: _Conversation,
    ) -> VerificationOutcome:
        ready = asyncio.Event()
        reader = asyncio.create_task(self._read(ws, desk, on_transcript, convo, ready))
        uplink = asyncio.create_task(self._uplink(ws, desk, ready))
        try:
            done, _ = await asyncio.wait({reader, uplink}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (reader, uplink):
                task.cancel()
            for task in (reader, uplink):
                with contextlib.suppress(BaseException):
                    await task
        if reader in done:
            exc = reader.exception()
            if exc is not None:
                raise exc
            return reader.result()
        uplink_exc = uplink.exception()
        if uplink_exc is not None:
            raise uplink_exc
        return convo.outcome or self._unanswered(
            convo, "the desk hung up before answering", "desk_hangup"
        )

    async def _uplink(self, ws: AgentSocket, desk: DeskAudio, ready: asyncio.Event) -> None:
        """Desk microphone -> agent, until the desk hangs up.

        Audio before ``session.ready`` is read and dropped rather than waited
        for, so a desk that hangs up during setup is still noticed at once.
        """
        while True:
            pcm = await desk.receive_audio()
            if pcm is None:
                return
            if ready.is_set():
                await ws.send(wire.input_audio(pcm))

    async def _read(
        self,
        ws: AgentSocket,
        desk: DeskAudio,
        on_transcript: OnTranscript | None,
        convo: _Conversation,
        ready: asyncio.Event,
    ) -> VerificationOutcome:
        while True:
            raw = await self._next_frame(ws, convo)
            if raw is None:  # the goodbye window elapsed
                return self._reported(convo)
            ev = wire.read_agent_event(raw)
            if ev is None:
                continue
            if ev.kind == "session.ready":
                ready.set()
            elif ev.kind == "reply.audio" and ev.audio:
                await desk.send_audio(ev.audio)
            elif ev.kind in ("transcript.user", "transcript.agent") and ev.text:
                role = "desk" if ev.kind == "transcript.user" else "agent"
                await desk.send_event({"type": "transcript", "role": role, "text": ev.text})
                if on_transcript is not None:
                    with contextlib.suppress(Exception):
                        await on_transcript(role, ev.text)
            elif ev.kind == "tool.call":
                self._on_tool_call(ev, convo)
            elif ev.kind == "reply.done":
                if convo.owed:
                    for call_id, result in convo.owed:
                        await ws.send(wire.tool_result(call_id, result))
                    convo.owed.clear()
                    if convo.outcome is not None and convo.farewell_deadline is None:
                        convo.farewell_deadline = self._now() + self._farewell_grace_s
                elif convo.farewell_deadline is not None:
                    return self._reported(convo)  # the goodbye has been said
            elif ev.kind == "session.error":
                convo.error = ev.code or "session_error"
                log.warning("voice agent error %s: %s", ev.code, ev.message)
                return convo.outcome or self._unanswered(
                    convo, "the verification service reported an error"
                )
            elif ev.kind == "session.ended":
                return convo.outcome or self._unanswered(
                    convo, "the session ended without an answer"
                )

    async def _next_frame(self, ws: AgentSocket, convo: _Conversation) -> str | bytes | None:
        if convo.farewell_deadline is None:
            return await ws.recv()
        remaining = convo.farewell_deadline - self._now()
        if remaining <= 0:
            return None
        try:
            return await asyncio.wait_for(ws.recv(), remaining)
        except TimeoutError:
            return None

    def _on_tool_call(self, ev: wire.AgentEvent, convo: _Conversation) -> None:
        if not ev.call_id:
            return  # a call with no id cannot be answered
        if ev.name != wire.REPORT_TOOL:
            convo.owed.append((ev.call_id, {"error": "unknown tool"}))
            return
        if convo.outcome is not None:
            # The first report stands; a model changing its mind mid-goodbye
            # does not get to rewrite what the desk said.
            convo.owed.append((ev.call_id, {"recorded": True, "note": "already recorded"}))
            return
        outcome = wire.report_from_arguments(ev.arguments, duration_s=self._elapsed(convo))
        if outcome is None:
            convo.owed.append(
                (ev.call_id, {"error": "verified must be true or false, and reason is required"})
            )
            return
        convo.outcome = outcome
        convo.owed.append((ev.call_id, {"recorded": True}))

    # -- outcomes -----------------------------------------------------------

    def _elapsed(self, convo: _Conversation) -> float:
        return round(self._now() - convo.started, 2)

    def _reported(self, convo: _Conversation) -> VerificationOutcome:
        # A farewell window only ever opens after a report was recorded.
        assert convo.outcome is not None
        return convo.outcome

    def _unanswered(
        self, convo: _Conversation, reason: str, error: str | None = None
    ) -> VerificationOutcome:
        return VerificationOutcome(
            verified=None,
            reason=reason,
            duration_s=self._elapsed(convo),
            error=error or convo.error,
        )
