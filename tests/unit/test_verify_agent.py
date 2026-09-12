"""The Voice Agent session: the guard, then one bounded conversation.

The guard tests come first and stay first -- they shipped before the
conversation existed (Phase 0), and the conversation must not weaken them.
Everything past them drives ``VoiceAgentSession`` with a scripted fake socket
and a fake desk: no network, no key, no cost.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Mapping

import pytest

from packages.contracts.audio import Mode
from packages.verify.agent import AGENT_WS, VoiceAgentSession, may_open_session
from packages.verify.directory import Institution

_INST = Institution.model_validate(
    {
        "id": "amazon",
        "display_name": "Amazon",
        "desk_id": "demo_desk",
        "line_label": "account security",
        "aliases": ["amazon"],
    }
)


class _RecordingConnect:
    """Stands in for ``websockets.connect``. Records every attempt so a test
    can assert on attempts that should never have happened."""

    def __init__(self, socket: _FakeSocket | None = None) -> None:
        self.opened: list[str] = []
        self.headers: list[dict[str, str]] = []
        self._socket = socket

    async def __call__(self, url: str, *, additional_headers: dict[str, str]) -> _FakeSocket:
        self.opened.append(url)
        self.headers.append(additional_headers)
        if self._socket is None:
            raise ConnectionRefusedError("no socket scripted")
        return self._socket


class _FakeDesk:
    def __init__(self) -> None:
        self.mic: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.heard: list[bytes] = []
        self.events: list[dict[str, str]] = []

    async def receive_audio(self) -> bytes | None:
        return await self.mic.get()

    async def send_audio(self, pcm16: bytes) -> None:
        self.heard.append(pcm16)

    async def send_event(self, event: Mapping[str, str]) -> None:
        self.events.append(dict(event))


class _FakeSocket:
    """Delivers scripted server frames in order, then blocks like a quiet
    socket. ``on_ready`` fires as ``session.ready`` is handed over, so a test
    can release desk audio at a deterministic point."""

    def __init__(
        self, frames: list[dict[str, object] | str], desk: _FakeDesk | None = None
    ) -> None:
        self.inbox: asyncio.Queue[str] = asyncio.Queue()
        for f in frames:
            self.inbox.put_nowait(f if isinstance(f, str) else json.dumps(f))
        self.sent: list[dict[str, object]] = []
        self.closed = False
        self._desk = desk
        self.fail_recv: BaseException | None = None

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        if self.fail_recv is not None and self.inbox.empty():
            raise self.fail_recv
        frame = await self.inbox.get()
        if self._desk is not None and json.loads(frame).get("type") == "session.ready":
            self._desk.mic.put_nowait(b"\x10\x00" * 4)
        return frame

    async def close(self) -> None:
        self.closed = True

    def kinds(self) -> list[object]:
        return [m.get("type") for m in self.sent]


def _session(connect: _RecordingConnect, **kw: object) -> VoiceAgentSession:
    return VoiceAgentSession(api_key="k", connect=connect, dry_run=False, **kw)  # type: ignore[arg-type]


async def _start(
    session: VoiceAgentSession,
    desk: _FakeDesk | None = None,
    *,
    mode: Mode = Mode.SDK,
    amount: str | None = None,
    transcripts: list[tuple[str, str]] | None = None,
):  # noqa: ANN202
    async def on_transcript(role: str, text: str) -> None:
        if transcripts is not None:
            transcripts.append((role, text))

    return await session.start(
        mode=mode,
        institution=_INST,
        amount=amount,
        desk=desk or _FakeDesk(),
        on_transcript=on_transcript,
    )


def _report(call_id: str = "c1", **arguments: object) -> dict[str, object]:
    args = {"verified": False, "reason": "we have no record of calling"} | arguments
    return {
        "type": "tool.call",
        "call_id": call_id,
        "name": "report_verification",
        "arguments": args,
    }


# -- the predicate, on its own ----------------------------------------------


@pytest.mark.parametrize("mode", [Mode.CARRIER, Mode.SDK, Mode.ENTERPRISE])
def test_a_live_session_in_a_live_mode_may_open(mode: Mode) -> None:
    assert may_open_session(dry_run=False, mode=mode) is True


@pytest.mark.parametrize("mode", [Mode.CARRIER, Mode.SDK, Mode.ENTERPRISE, Mode.REPLAY])
def test_dry_run_never_opens_whatever_the_mode(mode: Mode) -> None:
    assert may_open_session(dry_run=True, mode=mode) is False


def test_replay_never_opens_even_when_not_dry_run() -> None:
    """A fixture replayed into a real tenant is still a replay. This is the
    case the bus could not even see before `mode` was added to the decision
    event -- see packages/pipeline/pipeline.py."""
    assert may_open_session(dry_run=False, mode=Mode.REPLAY) is False


# -- the session honours it --------------------------------------------------


async def test_a_guarded_session_never_touches_the_socket() -> None:
    connect = _RecordingConnect()
    session = VoiceAgentSession(api_key="k", connect=connect, dry_run=True)
    assert await _start(session, mode=Mode.CARRIER) is None
    assert connect.opened == []


async def test_a_replay_session_never_touches_the_socket() -> None:
    connect = _RecordingConnect()
    assert await _start(_session(connect), mode=Mode.REPLAY) is None
    assert connect.opened == []


async def test_a_live_session_does_reach_the_socket() -> None:
    """Without this the two tests above prove nothing -- a session that never
    connects under any circumstance would pass them both."""
    connect = _RecordingConnect()
    await _start(_session(connect), mode=Mode.CARRIER)
    assert connect.opened == [AGENT_WS]
    assert connect.headers == [{"Authorization": "Bearer k"}]


async def test_an_absent_api_key_is_refused_before_the_guard_is_even_consulted() -> None:
    """A missing key is a configuration error, not a dry-run. Failing loudly
    here stops a silent no-op being mistaken for the guard working."""
    with pytest.raises(ValueError, match="api_key"):
        VoiceAgentSession(api_key="", connect=_RecordingConnect(), dry_run=False)


# -- the conversation --------------------------------------------------------


async def test_an_unreachable_service_is_unanswered_not_raised() -> None:
    out = await _start(_session(_RecordingConnect()))
    assert out is not None and out.verified is None
    assert out.error == "connect:ConnectionRefusedError"


async def test_the_first_frame_is_the_session_update_with_the_prompt() -> None:
    sock = _FakeSocket([{"type": "session.ended"}])
    await _start(_session(_RecordingConnect(sock)), amount="$500")
    first = sock.sent[0]
    assert first["type"] == "session.update"
    session = first["session"]
    assert isinstance(session, dict)
    assert "Amazon" in session["system_prompt"] and "$500" in session["system_prompt"]
    assert "automated" in session["greeting"]


async def test_a_full_conversation_reports_what_the_desk_said() -> None:
    desk = _FakeDesk()
    agent_pcm = b"\x01\x00\x02\x00"
    sock = _FakeSocket(
        [
            {"type": "session.ready", "session_id": "sess_1"},
            {"type": "reply.audio", "data": base64.b64encode(agent_pcm).decode()},
            {"type": "transcript.agent", "text": "Did Amazon place this call?"},
            {"type": "transcript.user", "text": "No, we have no record of that."},
            _report("call_9"),
            {"type": "reply.done", "status": "completed"},
            {"type": "reply.done", "status": "completed"},  # the goodbye
        ],
        desk,
    )
    transcripts: list[tuple[str, str]] = []
    out = await _start(_session(_RecordingConnect(sock)), desk, transcripts=transcripts)

    assert out is not None and out.verified is False and out.simulated is False
    assert out.reason == "we have no record of calling"
    assert desk.heard == [agent_pcm]
    assert transcripts == [
        ("agent", "Did Amazon place this call?"),
        ("desk", "No, we have no record of that."),
    ]
    assert {"type": "transcript", "role": "desk", "text": "No, we have no record of that."} in (
        desk.events
    )
    results = [m for m in sock.sent if m["type"] == "tool.result"]
    assert [r["call_id"] for r in results] == ["call_9"]
    assert json.loads(str(results[0]["result"])) == {"recorded": True}
    assert sock.kinds()[-1] == "session.end" and sock.closed


async def test_the_tool_result_waits_for_reply_done() -> None:
    """AssemblyAI's guidance: answer tool calls after the reply finishes."""
    sock = _FakeSocket([{"type": "session.ready"}, _report(), {"type": "session.ended"}])
    out = await _start(_session(_RecordingConnect(sock)))
    assert out is not None and out.verified is False  # the report still counts
    assert "tool.result" not in sock.kinds()


