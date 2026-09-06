"""T-2.5 acceptance: every fixture end-to-end through NullASR + Pipeline,
landing on the same outcomes the invariant suite asserts.  Proves the
wiring; the detection logic is proven in tests/invariants and tests/unit.
"""

import pytest

from packages.asr.null import NullASR
from packages.contracts.audio import Frame, LegSpec, Mode, RoleHint, SessionDescriptor
from packages.contracts.events import InProcessBus
from packages.contracts.risk import Verdict
from packages.eval.fixtures import fixture_turns, iter_fixtures
from packages.eval.harness import DEFAULT_PACK, run_fixture
from packages.pipeline.pipeline import Pipeline, SessionResult
from packages.risk.judge import DialogueWindow
from packages.session.manager import SessionState

FIXTURES = [fx.id for fx in iter_fixtures()]


class _FixedJudge:
    """Returns a fixed adjustment on every call; records invocations."""

    def __init__(self, adjustment: int) -> None:
        self.adjustment = adjustment
        self.calls = 0

    async def evaluate(self, window: DialogueWindow, pack: object) -> Verdict:
        self.calls += 1
        return Verdict(
            verdict="fraud" if self.adjustment > 0 else "benign",
            adjustment=self.adjustment,
            signals=(),
            protective=(),
            rationale="fixed test judge",
            model_version="fake",
            latency_ms=1,
        )


async def _run(fx_id: str, *, judge: object | None = None) -> SessionResult:
    fx = next(f for f in iter_fixtures() if f.id == fx_id)
    provider = NullASR(fixture_turns(fx), speed=500.0)
    pipe = Pipeline(provider, pack=DEFAULT_PACK, bus=InProcessBus(), judge=judge)  # type: ignore[arg-type]
    desc = SessionDescriptor(
        session_id=fx.id,
        tenant_id="acme",
        mode=Mode.REPLAY,
        legs=(
            LegSpec("far", RoleHint.CALLER, 16000),
            LegSpec("near", RoleHint.CALLEE, 16000),
        ),
        started_at=0.0,
        language=fx.language,
    )
    await pipe.start(desc)
    await pipe.feed(Frame(fx.id, "far", b"\x00\x00" * 320, 16000, 0, 0.0))
    return await pipe.end(fx.id)


@pytest.mark.parametrize("fx_id", FIXTURES)
async def test_pipeline_matches_offline_harness(fx_id: str) -> None:
    r = await _run(fx_id)
    h = run_fixture(fx_id)
    assert r.turns_seen == len(next(f for f in iter_fixtures() if f.id == fx_id).turns)
    assert r.peak_state == h.peak_state
    assert r.final_state == h.final_state
    assert r.first_alert_t == h.first_alert_t
    assert r.first_intervene_t == h.first_intervene_t
    assert r.peak_score == pytest.approx(h.peak_score)
    assert r.session_state is SessionState.CLOSED


@pytest.mark.parametrize("fx_id", [fx.id for fx in iter_fixtures(label="benign")])
async def test_benign_fixtures_never_intervene_through_pipeline(fx_id: str) -> None:
    r = await _run(fx_id)
    assert r.peak_state != "INTERVENE"
    assert r.first_intervene_t is None


@pytest.mark.parametrize("fx_id", [fx.id for fx in iter_fixtures(label="fraud")])
async def test_fraud_fixtures_alert_by_transfer_request_through_pipeline(fx_id: str) -> None:
    fx = next(f for f in iter_fixtures() if f.id == fx_id)
    r = await _run(fx_id)
    assert r.first_alert_t is not None
    assert r.first_alert_t <= fx.transfer_request_t


async def test_callee_speech_never_raises_score_through_pipeline() -> None:
    r = await _run("fx_callee_repeats_terms_en_001")
    assert r.peak_score < 20
    assert r.peak_state == "CALM"


async def test_judge_is_invoked_on_a_fraud_call() -> None:
    judge = _FixedJudge(adjustment=0)
    await _run("fx_tech_support_en_001", judge=judge)
    assert judge.calls >= 1


async def test_judge_cannot_push_a_benign_call_to_intervene_through_pipeline() -> None:
    # Invariant #3, exercised on the streaming path: a judge screaming
    # +max_adjustment on every turn still cannot fire an intervention.
    judge = _FixedJudge(adjustment=DEFAULT_PACK.judge.max_adjustment)
    for fx in iter_fixtures(label="benign"):
        r = await _run(fx.id, judge=judge)
        assert r.first_intervene_t is None, fx.id
        assert r.peak_state != "INTERVENE", fx.id


async def test_decisions_are_published_on_the_bus() -> None:
    fx = next(f for f in iter_fixtures() if f.id == "fx_tech_support_en_001")
    provider = NullASR(fixture_turns(fx), speed=500.0)
    bus = InProcessBus()
    pipe = Pipeline(provider, pack=DEFAULT_PACK, bus=bus)

    got: list[tuple[str, dict]] = []

    async def collect() -> None:
        async for subject, payload in bus.subscribe("rf.acme.decision"):
            got.append((subject, payload))

    import asyncio

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)

    desc = SessionDescriptor(
        session_id=fx.id,
        tenant_id="acme",
        mode=Mode.REPLAY,
        legs=(LegSpec("far", RoleHint.CALLER, 16000), LegSpec("near", RoleHint.CALLEE, 16000)),
        started_at=0.0,
        language="en",
    )
    await pipe.start(desc)
    await pipe.feed(Frame(fx.id, "far", b"\x00\x00" * 320, 16000, 0, 0.0))
    result = await pipe.end(fx.id)
    await asyncio.sleep(0.05)
    task.cancel()

    assert len(got) == len(result.decisions) >= 1
    assert {p["state"] for _, p in got} <= {"WATCH", "ALERT", "INTERVENE"}
    assert any(p["state"] == "ALERT" for _, p in got)
