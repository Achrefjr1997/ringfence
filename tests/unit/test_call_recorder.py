"""Oversight console P1 -- CallLedgerRecorder consumes rf.*.decision."""

from __future__ import annotations

import asyncio

import pytest

from apps.gateway.call_recorder import CallLedgerRecorder
from packages.calls.ledger import InMemoryCallLedger
from packages.contracts.events import InProcessBus


def _decision(session_id: str, *, state: str, score: float, t: float) -> dict[str, object]:
    return {"session_id": session_id, "state": state, "score": score, "t": t}


async def test_run_appends_score_points_for_open_calls_only() -> None:
    lg = InMemoryCallLedger()
    lg.open("s1", tenant="acme", api_key_id="k1", started_at=0.0)
    rec = CallLedgerRecorder(lg)

    bus = InProcessBus()
    task = asyncio.create_task(rec.run(bus))
    await asyncio.sleep(0)
    await bus.publish("rf.acme.decision", _decision("s1", state="WATCH", score=30.0, t=5.0))
    await bus.publish("rf.acme.decision", _decision("s1", state="ALERT", score=62.0, t=9.0))
    await bus.publish("rf.acme.decision", _decision("ghost", state="ALERT", score=99.0, t=1.0))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    got = lg.get("s1")
    assert got is not None
    assert [(p.state, p.score) for p in got.scores] == [("WATCH", 30.0), ("ALERT", 62.0)]
    assert got.peak_state == "ALERT" and got.peak_score == 62.0
    assert lg.get("ghost") is None  # never opened -> ignored


async def test_malformed_events_do_not_crash_the_loop() -> None:
    lg = InMemoryCallLedger()
    lg.open("s1", tenant="acme", api_key_id=None, started_at=0.0)
    rec = CallLedgerRecorder(lg)
    bus = InProcessBus()
    task = asyncio.create_task(rec.run(bus))
    await asyncio.sleep(0)
    await bus.publish("rf.acme.decision", {"nonsense": True})
    await bus.publish("rf.acme.decision", {"session_id": "s1", "state": "BOGUS"})
    await bus.publish("rf.acme.decision", _decision("s1", state="CALM", score=1.0, t=1.0))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    got = lg.get("s1")
    assert got is not None and len(got.scores) == 1
