"""VerificationDispatcher: the decision to contact someone.

Watches the same bus as InterventionDispatcher, but has to do something none
of the other consumers do -- reconstruct *who the caller claimed to be*. That
is not on the decision event (pipeline.py strips `evidence`), so it comes
from the rf.*.turn stream: CALLER turns first, UNKNOWN turns as a fallback
(speakerphone capture labels the opening seconds UNKNOWN), never CALLEE.

The never-CALLEE rule is the same idea as invariant #1: a frightened victim
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
        self.amounts: list[str | None] = []

    async def verify(  # noqa: ANN201
        self, *, institution: Institution, amount: str | None, mode: Mode, progress=None
    ):
        self.calls.append(institution)
        self.amounts.append(amount)
        if progress is not None:
            await progress("transcript", {"role": "desk", "text": "no record"})
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
    assert stages == ["dialing", "transcript", "result"][: len(stages)]
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


# -- the calibration-window fallback (found live) ----------------------------


async def test_an_unattributed_opening_line_can_name_the_institution() -> None:
    """Found against the real stack: speakerphone capture labels turns UNKNOWN
    until the acoustic classifier calibrates, and that window is exactly when
    a caller says "this is Daniel from Amazon". A CALLER-only rule skipped a
    genuine INTERVENE as no_institution. UNKNOWN is now a fallback source."""
    bus = InProcessBus()
    verifier = _StubVerifier()
    d = _dispatcher(bus, verifier)
    await d.on_turn(
        "rf.acme.turn", _turn("this is Daniel from Amazon account security", role="UNKNOWN")
    )
    await d.on_decision("rf.acme.decision", _decision())

    assert [i.id for i in verifier.calls] == ["amazon"]
    first = (await _events(bus, "rf.acme.verification", 1))[0]
    assert first["stage"] == "dialing"
    assert first["name_source"] == "unattributed"


async def test_caller_speech_outranks_unattributed_speech() -> None:
    """When attribution exists, it wins. The fallback only fills silence."""
    moneygram = Institution.model_validate(
        {
            "id": "moneygram",
            "display_name": "MoneyGram",
            "desk_id": "demo_desk",
            "line_label": "account security",
            "aliases": ["moneygram"],
        }
    )
    bus = InProcessBus()
    verifier = _StubVerifier()
    d = VerificationDispatcher(
        bus, directory=Directory([_INST, moneygram]), verifier=verifier, enabled=True
    )
    await d.on_turn("rf.acme.turn", _turn("moneygram mentioned early", role="UNKNOWN"))
    await d.on_turn("rf.acme.turn", _turn("amazon account security here", role="CALLER"))
    await d.on_decision("rf.acme.decision", _decision())

    assert [i.id for i in verifier.calls] == ["amazon"]
    first = (await _events(bus, "rf.acme.verification", 1))[0]
    assert first["name_source"] == "caller"


async def test_callee_is_still_never_a_source_even_with_no_other_speech() -> None:
    """The fallback must not quietly reopen the victim-echo hole."""
    bus = InProcessBus()
    verifier = _StubVerifier()
    d = _dispatcher(bus, verifier)
    await d.on_turn("rf.acme.turn", _turn("amazon account security?", role="CALLEE"))
    await d.on_turn("rf.acme.turn", _turn("hello, are you there", role="UNKNOWN"))
    await d.on_decision("rf.acme.decision", _decision())
    assert verifier.calls == []


async def test_the_unattributed_buffer_is_bounded_too() -> None:
    bus = InProcessBus()
    d = _dispatcher(bus)
    for i in range(500):
        await d.on_turn("rf.acme.turn", _turn(f"filler {i}", role="UNKNOWN", session=f"s{i}"))
    assert len(d._unattributed) <= 64


# -- Phase 2: what the verifier is given, and what it reports back ------------


def _recording(bus: InProcessBus) -> list[tuple[str, dict]]:
    published: list[tuple[str, dict]] = []
    original = bus.publish

    async def publish(subject: str, payload: dict) -> None:  # type: ignore[type-arg]
        published.append((subject, payload))
        await original(subject, payload)

    bus.publish = publish  # type: ignore[method-assign]
    return published


async def test_verifier_progress_is_published_as_transcript_stages() -> None:
    bus = InProcessBus()
    published = _recording(bus)
    d = _dispatcher(bus)
    await d.on_turn("rf.acme.turn", _turn("Amazon account security here"))
    await d.on_decision("rf.acme.decision", _decision())

    stages = [p for s, p in published if s == "rf.acme.verification"]
    assert [p["stage"] for p in stages] == ["dialing", "transcript", "result"]
    line = stages[1]
    assert line["role"] == "desk" and line["text"] == "no record"
    assert line["institution"] == "amazon"
    assert "role" not in stages[0] and "text" not in stages[2]


async def test_the_amount_comes_from_caller_speech() -> None:
    bus = InProcessBus()
    verifier = _StubVerifier()
    d = _dispatcher(bus, verifier)
    await d.on_turn("rf.acme.turn", _turn("Amazon account security, a charge of 500 dollars"))
    await d.on_decision("rf.acme.decision", _decision())
    assert verifier.amounts == ["500 dollars"]


async def test_an_amount_only_in_unattributed_speech_is_never_passed_on() -> None:
    """Omitting an amount is free; repeating the victim's guess to the
    institution as fact is not."""
    bus = InProcessBus()
    verifier = _StubVerifier()
    d = _dispatcher(bus, verifier)
    await d.on_turn("rf.acme.turn", _turn("Amazon account security", role="UNKNOWN"))
    await d.on_turn("rf.acme.turn", _turn("is it the 500 dollars?", role="UNKNOWN"))
    await d.on_decision("rf.acme.decision", _decision())
    assert [i.id for i in verifier.calls] == ["amazon"] and verifier.amounts == [None]


async def test_the_bus_loop_keeps_consuming_while_a_verification_is_in_flight() -> None:
    """A live verification lasts tens of seconds. The loop must not stall on
    it: the bus drops the oldest events for a subscriber that falls behind,
    and those would be other sessions' turns."""
    release = asyncio.Event()
    started = asyncio.Event()

    class _SlowVerifier(_StubVerifier):
        async def verify(self, **kw):  # type: ignore[no-untyped-def]  # noqa: ANN003, ANN201
            started.set()
            await release.wait()
            return await super().verify(**kw)

    bus = InProcessBus()
    d = _dispatcher(bus, _SlowVerifier())
    loop = asyncio.create_task(d.run())
    await asyncio.sleep(0)
    await bus.publish("rf.acme.turn", _turn("Amazon account security"))
    await bus.publish("rf.acme.decision", _decision())
    await asyncio.wait_for(started.wait(), 1.0)

    await bus.publish("rf.acme.turn", _turn("MoneyGram here", session="s2"))
    for _ in range(50):
        if "s2" in d._buffer:
            break
        await asyncio.sleep(0.01)
    assert "s2" in d._buffer, "a turn published mid-verification was not consumed"

    release.set()
    loop.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await loop


async def test_outcomes_and_skips_are_counted_for_metrics() -> None:
    bus = InProcessBus()
    d = _dispatcher(bus)
    await d.on_turn("rf.acme.turn", _turn("this is your electricity supplier", session="s9"))
    await d.on_decision("rf.acme.decision", _decision(session="s9"))
    await d.on_turn("rf.acme.turn", _turn("Amazon account security"))
    await d.on_decision("rf.acme.decision", _decision())
    await d.on_decision("rf.acme.decision", _decision())  # already verified
    assert d.stats.skipped == {"no_institution": 1, "already_verified": 1}
    assert d.stats.outcomes[("unconfirmed", False)] == 1
