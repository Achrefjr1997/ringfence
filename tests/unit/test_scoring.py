import pytest

from packages.contracts.risk import Contribution, SignalHit
from packages.policy.pack import load_pack
from packages.risk.scoring import EvidenceWindow, score_window

PACK = load_pack("config/policy/default.yaml")
HALF = PACK.thresholds.decay_half_life_s


def _hit(signal_id: str, role: str, t: float, weight: float) -> SignalHit:
    return SignalHit(
        signal_id=signal_id,
        weight=weight,
        role=role,  # type: ignore[arg-type]
        t=t,
        evidence="ev",
        evidence_span=(t, t + 1.0),
        extractor="test@1.0",
    )


def test_dedup_keeps_most_recent_for_decay() -> None:
    """Same (signal_id, role) twice: one contribution, decay from the latest t."""
    w = EvidenceWindow()
    w.add(_hit("VERIF_INVERT", "CALLER", 20.0, 30.0))
    w.add(_hit("VERIF_INVERT", "CALLER", 140.0, 30.0))
    score, contribs = score_window(w, 180.0, PACK)
    assert len(contribs) == 1
    assert contribs[0].t == 140.0
    expected = 30.0 * 2 ** (-(180.0 - 140.0) / HALF)
    assert score == pytest.approx(expected)


def test_decay_floor_at_0_4() -> None:
    w = EvidenceWindow(span_s=100000.0)
    w.add(_hit("AUTH_CLAIM", "CALLER", 0.0, 12.0))
    score, contribs = score_window(w, 5000.0, PACK)
    assert score == pytest.approx(12.0 * 0.4)
    assert contribs[0].value == pytest.approx(12.0 * 0.4)


def test_callee_contributes_exactly_zero() -> None:
    w = EvidenceWindow()
    w.add(_hit("RAIL_UNUSUAL", "CALLEE", 50.0, 25.0))
    score, contribs = score_window(w, 100.0, PACK)
    assert score == 0.0
    assert contribs[0].value == 0.0


def test_unknown_half_weight() -> None:
    w = EvidenceWindow()
    w.add(_hit("SECRECY", "UNKNOWN", 50.0, 20.0))
    score, _ = score_window(w, 100.0, PACK)
    assert score == pytest.approx(20.0 * 0.5 * 2 ** (-50.0 / HALF))


def test_clamps_at_100() -> None:
    w = EvidenceWindow()
    for sig, wt in (
        ("VERIF_INVERT", 30.0),
        ("RAIL_UNUSUAL", 25.0),
        ("REMOTE_ACCESS", 25.0),
        ("SECRECY", 20.0),
        ("CALLBACK_SUPPRESS", 18.0),
        ("EMOTION_LEVER", 12.0),
    ):
        w.add(_hit(sig, "CALLER", 90.0, wt))
    score, _ = score_window(w, 100.0, PACK)
    assert score == 100.0


def test_clamps_at_0() -> None:
    w = EvidenceWindow()
    w.add(_hit("REFUSE_SECRETS", "CALLER", 10.0, -30.0))
    score, _ = score_window(w, 100.0, PACK)
    assert score == 0.0


def test_contributions_sum_to_score() -> None:
    w = EvidenceWindow()
    w.add(_hit("AUTH_CLAIM", "CALLER", 10.0, 12.0))
    w.add(_hit("URGENCY", "CALLER", 20.0, 10.0))
    score, contribs = score_window(w, 100.0, PACK)
    assert sum(c.value for c in contribs) == pytest.approx(score)


def test_extras_fold_into_score() -> None:
    w = EvidenceWindow()
    w.add(_hit("AUTH_CLAIM", "CALLER", 10.0, 12.0))
    extra = Contribution(source="combo", id="COMBO_CRITICAL", value=35.0)
    score, contribs = score_window(w, 100.0, PACK, extras=(extra,))
    base = 12.0 * 2 ** (-90.0 / HALF)
    assert score == pytest.approx(base + 35.0)
    assert contribs[-1] is extra


def test_has_respects_role_and_window() -> None:
    w = EvidenceWindow()
    w.add(_hit("SECRECY", "CALLER", 50.0, 20.0))
    assert w.has("SECRECY", 60.0, 100.0, role="CALLER")
    assert not w.has("SECRECY", 60.0, 100.0, role="UNKNOWN")
    assert not w.has("SECRECY", 10.0, 100.0, role="CALLER")


def test_active_prunes_outside_span() -> None:
    w = EvidenceWindow(span_s=180.0)
    w.add(_hit("AUTH_CLAIM", "CALLER", 0.0, 12.0))
    w.add(_hit("URGENCY", "CALLER", 100.0, 10.0))
    active = w.active(200.0)
    assert [h.signal_id for h in active] == ["URGENCY"]