async def test_desk_audio_goes_up_only_after_session_ready() -> None:
    desk = _FakeDesk()
    desk.mic.put_nowait(b"\x99\x00" * 4)  # spoken before the agent was ready: dropped
    sock = _FakeSocket([], desk)  # silent until the test says otherwise
    session = _session(_RecordingConnect(sock), max_duration_s=0.3)
    running = asyncio.create_task(_start(session, desk))
    await asyncio.sleep(0.05)  # the uplink reads, and drops, the early audio
    sock.inbox.put_nowait(json.dumps({"type": "session.ready"}))  # releases \x10 audio
    await running
    uploads = [base64.b64decode(str(m["audio"])) for m in sock.sent if m["type"] == "input.audio"]
    assert uploads == [b"\x10\x00" * 4]


async def test_an_error_frame_ends_it_unanswered_and_still_ends_the_session() -> None:
    sock = _FakeSocket([{"type": "session.error", "code": "UNAUTHORIZED", "message": "bad key"}])
    out = await _start(_session(_RecordingConnect(sock)))
    assert out is not None and out.verified is None and out.error == "UNAUTHORIZED"
    assert sock.kinds()[-1] == "session.end"


async def test_session_end_is_sent_even_when_the_conversation_blows_up() -> None:
    """The billing test. Socket close alone does not stop the meter."""
    sock = _FakeSocket([{"type": "session.ready"}])
    sock.fail_recv = ConnectionResetError("dropped")
    out = await _start(_session(_RecordingConnect(sock)))
    assert out is not None and out.verified is None and out.error == "ConnectionResetError"
    assert sock.kinds()[-1] == "session.end" and sock.closed


