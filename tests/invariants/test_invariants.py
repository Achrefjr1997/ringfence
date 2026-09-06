"""The invariant suite (T-1.9).

Every test here is marked ``@pytest.mark.invariant`` and runs under
``make inv`` after any change to ``packages/risk/``.  If a change breaks
one of these, the change is wrong — do not weaken the assertion.

Maps to the "Non-negotiable invariants" list in RINGFENCE_IMPLEMENTATION_PLAN.md:
  #1  callee speech never raises the score
  #2  AUTH_CLAIM + URGENCY alone never reaches ALERT
  #3  the LLM judge can never fire an intervention on its own
  #7  the COMBO_CRITICAL fast-path still requires state == ALERT immediately prior
plus the two SLO-in-miniature sweeps (all benign / all fraud fixtures).

Invariants #4 (no side effect in dry-run/replay), #5 (transcripts never
written to disk) and #6 (no unbounded queue/retry) are asserted at the
egress and ingress layers in later phases, not against the offline engine.
"""

import pytest

from packages.contracts.risk import Contribution
from packages.eval.fixtures import iter_fixtures
from packages.eval.harness import DEFAULT_PACK as pack
from packages.eval.harness import judge_forced, run_fixture
from packages.risk.state import RiskStateMachine


@pytest.mark.invariant
def test_callee_speech_never_raises_score() -> None:
    """Invariant #1. A victim repeating the scammer's words is still a victim.

    The fixture is a callee recounting a scam call: they say "gift card",
    they read a card number and an OTP aloud.  Every one of those is a
    CALLEE-attributed hit, which scores exactly 0.0.
    """
    r = run_fixture("fx_callee_repeats_terms_en_001")
    assert r.peak_score < 20, f"callee speech scored {r.peak_score}"
    assert r.peak_state == "CALM"


@pytest.mark.invariant
def test_authority_plus_urgency_alone_never_alerts() -> None:
    """Invariant #2. A real bank fraud desk claims authority and conveys
    urgency too — those two signals together must not be enough to ALERT."""
    r = run_fixture("fx_real_bank_frauddesk_fr_001")
    assert r.peak_state in {"CALM", "WATCH"}, f"reached {r.peak_state}"
    assert (
        r.peak_score < pack.thresholds.alert
    ), f"peak {r.peak_score} >= alert {pack.thresholds.alert}"


@pytest.mark.invariant
def test_judge_cannot_fire_intervention_alone() -> None:
    """Invariant #3. The judge is bounded to +/- max_adjustment; rules keep
    control.  Force it to the strongest possible fraud verdict on a benign
    call with no rule signal — it must not reach INTERVENE."""
    with judge_forced(verdict="fraud", adjustment=999):
        for fx in iter_fixtures(label="benign"):
            r = run_fixture(fx.id)
            assert r.first_intervene_t is None, f"{fx.id}: judge forced an intervention"
            assert r.final_state != "INTERVENE", f"{fx.id}: judge drove final state to INTERVENE"
            assert r.peak_state != "INTERVENE", f"{fx.id}: judge reached INTERVENE mid-call"


@pytest.mark.invariant
def test_all_benign_fixtures_stay_below_intervene() -> None:
    """The FPR SLO in miniature: no benign fixture ever intervenes."""
    offenders = [
        fx.id
        for fx in iter_fixtures(label="benign")
        if run_fixture(fx.id).peak_state == "INTERVENE"
    ]
    assert not offenders, f"benign fixtures false-positived: {offenders}"


@pytest.mark.invariant
def test_all_fraud_fixtures_alert_by_the_transfer_request() -> None:
    """Detecting after the money moved is not detecting.

    ``transfer_request_t`` is the end of the turn in which the scammer
    completes the value-transfer / credential ask; the money only moves
    once the victim then complies.  A confirm-based (2+2 hysteresis)
    detector reaches ALERT on that turn at the earliest, so the bar is
    ``first_alert_t <= transfer_request_t`` — alerting *as* the ask lands
    is still ahead of the loss.  Alerting a turn or more later is not.
    """
    failures: list[str] = []
    for fx in iter_fixtures(label="fraud"):
        assert fx.transfer_request_t is not None, f"{fx.id} has no transfer_request_t"
        r = run_fixture(fx.id)
        if r.first_alert_t is None:
            failures.append(f"{fx.id}: never alerted (peak {r.peak_state} @ {r.peak_score:.1f})")
        elif r.first_alert_t > fx.transfer_request_t:
            failures.append(
                f"{fx.id}: alerted at {r.first_alert_t:.1f}, after the transfer request at "
                f"{fx.transfer_request_t:.1f}"
            )
    assert not failures, "fraud fixtures did not alert in time:\n  " + "\n  ".join(failures)


@pytest.mark.invariant
def test_combo_critical_fastpath_requires_alert_state() -> None:
    """Invariant #7. COMBO_CRITICAL fires ALERT -> INTERVENE in one turn
    without the score reaching the intervene threshold — but it can never
    fire from CALM or WATCH.  Reaching ALERT (sustained score >= alert) is
    the scored-severity floor beneath the loudest action.
    """
    m = RiskStateMachine(pack)
    combo = [Contribution(source="combo", id="COMBO_CRITICAL", value=35.0)]

    # CALM + combo at a trivial score: no escalation.
    assert m.update(20.0, combo, 0.0) is None
    assert m.state == "CALM"

    # Climb to WATCH on two sustained turns >= watch, no combo.
    m.update(56.0, [], 1.0)
    assert m.update(56.0, [], 2.0) is not None
    assert m.state == "WATCH"  # type: ignore[comparison-overlap]

    # WATCH + combo at a trivial score: still no jump to INTERVENE.
    assert m.update(20.0, combo, 3.0) is None
    assert m.state == "WATCH"  # type: ignore[comparison-overlap]

    # Only once ALERT is reached does the combo fast-path apply.
    m.update(60.0, [], 4.0)
    d = m.update(60.0, [], 5.0)
    assert d is not None and d.state == "ALERT"
    d = m.update(20.0, combo, 6.0)
    assert d is not None and d.state == "INTERVENE"  # type: ignore[comparison-overlap]
