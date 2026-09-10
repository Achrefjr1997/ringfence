"""ESCALATION — repetition credit, with a hard ceiling.

The pack has declared ESCALATION at +10 since T-1.1 and nothing has ever
emitted it, so ``EvidenceWindow`` dedups on ``(signal_id, role)`` and a
scammer who asks ten times scores exactly what one ask scores.  That is the
gap this closes, and it matters because repetition is the one piece of
evidence that gets *stronger* when the caller rewords: five differently
phrased asks raise the count where they would dodge a lexicon match.

The three properties below are what keep it safe to add.
"""

from __future__ import annotations

import math

from packages.contracts.risk import SignalHit
from packages.policy.pack import load_pack
from packages.risk.derived import _MIN_REPEATS, _SATURATE_AT, evaluate_escalation
from packages.risk.scoring import EvidenceWindow

PACK = load_pack("config/policy/default.yaml")
_CEILING = PACK.signals["ESCALATION"].weight


def _hit(signal_id: str, t: float, role: str = "CALLER") -> SignalHit:
    spec = PACK.signals[signal_id]
    return SignalHit(
        signal_id=signal_id,
        weight=float(spec.weight),
        role=role,  # type: ignore[arg-type]
        t=t,
        evidence=f"{signal_id}@{t}",
        evidence_span=(t, t + 1.0),
        extractor="test@1.0",
    )


def _window(*hits: SignalHit) -> EvidenceWindow:
    w = EvidenceWindow()
    for h in hits:
        w.add(h)
    return w


def _value(w: EvidenceWindow, now: float) -> float:
    out = evaluate_escalation(w, now, PACK)
    return out[0].value if out else 0.0


# -- it fires at all -------------------------------------------------


def test_a_single_hit_is_not_escalation() -> None:
    assert evaluate_escalation(_window(_hit("URGENCY", 1.0)), 10.0, PACK) == []


def test_a_repeated_ask_scores() -> None:
    w = _window(*[_hit("URGENCY", float(i)) for i in range(_MIN_REPEATS)])
    assert _value(w, 10.0) > 0.0


def test_one_short_of_the_minimum_does_not_score() -> None:
    w = _window(*[_hit("URGENCY", float(i)) for i in range(_MIN_REPEATS - 1)])
    assert evaluate_escalation(w, 10.0, PACK) == []


def test_repetition_is_what_the_evidence_window_still_hides() -> None:
    """The premise: without this, ten asks look exactly like one."""
    once = _window(_hit("URGENCY", 1.0))
    ten = _window(*[_hit("URGENCY", float(i)) for i in range(10)])
    assert len(once.active(20.0)) == len(ten.active(20.0)) == 1  # dedup, unchanged
    assert _value(once, 20.0) == 0.0
    assert _value(ten, 20.0) > 0.0


# -- property 1: CALLER-only (invariant #1 must stay untouchable) ----


def test_callee_repetition_never_escalates() -> None:
    w = _window(*[_hit("VERIF_INVERT", float(i), role="CALLEE") for i in range(10)])
    assert evaluate_escalation(w, 20.0, PACK) == []


def test_unknown_role_repetition_never_escalates() -> None:
    w = _window(*[_hit("VERIF_INVERT", float(i), role="UNKNOWN") for i in range(10)])
    assert evaluate_escalation(w, 20.0, PACK) == []


def test_protective_signal_repetition_never_escalates() -> None:
    """Refusing repeatedly is not escalating."""
    assert PACK.signals["REFUSE_SECRETS"].weight < 0
    w = _window(*[_hit("REFUSE_SECRETS", float(i)) for i in range(10)])
    assert evaluate_escalation(w, 20.0, PACK) == []


# -- property 2: one contribution, hard-capped ------------------------


def test_many_repeated_signals_still_yield_one_contribution() -> None:
    w = _window(
        *[_hit("URGENCY", float(i)) for i in range(6)],
        *[_hit("VERIF_INVERT", float(i)) for i in range(6)],
        *[_hit("RAIL_UNUSUAL", float(i)) for i in range(6)],
    )
    out = evaluate_escalation(w, 20.0, PACK)
    assert len(out) == 1
    assert out[0].id == "ESCALATION"


def test_value_never_exceeds_the_pack_weight() -> None:
    for n in (2, 4, 8, 50, 500):
        w = _window(*[_hit("URGENCY", float(i) * 0.1) for i in range(n)])
        assert _value(w, 60.0) <= _CEILING + 1e-9, n


# -- property 3: saturating ------------------------------------------


def test_saturates_at_the_configured_repeat_count() -> None:
    at_cap = _window(*[_hit("URGENCY", float(i)) for i in range(_SATURATE_AT)])
    way_over = _window(*[_hit("URGENCY", float(i) * 0.1) for i in range(_SATURATE_AT * 12)])
    assert math.isclose(_value(at_cap, 60.0), _CEILING)
    assert math.isclose(_value(way_over, 60.0), _CEILING)


def test_the_minimum_is_worth_less_than_saturation() -> None:
    assert _MIN_REPEATS < _SATURATE_AT, "otherwise the ramp has no room"
    few = _value(_window(*[_hit("URGENCY", float(i)) for i in range(_MIN_REPEATS)]), 30.0)
    many = _value(_window(*[_hit("URGENCY", float(i)) for i in range(_SATURATE_AT)]), 30.0)
    assert 0.0 < few < many


# -- window semantics -------------------------------------------------


def test_occurrences_age_out_with_the_window() -> None:
    """Repeats outside span_s stop counting, like every other hit."""
    w = EvidenceWindow()
    for i in range(6):
        w.add(_hit("URGENCY", float(i)))
    assert _value(w, 10.0) > 0.0
    w.add(_hit("URGENCY", 10_000.0))  # far future: prunes the old cluster
    assert _value(w, 10_000.0) == 0.0  # one recent hit left, not a repeat


def test_hits_dedup_behaviour_is_unchanged() -> None:
    """We must not disturb decay or the combo call sites."""
    w = _window(_hit("URGENCY", 1.0), _hit("URGENCY", 9.0))
    assert len(w.hits) == 1
    assert w.hits[("URGENCY", "CALLER")].t == 9.0  # still most-recent-wins
    assert w.has("URGENCY", within_s=5.0, now=10.0) is True
