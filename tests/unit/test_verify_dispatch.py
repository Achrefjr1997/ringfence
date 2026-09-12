"""VerificationDispatcher: the decision to contact someone.

Watches the same bus as InterventionDispatcher, but has to do something none
of the other consumers do -- reconstruct *who the caller claimed to be*. That
is not on the decision event (pipeline.py strips `evidence`), so it comes
from the rf.*.turn stream, and only ever from CALLER turns.

The CALLER-only rule is the same idea as invariant #1: a frightened victim
repeating "Amazon" back at the scammer must never be what causes Amazon to be
contacted.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from packages.contracts.audio import Mode
from packages.contracts.verify import VerificationOutcome
from packages.verify.directory import Directory, Institution
from apps.gateway.verify_dispatch import VerificationDispatcher
from packages.contracts.events import InProcessBus

_INST = Institution.model_validate(
    {
        "id": "amazon",
        "display_name": "Amazon",
        "desk_id": "demo_desk",
        "line_label": "account security",
        "aliases": ["amazon account security", "amazon"],
    }
)


def _directory() -> Directory:
    return Directory([_INST])


class _StubVerifier:
    """Stands in for the Voice Agent session. Phase 2 replaces it."""

    def __init__(self, outcome: VerificationOutcome | None = None) -> None:
        self.outcome = outcome or VerificationOutcome(
            verified=False, reason="no record of that call", confidence="high"
        )
        self.calls: list[Institution] = []

    async def verify(self, *, institution: Institution, amount: str | None, mode: Mode):  # noqa: ANN201
        self.calls.append(institution)
        return self.outcome


def _dispatcher(bus: InProcessBus, verifier: _StubVerifier | None = None, **kw: object):  # noqa: ANN201
    return VerificationDispatcher(
        bus,
        directory=_directory(),
        verifier=verifier or _StubVerifier(),
        enabled=True,
        **kw,  # type: ignore[arg-type]
    )


def _turn(text: str, *, role: str = "CALLER", session: str = "s1") -> dict[str, object]:
    return {"session_id": session, "role": role, "text": text, "t_start": 1.0, "t_end": 2.0}


def _decision(
    *, state: str = "INTERVENE", mode: str = "sdk", session: str = "s1"
) -> dict[str, object]:
    return {
        "session_id": session,
        "decision_id": "d1",
        "t": 27.0,
        "state": state,
        "score": 100.0,
        "language": "en",
        "mode": mode,
        "contributions": [],
    }


async def _events(bus: InProcessBus, pattern: str, n: int, timeout: float = 1.0) -> list[dict]:
    got: list[dict] = []

    async def _watch() -> None:
        async with contextlib.aclosing(bus.subscribe(pattern)) as stream:
            async for _s, payload in stream:
                got.append(payload)
                if len(got) >= n:
                    return

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(_watch(), timeout=timeout)
    return got


# -- the happy path ----------------------------------------------------------


async def test_a_named_institution_is_verified_and_the_result_published() -> None:
    bus = InProcessBus()
    verifier = _StubVerifier()
    d = _dispatcher(bus, verifier)

    await d.on_turn("rf.acme.turn", _turn("this is Daniel from Amazon account security"))
    await d.on_decision("rf.acme.decision", _decision())

    assert [i.id for i in verifier.calls] == ["amazon"]
    stages = [e["stage"] for e in await _events(bus, "rf.acme.verification", 3)]
    assert stages == ["dialing", "result", "result"][: len(stages)]
    assert any(e.get("verified") is False for e in await _events(bus, "rf.acme.verification", 3))


async def test_the_result_also_lands_on_the_existing_warning_subject() -> None:
    """So the console's coach banner renders it with no new front-end code,
    in every language templates.yaml already carries."""
    bus = InProcessBus()
    d = _dispatcher(bus)
    await d.on_turn("rf.acme.turn", _turn("amazon account security here"))
    await d.on_decision("rf.acme.decision", _decision())

    warnings = await _events(bus, "rf.acme.warning", 1)
    assert warnings and warnings[0]["template_id"] == "VERIFY_UNCONFIRMED"
    assert warnings[0]["text"]


# -- who is allowed to name the institution ----------------------------------


async def test_only_the_caller_can_name_the_institution() -> None:
    """Invariant #1's echo. The victim saying "Amazon" back -- which is what
    a frightened person does -- must never contact Amazon."""
    bus = InProcessBus()
    verifier = _StubVerifier()
    d = _dispatcher(bus, verifier)

    await d.on_turn("rf.acme.turn", _turn("wait, Amazon account security?", role="CALLEE"))
    await d.on_decision("rf.acme.decision", _decision())

    assert verifier.calls == []
    skipped = await _events(bus, "rf.acme.verification", 1)
    assert skipped and skipped[0]["stage"] == "skipped"
    assert skipped[0]["reason"] == "no_institution"


async def test_an_unknown_institution_is_skipped_with_a_visible_reason() -> None:
    bus = InProcessBus()
    verifier = _StubVerifier()
    d = _dispatcher(bus, verifier)
    await d.on_turn("rf.acme.turn", _turn("this is your electricity supplier"))
    await d.on_decision("rf.acme.decision", _decision())

    assert verifier.calls == []
    assert (await _events(bus, "rf.acme.verification", 1))[0]["reason"] == "no_institution"


# -- when it must not act ----------------------------------------------------


async def test_a_replay_sourced_decision_never_verifies() -> None:
    """The hole that existed before `mode` was on the decision event: this
    tenant is "acme", not "replay", so the tenant string alone cannot see it."""
    bus = InProcessBus()
    verifier = _StubVerifier()
    d = _dispatcher(bus, verifier)
    await d.on_turn("rf.acme.turn", _turn("amazon account security"))
    await d.on_decision("rf.acme.decision", _decision(mode=Mode.REPLAY.value))
    assert verifier.calls == []


async def test_the_replay_tenant_never_verifies() -> None:
    bus = InProcessBus()
    verifier = _StubVerifier()
    d = _dispatcher(bus, verifier)
    await d.on_turn("rf.replay.turn", _turn("amazon account security"))
    await d.on_decision("rf.replay.decision", _decision())
    assert verifier.calls == []


@pytest.mark.parametrize("state", ["CALM", "WATCH", "ALERT"])
async def test_nothing_below_intervene_verifies(state: str) -> None:
    bus = InProcessBus()
    verifier = _StubVerifier()
    d = _dispatcher(bus, verifier)
    await d.on_turn("rf.acme.turn", _turn("amazon account security"))
    await d.on_decision("rf.acme.decision", _decision(state=state))
    assert verifier.calls == []


async def test_a_session_verifies_only_once() -> None:
    bus = InProcessBus()
    verifier = _StubVerifier()
    d = _dispatcher(bus, verifier)
    await d.on_turn("rf.acme.turn", _turn("amazon account security"))
    await d.on_decision("rf.acme.decision", _decision())
    await d.on_decision("rf.acme.decision", _decision())
    assert len(verifier.calls) == 1


async def test_disabled_means_nothing_happens_at_all() -> None:
    bus = InProcessBus()
    verifier = _StubVerifier()
    d = VerificationDispatcher(bus, directory=_directory(), verifier=verifier, enabled=False)
    await d.on_turn("rf.acme.turn", _turn("amazon account security"))
    await d.on_decision("rf.acme.decision", _decision())
    assert verifier.calls == []
    assert await _events(bus, "rf.acme.verification", 1, timeout=0.05) == []


# -- the turn buffer ---------------------------------------------------------


async def test_the_buffer_is_per_session() -> None:
    bus = InProcessBus()
    verifier = _StubVerifier()
    d = _dispatcher(bus, verifier)
    await d.on_turn("rf.acme.turn", _turn("amazon account security", session="other"))
    await d.on_decision("rf.acme.decision", _decision(session="s1"))
    assert verifier.calls == []


async def test_the_buffer_is_bounded() -> None:
    """Invariant #6 -- a long call must not grow the buffer without limit."""
    bus = InProcessBus()
    d = _dispatcher(bus)
    for i in range(500):
        await d.on_turn("rf.acme.turn", _turn(f"filler turn number {i}"))
    assert len(d._buffer) <= 64


async def test_an_outcome_of_none_is_reported_as_unreachable_not_as_a_denial() -> None:
    """ "We could not reach them" and "they say they never called you" are
    different claims. Collapsing the first into the second would render a
    dropped connection as an accusation."""
    bus = InProcessBus()
    verifier = _StubVerifier(VerificationOutcome(verified=None, reason="no answer"))
    d = _dispatcher(bus, verifier)
    await d.on_turn("rf.acme.turn", _turn("amazon account security"))
    await d.on_decision("rf.acme.decision", _decision())

    warnings = await _events(bus, "rf.acme.warning", 1)
    assert warnings and warnings[0]["template_id"] == "VERIFY_FAILED"