async def test_the_duration_cap_is_enforced_here_not_trusted_to_the_model() -> None:
    sock = _FakeSocket([{"type": "session.ready"}])  # then silence, forever
    out = await _start(_session(_RecordingConnect(sock), max_duration_s=0.1))
    assert out is not None and out.verified is None and out.error == "duration_cap"
    assert sock.kinds()[-1] == "session.end"


async def test_a_report_made_before_the_cap_survives_it() -> None:
    sock = _FakeSocket([{"type": "session.ready"}, _report(verified=True, reason="yes we called")])
    out = await _start(_session(_RecordingConnect(sock), max_duration_s=0.1))
    assert out is not None and out.verified is True


async def test_the_desk_hanging_up_first_is_unanswered() -> None:
    desk = _FakeDesk()
    desk.mic.put_nowait(None)
    sock = _FakeSocket([])
    out = await _start(_session(_RecordingConnect(sock)), desk)
    assert out is not None and out.verified is None and out.error == "desk_hangup"
    assert sock.kinds()[-1] == "session.end"


async def test_an_unusable_report_is_refused_and_a_retry_is_accepted() -> None:
    sock = _FakeSocket(
        [
            {"type": "session.ready"},
            _report("bad", verified="nope"),
            {"type": "reply.done"},
            _report("good", verified=False, reason="no record"),
            {"type": "reply.done"},
            {"type": "reply.done"},
        ]
    )
    out = await _start(_session(_RecordingConnect(sock)))
    assert out is not None and out.verified is False and out.reason == "no record"
    results = {
        str(m["call_id"]): json.loads(str(m["result"]))
        for m in sock.sent
        if m["type"] == "tool.result"
    }
    assert "error" in results["bad"] and results["good"] == {"recorded": True}


async def test_a_second_report_cannot_rewrite_the_first() -> None:
    sock = _FakeSocket(
        [
            {"type": "session.ready"},
            _report("one", verified=False, reason="no record"),
            _report("two", verified=True, reason="actually yes"),
            {"type": "reply.done"},
            {"type": "reply.done"},
        ]
    )
    out = await _start(_session(_RecordingConnect(sock)))
    assert out is not None and out.verified is False


async def test_the_goodbye_window_closes_on_its_own() -> None:
    """If the agent never finishes a goodbye, the verdict still comes back."""
    sock = _FakeSocket([{"type": "session.ready"}, _report(), {"type": "reply.done"}])
    session = _session(_RecordingConnect(sock), farewell_grace_s=0.05, max_duration_s=5.0)
    out = await asyncio.wait_for(_start(session), 1.0)
    assert out is not None and out.verified is False and out.error is None


async def test_an_unknown_tool_gets_an_error_result_not_a_verdict() -> None:
    sock = _FakeSocket(
        [
            {"type": "session.ready"},
            {"type": "tool.call", "call_id": "x", "name": "transfer_funds", "arguments": {}},
            {"type": "reply.done"},
            {"type": "session.ended"},
        ]
    )
    out = await _start(_session(_RecordingConnect(sock)))
    assert out is not None and out.verified is None
    (result,) = [m for m in sock.sent if m["type"] == "tool.result"]
    assert json.loads(str(result["result"])) == {"error": "unknown tool"}


async def test_malformed_frames_do_not_end_the_conversation() -> None:
    sock = _FakeSocket(
        [
            "not json",
            {"type": "reply.audio", "data": "!!"},
            {"type": "session.ready"},
            _report(),
            {"type": "session.ended"},
        ]
    )
    out = await _start(_session(_RecordingConnect(sock)))
    assert out is not None and out.verified is False
