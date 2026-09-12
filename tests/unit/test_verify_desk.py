"""desk.py: single-use tickets, and a line that cannot grow without bound."""

from __future__ import annotations

import asyncio
import json

from packages.verify.desk import DeskExchange, DeskLine
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


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


# -- tickets -----------------------------------------------------------------


async def test_an_offer_shows_the_desk_only_the_institution() -> None:
    offer = DeskExchange().offer(_INST)
    assert offer is not None
    assert set(offer.public()) == {"ticket", "desk_id", "institution", "line_label"}
    assert offer.public()["institution"] == "Amazon"
    assert len(offer.ticket) >= 32  # token_urlsafe(24)


async def test_a_ticket_redeems_once_and_the_waiter_gets_the_same_line() -> None:
    ex = DeskExchange()
    offer = ex.offer(_INST)
    assert offer is not None
    waiter = asyncio.create_task(ex.wait_answer(offer))
    await asyncio.sleep(0)
    line = ex.redeem(offer.ticket)
    assert line is not None
    assert await waiter is line
    assert ex.redeem(offer.ticket) is None  # single-use


async def test_an_unknown_ticket_redeems_nothing() -> None:
    ex = DeskExchange()
    ex.offer(_INST)
    assert ex.redeem("not-a-ticket") is None


async def test_an_expired_ticket_is_refused_and_consumed() -> None:
    clock = _Clock()
    ex = DeskExchange(ttl_s=30.0, now=clock)
    offer = ex.offer(_INST)
    assert offer is not None
    clock.t += 31
    assert ex.redeem(offer.ticket) is None
    assert ex.pending_offers() == []


async def test_an_unanswered_offer_rings_out_and_is_withdrawn() -> None:
    ex = DeskExchange(ttl_s=0.05)
    offer = ex.offer(_INST)
    assert offer is not None
    assert await ex.wait_answer(offer) is None
    assert ex.redeem(offer.ticket) is None
    assert ex.pending_offers() == []


async def test_pending_offers_are_bounded() -> None:
    ex = DeskExchange(max_pending=2)
    assert ex.offer(_INST) is not None
    assert ex.offer(_INST) is not None
    assert ex.offer(_INST) is None


async def test_a_late_subscriber_still_sees_the_ringing_offer_then_its_withdrawal() -> None:
    ex = DeskExchange()
    offer = ex.offer(_INST)
    assert offer is not None
    notices = ex.notices()
    assert await anext(notices) == ("offer", offer)
    ex.redeem(offer.ticket)
    assert await anext(notices) == ("withdrawn", offer)
    await notices.aclose()


# -- the line ----------------------------------------------------------------


async def test_desk_audio_reaches_the_agent_and_hang_up_ends_it() -> None:
    line = DeskLine()
    line.push_audio(b"\x01\x00")
    line.hang_up()
    assert await line.receive_audio() == b"\x01\x00"
    assert await line.receive_audio() is None


async def test_the_uplink_keeps_the_latest_speech_when_full() -> None:
    line = DeskLine()
    for i in range(200):
        line.push_audio(i.to_bytes(2, "little"))
    first = await line.receive_audio()
    assert first is not None and int.from_bytes(first, "little") > 0  # oldest dropped


async def test_agent_audio_and_events_reach_the_desk_in_order_then_close() -> None:
    line = DeskLine()
    await line.send_audio(b"\x02\x00")
    await line.send_event({"type": "transcript", "role": "agent", "text": "hello"})
    line.close({"type": "ended", "verified": "false"})
    assert await line.next_outbound() == b"\x02\x00"
    assert json.loads(str(await line.next_outbound()))["text"] == "hello"
    assert json.loads(str(await line.next_outbound())) == {"type": "ended", "verified": "false"}
    assert await line.next_outbound() is None


async def test_a_hung_up_desk_never_blocks_the_agent() -> None:
    line = DeskLine()
    line.hang_up()
    for _ in range(2000):  # far past the queue bound
        await asyncio.wait_for(line.send_audio(b"\x00\x00"), 0.1)
