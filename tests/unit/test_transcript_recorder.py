"""Oversight console P6 -- TranscriptRecorder + the RF_RETAIN_TRANSCRIPTS gate."""

from __future__ import annotations

import asyncio

import pytest

from apps.gateway.call_recorder import TranscriptRecorder
from packages.calls.transcripts import InMemoryTranscriptStore
from packages.contracts.events import InProcessBus
from packages.contracts.settings import get_settings


async def _drive(retain: bool, monkeypatch: pytest.MonkeyPatch) -> InMemoryTranscriptStore:
    get_settings.cache_clear()  # type: ignore[attr-defined]
    monkeypatch.setenv("RF_RETAIN_TRANSCRIPTS", "true" if retain else "false")
    get_settings.cache_clear()  # type: ignore[attr-defined]

    store = InMemoryTranscriptStore()
    rec = TranscriptRecorder(store)
    bus = InProcessBus()
    task = asyncio.create_task(rec.run(bus))
    await asyncio.sleep(0)
    await bus.publish(
        "rf.acme.turn", {"session_id": "s1", "role": "CALLER", "text": "hello", "t_end": 1.0}
    )
    await bus.publish(
        "rf.acme.turn", {"session_id": "s1", "role": "CALLEE", "text": "who is this", "t_end": 3.0}
    )
    await bus.publish(
        "rf.replay.turn", {"session_id": "s1", "role": "CALLER", "text": "ignored", "t_end": 4.0}
    )
    await bus.publish("rf.acme.session.closed", {"session_id": "s1"})
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    get_settings.cache_clear()  # type: ignore[attr-defined]
    return store


async def test_flushes_the_transcript_when_retention_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = await _drive(retain=True, monkeypatch=monkeypatch)
    assert store.get("s1") == [("CALLER", "hello", 1.0), ("CALLEE", "who is this", 3.0)]


@pytest.mark.invariant
async def test_saves_nothing_when_retention_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = await _drive(retain=False, monkeypatch=monkeypatch)
    assert store.get("s1") == []


async def test_buffer_is_cleared_on_close_regardless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RF_RETAIN_TRANSCRIPTS", "false")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    store = InMemoryTranscriptStore()
    rec = TranscriptRecorder(store)
    bus = InProcessBus()
    task = asyncio.create_task(rec.run(bus))
    await asyncio.sleep(0)
    await bus.publish(
        "rf.acme.turn", {"session_id": "s1", "role": "CALLER", "text": "x", "t_end": 1.0}
    )
    await bus.publish("rf.acme.session.closed", {"session_id": "s1"})
    await asyncio.sleep(0.02)
    # a stray late turn after close must not resurrect the buffer
    await bus.publish("rf.acme.session.closed", {"session_id": "s1"})
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    get_settings.cache_clear()  # type: ignore[attr-defined]
    assert store.get("s1") == []
