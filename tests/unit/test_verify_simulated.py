"""Phase 1 wiring: the simulated verifier, the master switch, and the stream.

The simulated verifier exists so the verification path demos end to end with
no API key. Its one hard requirement is honesty: every outcome is flagged, and
the flag survives all the way onto the banner payload, so a result that reads
"We called Amazon directly" is never shown unmarked when nobody called anyone.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from apps.gateway.app import session_events
from apps.gateway.verify_dispatch import VerificationDispatcher
from packages.contracts.audio import Mode
from packages.contracts.events import InProcessBus
from packages.contracts.settings import Settings
from packages.verify.directory import Directory, Institution
from packages.verify.simulated import SimulatedVerifier

_INST = Institution.model_validate(
    {
        "id": "amazon",
        "display_name": "Amazon",
        "desk_id": "demo_desk",
        "line_label": "account security",
        "aliases": ["amazon account security", "amazon"],
    }
)


async def test_every_simulated_outcome_is_flagged_as_simulated() -> None:
    outcome = await SimulatedVerifier(delay_s=0).verify(
        institution=_INST, amount=None, mode=Mode.SDK
    )
    assert outcome is not None
    assert outcome.simulated is True
    assert outcome.verified is False
    assert "Amazon" in outcome.reason


def test_the_simulated_verifier_cannot_reach_a_network() -> None:
    """It needs no guard only because it has nothing to guard. Keep it that
    way: the day it imports a network client, it needs the real one's guard."""
    src = (Path(__file__).resolve().parents[2] / "packages/verify/simulated.py").read_text(
        encoding="utf-8"
    )
    for client in ("websockets", "httpx", "aiohttp", "socket", "urllib"):
        assert f"import {client}" not in src and f"from {client}" not in src


def test_the_master_switch_defaults_off() -> None:
    assert Settings().verify_enabled is False


async def test_the_simulated_flag_survives_onto_the_banner_payload() -> None:
    bus = InProcessBus()
    d = VerificationDispatcher(
        bus,
        verifier=SimulatedVerifier(delay_s=0),
        directory=Directory([_INST]),
        enabled=True,
    )
    await d.on_turn(
        "rf.acme.turn", {"session_id": "s1", "role": "CALLER", "text": "amazon account security"}
    )
    await d.on_decision(
        "rf.acme.decision",
        {"session_id": "s1", "decision_id": "d1", "t": 9.0, "state": "INTERVENE", "mode": "sdk"},
    )

    got: list[dict[str, Any]] = []

    async def _watch() -> None:
        async with contextlib.aclosing(bus.subscribe("rf.acme.*")) as stream:
            async for subject, payload in stream:
                if subject.endswith((".warning", ".verification")):
                    got.append({"subject": subject, **payload})
                if subject.endswith(".warning"):
                    return

    await asyncio.wait_for(_watch(), timeout=1.0)
    warning = next(g for g in got if g["subject"].endswith(".warning"))
    result = next(g for g in got if g.get("stage") == "result")
    assert warning["simulated"] is True
    assert result["simulated"] is True


async def test_the_session_stream_forwards_verification_events() -> None:
    bus = InProcessBus()
    await bus.publish("rf.acme.verification", {"session_id": "s1", "stage": "dialing"})
    await bus.publish("rf.acme.session.closed", {"session_id": "s1"})

    kinds = [item["event"] async for item in session_events(bus, "rf.*", "s1", close_grace_s=0.05)]
    assert kinds == ["verification", "end"]
