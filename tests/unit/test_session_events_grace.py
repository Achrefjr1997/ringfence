"""session_events(): found live, not in review. The LLM coach answers up
to ~2s after the INTERVENE decision that triggers it, but a /replay
session closes almost instantly -- well before that. The SSE stream used
to return the moment .session.closed arrived, tearing the connection down
before the coach's delayed rf.*.warning could ever reach it. These tests
exercise the actual race with real asyncio concurrency (a background task
publishing after a real sleep), not a pre-loaded backlog -- a backlog-only
test would pass even with the old, buggy "return on close" code, since
every event would already be queued before the subscriber ever starts.
"""

from __future__ import annotations

import asyncio

from apps.gateway.app import session_events
from packages.contracts.events import InProcessBus


async def _collect(bus: InProcessBus, session_id: str, *, close_grace_s: float) -> list[str]:
    kinds = []
    async for item in session_events(bus, "rf.*", session_id, close_grace_s=close_grace_s):
        kinds.append(item["event"])
    return kinds


async def test_a_warning_published_after_close_still_arrives_within_grace() -> None:
    bus = InProcessBus()

    async def _late_publish() -> None:
        await asyncio.sleep(0.05)
        await bus.publish("rf.acme.warning", {"session_id": "s1", "text": "refined"})

    task = asyncio.create_task(_late_publish())
    await bus.publish("rf.acme.decision", {"session_id": "s1", "state": "INTERVENE"})
    await bus.publish("rf.acme.session.closed", {"session_id": "s1"})

    kinds = await asyncio.wait_for(_collect(bus, "s1", close_grace_s=1.0), timeout=2.0)
    await task
    assert kinds == ["decision", "end", "warning"]


async def test_nothing_further_arriving_still_ends_after_the_grace_period() -> None:
    bus = InProcessBus()
    await bus.publish("rf.acme.session.closed", {"session_id": "s1"})

    kinds = await asyncio.wait_for(_collect(bus, "s1", close_grace_s=0.05), timeout=2.0)
    assert kinds == ["end"]


async def test_a_warning_arriving_after_the_grace_period_expires_is_missed() -> None:
    """Documents the actual boundary rather than asserting infinite
    patience: the grace period is bounded on purpose, so a viewer's
    connection does not hang open forever on the rare session where the
    coach never answers."""
    bus = InProcessBus()

    async def _too_late_publish() -> None:
        await asyncio.sleep(0.15)
        await bus.publish("rf.acme.warning", {"session_id": "s1", "text": "too late"})

    task = asyncio.create_task(_too_late_publish())
    await bus.publish("rf.acme.session.closed", {"session_id": "s1"})

    kinds = await asyncio.wait_for(_collect(bus, "s1", close_grace_s=0.05), timeout=2.0)
    assert kinds == ["end"]
    await task  # let the late publish land so it doesn't warn on teardown


async def test_a_second_session_closed_does_not_restart_the_grace_clock() -> None:
    """Only the first close should arm the deadline -- a second closed
    event for the same session (should not normally happen, but must not
    be trusted to) must not grant the stream a fresh grace window."""
    bus = InProcessBus()
    await bus.publish("rf.acme.session.closed", {"session_id": "s1"})
    await bus.publish("rf.acme.session.closed", {"session_id": "s1"})

    kinds = await asyncio.wait_for(_collect(bus, "s1", close_grace_s=0.05), timeout=2.0)
    assert kinds == ["end", "end"]


async def test_other_sessions_are_filtered_out_throughout() -> None:
    bus = InProcessBus()
    await bus.publish("rf.acme.decision", {"session_id": "other", "state": "INTERVENE"})
    await bus.publish("rf.acme.session.closed", {"session_id": "s1"})
    await bus.publish("rf.acme.warning", {"session_id": "other", "text": "not for us"})

    kinds = await asyncio.wait_for(_collect(bus, "s1", close_grace_s=0.05), timeout=2.0)
    assert kinds == ["end"]
