"""T-4.1 wiring -- InterventionDispatcher: INTERVENE on the bus -> a live
coaching warning, published back so a viewer sees it the same turn it
fires. Mirrors test_guardian_dispatch.py's shape, for the sibling
dispatcher that watches the same rf.*.decision stream.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from apps.gateway.intervene_dispatch import InterventionDispatcher
from packages.contracts.events import InProcessBus
from packages.policy.pack import load_pack

PACK = load_pack("config/policy/default.yaml")


def _event(*, state: str = "INTERVENE", language: str = "en") -> dict[str, object]:
    return {
        "session_id": "s1",
        "decision_id": "d1",
        "t": 27.0,
        "state": state,
        "score": 100.0,
        "counterfactual": None,
        "language": language,
        "contributions": [
            {"source": "signal", "id": "AUTH_CLAIM", "value": 9.9, "role": "CALLER"},
            {"source": "signal", "id": "VERIF_INVERT", "value": 28.4, "role": "CALLER"},
            {"source": "combo", "id": "COMBO_CLASSIC", "value": 45.0, "role": None},
        ],
    }


async def _collect_one(bus: InProcessBus, pattern: str) -> dict[str, object]:
    async with contextlib.aclosing(bus.subscribe(pattern)) as stream:
        async for _subject, payload in stream:
            return payload
    raise AssertionError("bus closed before a matching event arrived")  # pragma: no cover


async def test_intervene_publishes_a_warning_for_the_console_to_render() -> None:
    bus = InProcessBus()
    d = InterventionDispatcher(PACK, bus, dry_run=False)
    warning = await d.dispatch("acme", _event())
    assert warning is not None and warning.delivered

    payload = await _collect_one(bus, "rf.acme.warning")
    assert payload["session_id"] == "s1"
    assert payload["decision_id"] == "d1"
    assert payload["text"]  # non-empty coaching copy, unlike the guardian channel


async def test_only_app_banner_is_delivered_not_guardian_push() -> None:
    """guardian_push already has its own dispatcher (guardian.py) with its
    own signed webhook; firing it again here would double-notify. Only one
    bus publish should happen per decision, not one per configured channel."""
    bus = InProcessBus()
    seen: list[str] = []

    async def _watch() -> None:
        async for subject, _payload in bus.subscribe("rf.acme.*"):
            seen.append(subject)

    task = asyncio.create_task(_watch())
    await asyncio.sleep(0)
    d = InterventionDispatcher(PACK, bus, dry_run=False)
    await d.dispatch("acme", _event())
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert seen == ["rf.acme.warning"]  # not three, one per channel configured


async def test_calm_and_watch_and_alert_do_not_fire() -> None:
    bus = InProcessBus()
    d = InterventionDispatcher(PACK, bus, dry_run=False)
    for state in ("CALM", "WATCH", "ALERT"):
        assert await d.dispatch("acme", _event(state=state)) is None


async def test_dry_run_suppresses_delivery_but_still_returns_the_warning() -> None:
    """Matches InterventionService's own contract: dry_run logs and returns
    the Warning with delivered=False, it does not raise or return None."""
    bus = InProcessBus()
    d = InterventionDispatcher(PACK, bus, dry_run=True)
    warning = await d.dispatch("acme", _event())
    assert warning is not None and warning.delivered is False


async def test_cooldown_is_shared_across_calls_for_the_same_tenant() -> None:
    """The dispatcher must reuse one InterventionService per tenant, or
    cooldown state resets every dispatch and never actually cools down."""
    bus = InProcessBus()
    d = InterventionDispatcher(PACK, bus, dry_run=False)
    first = await d.dispatch("acme", _event())
    second = await d.dispatch("acme", _event())  # same session, immediately after
    assert first is not None and first.delivered
    assert second is None  # cooldown -- same session, no elapsed time


async def test_the_warning_uses_the_language_carried_on_the_event() -> None:
    bus = InProcessBus()
    d = InterventionDispatcher(PACK, bus, dry_run=False)
    warning = await d.dispatch("acme", _event(language="fr"))
    assert warning is not None and warning.language == "fr"


async def test_missing_language_on_the_event_falls_back_to_english() -> None:
    bus = InProcessBus()
    d = InterventionDispatcher(PACK, bus, dry_run=False)
    event = _event()
    del event["language"]
    warning = await d.dispatch("acme", event)
    assert warning is not None and warning.language == "en"


async def test_run_consumes_the_bus_and_ignores_non_intervene_and_replay() -> None:
    bus = InProcessBus()
    d = InterventionDispatcher(PACK, bus, dry_run=False)

    task = asyncio.create_task(d.run())
    await asyncio.sleep(0)
    await bus.publish("rf.acme.decision", {**_event(), "state": "ALERT"})  # ignored
    await bus.publish("rf.replay.decision", _event())  # replay tenant skipped
    await bus.publish("rf.acme.decision", _event())  # fires
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    payload = await _collect_one(bus, "rf.acme.warning")
    assert payload["session_id"] == "s1"
