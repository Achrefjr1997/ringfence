import re

import pytest

from packages.contracts.risk import Contribution
from packages.policy.pack import load_pack
from packages.risk.state import RiskStateMachine

PACK = load_pack("config/policy/default.yaml")


def _machine() -> RiskStateMachine:
    return RiskStateMachine(PACK, session_id="s1")


def test_no_escalation_on_single_spike() -> None:
    m = _machine()
    assert m.update(80.0, [], 0.0) is None
    assert m.state == "CALM"


def test_escalation_on_two_sustained_turns() -> None:
    m = _machine()
    assert m.update(60.0, [], 0.0) is None
    d = m.update(60.0, [], 1.0)
    assert d is not None and d.state == "WATCH"
    assert m.update(60.0, [], 2.0) is None
    d = m.update(60.0, [], 3.0)
    assert d is not None and d.state == "ALERT"


def test_no_flapping_when_oscillating() -> None:
    """Score oscillates around the alert threshold ±3 for 10 turns."""
    m = _machine()
    m.update(60.0, [], 0.0)
    assert m.update(60.0, [], 1.0) is not None  # CALM -> WATCH (transition 1)
    transitions = 0
    for i in range(10):
        score = 53.0 if i % 2 == 0 else 57.0
        if m.update(score, [], 2.0 + i) is not None:
            transitions += 1
    assert transitions == 0
    assert m.state == "WATCH"


def test_counterfactual_present_on_alert_plus() -> None:
    m = _machine()
    contribs = [
        Contribution(source="signal", id="AUTH_CLAIM", value=12.0),
        Contribution(source="signal", id="URGENCY", value=10.0),
        Contribution(source="combo", id="COMBO_CRITICAL", value=35.0),
    ]
    m.update(50.0, contribs, 0.0)  # CALM sustain=1
    m.update(50.0, contribs, 1.0)  # CALM sustain=2 → WATCH
    m.update(80.0, contribs, 2.0)  # WATCH sustain=1
    d = m.update(80.0, contribs, 3.0)  # WATCH sustain=2 → ALERT
    assert d is not None and d.state == "ALERT"
    assert d.counterfactual == "Without COMBO_CRITICAL the score would be 45.0 (WATCH)."
    d = m.update(80.0, contribs, 4.0)  # ALERT + combo → INTERVENE
    assert d is not None and d.state == "INTERVENE"
    assert d.counterfactual == "Without COMBO_CRITICAL the score would be 45.0 (ALERT)."


def test_combo_critical_escalates_immediately() -> None:
    m = _machine()
    m.update(60.0, [], 0.0)
    m.update(60.0, [], 1.0)  # WATCH
    m.update(60.0, [], 2.0)
    m.update(60.0, [], 3.0)  # ALERT
    combo = [Contribution(source="combo", id="COMBO_CRITICAL", value=35.0)]
    d = m.update(40.0, combo, 4.0)  # below intervene but combo fired
    assert d is not None and d.state == "INTERVENE"


def test_de_escalation_watch_to_calm_after_quiet_period() -> None:
    m = _machine()
    m.update(60.0, [], 0.0)
    m.update(60.0, [], 1.0)  # WATCH
    assert m.update(15.0, [], 100.0) is None
    assert m.update(15.0, [], 144.0) is None  # 44s quiet: still WATCH
    d = m.update(15.0, [], 146.0)  # 46s quiet: -> CALM
    assert d is not None and d.state == "CALM"


def test_quiet_period_resets_on_recovery() -> None:
    m = _machine()
    m.update(60.0, [], 0.0)
    m.update(60.0, [], 1.0)  # WATCH
    m.update(15.0, [], 100.0)  # quiet starts
    m.update(60.0, [], 110.0)  # recovers -> timer resets
    assert m.update(15.0, [], 130.0) is None  # 20s of the second quiet window
    d = m.update(15.0, [], 190.0)  # 60s since second quiet start
    assert d is not None and d.state == "CALM"


def test_de_escalation_alert_to_watch_after_quiet_period() -> None:
    m = _machine()
    m.update(60.0, [], 0.0)
    m.update(60.0, [], 1.0)  # WATCH
    m.update(60.0, [], 2.0)
    m.update(60.0, [], 3.0)  # ALERT
    assert m.update(30.0, [], 100.0) is None
    assert m.update(30.0, [], 129.0) is None  # 29s quiet: still ALERT
    d = m.update(30.0, [], 131.0)  # 31s quiet: -> WATCH
    assert d is not None and d.state == "WATCH"


def test_intervene_cooldown_returns_to_alert() -> None:
    m = _machine()
    m.update(60.0, [], 0.0)
    m.update(60.0, [], 1.0)  # WATCH
    m.update(60.0, [], 2.0)
    m.update(60.0, [], 3.0)  # ALERT
    combo = [Contribution(source="combo", id="COMBO_CRITICAL", value=35.0)]
    m.update(40.0, combo, 4.0)  # INTERVENE
    assert m.update(40.0, [], 63.0) is None  # 59s cooldown: still INTERVENE
    d = m.update(40.0, [], 65.0)  # 61s cooldown: -> ALERT
    assert d is not None and d.state == "ALERT"


def test_decision_fields() -> None:
    m = _machine()
    m.update(60.0, [], 0.0)
    d = m.update(60.0, [], 1.0)
    assert d is not None
    assert d.session_id == "s1"
    assert d.t == 1.0
    assert d.score == 60.0
    assert d.policy_pack == "default@1"
    assert re.match(r"^[0-9a-f]{32}$", d.decision_id)


def test_end_resolves() -> None:
    m = _machine()
    m.update(60.0, [], 0.0)
    m.update(60.0, [], 1.0)  # WATCH
    d = m.end(2.0)
    assert d is not None and d.state == "RESOLVED"
    assert m.end(3.0) is None  # already resolved


@pytest.mark.invariant
def test_combo_critical_requires_alert_state() -> None:
    """Invariant #7: COMBO_CRITICAL cannot jump to INTERVENE from CALM
    or WATCH.  The ALERT state gate is the safety floor — once in
    ALERT, combo alone fires immediately (even at low score); before
    ALERT, it does nothing special."""
    m = _machine()
    combo = [Contribution(source="combo", id="COMBO_CRITICAL", value=35.0)]

    # From CALM with combo: stays CALM
    assert m.update(20.0, combo, 0.0) is None
    assert m.state == "CALM"

    # Two turns ≥ watch → WATCH (transition 1)
    m.update(56.0, [], 1.0)
    assert m.update(56.0, [], 2.0) is not None
    assert m.state == "WATCH"  # type: ignore[comparison-overlap]

    # Combo present but in WATCH: no jump to INTERVENE
    d = m.update(20.0, combo, 3.0)
    assert d is None  # no transition
    assert m.state == "WATCH"  # type: ignore[comparison-overlap]

    # Only after reaching ALERT does combo matter
    m.update(60.0, [], 4.0)  # sustain=1 in WATCH
    d = m.update(60.0, [], 5.0)  # sustain=2 → ALERT (transition 2)
    assert d is not None and d.state == "ALERT"
    # Now combo alone fires INTERVENE even at score 20
    d = m.update(20.0, combo, 6.0)
    assert d is not None and d.state == "INTERVENE"  # type: ignore[comparison-overlap]
