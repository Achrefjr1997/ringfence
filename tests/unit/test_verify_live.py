"""live.py: guard, then ring, then talk -- in that order."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

from packages.contracts.audio import Mode
from packages.contracts.verify import VerificationStage
from packages.verify.agent import VoiceAgentSession
from packages.verify.desk import DeskExchange
from packages.verify.directory import Institution
from packages.verify.live import VoiceAgentVerifier

_INST = Institution.model_validate(
    {
        "id": "amazon",
        "display_name": "Amazon",
        "desk_id": "demo_desk",
        "line_label": "account security",
        "aliases": ["amazon"],
    }
)


class _Socket:
    def __init__(self, frames: list[dict[str, object]]) -> None:
        self.inbox: asyncio.Queue[str] = asyncio.Queue()
        for f in frames:
            self.inbox.put_nowait(json.dumps(f))
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        return await self.inbox.get()

    async def close(self) -> None:
        pass


class _Connect:
    def __init__(self, socket: _Socket) -> None:
        self.socket = socket
        self.opened = 0

    async def __call__(self, url: str, *, additional_headers: dict[str, str]) -> _Socket:
        self.opened += 1
        return self.socket


class _Progress:
    def __init__(self) -> None:
        self.stages: list[tuple[VerificationStage, dict[str, str]]] = []

    async def __call__(self, stage: VerificationStage, detail: Mapping[str, str]) -> None:
        self.stages.append((stage, dict(detail)))


def _verifier(
    frames: list[dict[str, object]], *, dry_run: bool = False, ttl_s: float = 5.0
) -> tuple[VoiceAgentVerifier, DeskExchange, _Connect]:
    connect = _Connect(_Socket(frames))
    session = VoiceAgentSession(
        api_key="k", connect=connect, dry_run=dry_run, farewell_grace_s=0.05
    )
    exchange = DeskExchange(ttl_s=ttl_s)
    return VoiceAgentVerifier(session=session, exchange=exchange), exchange, connect


async def _answer_when_ringing(exchange: DeskExchange):  # noqa: ANN202
    async for kind, offer in exchange.notices():
        if kind == "offer":
            return exchange.redeem(offer.ticket)
    return None


async def test_dry_run_never_even_rings_the_desk() -> None:
    """A human paged by a dry run is a side effect too."""
    verifier, exchange, connect = _verifier([], dry_run=True)
    progress = _Progress()
    assert (
        await verifier.verify(institution=_INST, amount=None, mode=Mode.SDK, progress=progress)
        is None
    )
    assert exchange.pending_offers() == [] and progress.stages == [] and connect.opened == 0


async def test_replay_never_even_rings_the_desk() -> None:
    verifier, exchange, connect = _verifier([])
    assert await verifier.verify(institution=_INST, amount=None, mode=Mode.REPLAY) is None
    assert exchange.pending_offers() == [] and connect.opened == 0


async def test_an_unanswered_desk_is_unknown_and_no_session_is_paid_for() -> None:
    verifier, _, connect = _verifier([], ttl_s=0.05)
    progress = _Progress()
    out = await verifier.verify(institution=_INST, amount=None, mode=Mode.SDK, progress=progress)
    assert out is not None and out.verified is None and out.error == "no_answer"
    assert [s for s, _ in progress.stages] == ["ringing"]
    assert connect.opened == 0


async def test_answered_ring_runs_the_conversation_and_streams_it() -> None:
    frames: list[dict[str, object]] = [
        {"type": "session.ready"},
        {"type": "transcript.agent", "text": "Did Amazon call this customer?"},
        {"type": "transcript.user", "text": "No."},
        {
            "type": "tool.call",
            "call_id": "c1",
            "name": "report_verification",
            "arguments": {"verified": False, "reason": "No."},
        },
        {"type": "reply.done"},
        {"type": "reply.done"},
    ]
    verifier, exchange, connect = _verifier(frames)
    progress = _Progress()
    desk = asyncio.create_task(_answer_when_ringing(exchange))
    out = await verifier.verify(institution=_INST, amount=None, mode=Mode.SDK, progress=progress)
    line = await desk

    assert out is not None and out.verified is False and out.simulated is False
    assert connect.opened == 1
    assert [s for s, _ in progress.stages] == ["ringing", "connected", "transcript", "transcript"]
    assert progress.stages[3][1] == {"role": "desk", "text": "No."}

    assert line is not None
    tail: list[bytes | str | None] = []
    while (item := await line.next_outbound()) is not None:
        tail.append(item)
    assert json.loads(str(tail[-1])) == {"type": "ended", "verified": "false"}


async def test_a_broken_progress_sink_does_not_stop_the_verification() -> None:
    verifier, _, _ = _verifier([], ttl_s=0.05)

    async def broken(stage: VerificationStage, detail: Mapping[str, str]) -> None:
        raise RuntimeError("viewer went away")

    out = await verifier.verify(institution=_INST, amount=None, mode=Mode.SDK, progress=broken)
    assert out is not None and out.error == "no_answer"
