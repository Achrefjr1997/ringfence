from packages.contracts.risk import SignalHit
from packages.policy.pack import PolicyPack, load_pack
from packages.risk.combos import evaluate_combos
from packages.risk.scoring import EvidenceWindow

PACK = load_pack("config/policy/default.yaml")


def _hit(signal_id: str, role: str, t: float, weight: float = 10.0) -> SignalHit:
    return SignalHit(
        signal_id=signal_id,
        weight=weight,
        role=role,  # type: ignore[arg-type]
        t=t,
        evidence="ev",
        evidence_span=(t, t + 1.0),
        extractor="test@1.0",
    )


def test_combo_critical_fires() -> None:
    w = EvidenceWindow()
    w.add(_hit("AUTH_CLAIM", "CALLER", 100.0, 12.0))
    w.add(_hit("VERIF_INVERT", "CALLER", 150.0, 30.0))
    bonuses = evaluate_combos(w, 180.0, PACK)
    assert any(c.id == "COMBO_CRITICAL" for c in bonuses)
    assert sum(c.value for c in bonuses) == PACK.combos["COMBO_CRITICAL"].bonus


def test_combo_critical_with_alternate_signals() -> None:
    w = EvidenceWindow()
    w.add(_hit("AUTH_CLAIM", "CALLER", 100.0, 12.0))
    w.add(_hit("RAIL_UNUSUAL", "CALLER", 160.0, 25.0))
    bonuses = evaluate_combos(w, 180.0, PACK)
    assert any(c.id == "COMBO_CRITICAL" for c in bonuses)


def test_combo_isolation_fires() -> None:
    w = EvidenceWindow()
    w.add(_hit("SECRECY", "CALLER", 130.0, 20.0))
    w.add(_hit("URGENCY", "CALLER", 140.0, 10.0))
    bonuses = evaluate_combos(w, 180.0, PACK)
    assert any(c.id == "COMBO_ISOLATION" for c in bonuses)
    assert any(c.value == PACK.combos["COMBO_ISOLATION"].bonus for c in bonuses)


def test_combo_classic_fires() -> None:
    w = EvidenceWindow()
    w.add(_hit("AUTH_CLAIM", "CALLER", 70.0, 12.0))
    w.add(_hit("URGENCY", "CALLER", 80.0, 10.0))
    w.add(_hit("RAIL_UNUSUAL", "CALLER", 90.0, 25.0))
    bonuses = evaluate_combos(w, 180.0, PACK)
    assert any(c.id == "COMBO_CLASSIC" for c in bonuses)
    assert any(c.value == PACK.combos["COMBO_CLASSIC"].bonus for c in bonuses)


def test_combo_does_not_fire_when_signals_came_from_callee() -> None:
    """The plan's explicit acceptance: combos are CALLER-attributed only."""
    w = EvidenceWindow()
    w.add(_hit("AUTH_CLAIM", "CALLEE", 100.0, 12.0))
    w.add(_hit("VERIF_INVERT", "CALLEE", 150.0, 30.0))
    bonuses = evaluate_combos(w, 180.0, PACK)
    assert not bonuses


def test_combo_does_not_fire_when_signal_stale() -> None:
    """has() checks each signal's own recency: AUTH at t=0 is 180s stale
    at now=180, independently failing the 90s window regardless of the
    other signal's timing."""
    w = EvidenceWindow()
    w.add(_hit("AUTH_CLAIM", "CALLER", 0.0, 12.0))
    w.add(_hit("VERIF_INVERT", "CALLER", 100.0, 30.0))
    bonuses = evaluate_combos(w, 180.0, PACK)
    assert not bonuses


def test_combo_stacking() -> None:
    """CRITICAL and CLASSIC both fire when AUTH+VERIF+URGENCY+RAIL overlap."""
    w = EvidenceWindow()
    w.add(_hit("AUTH_CLAIM", "CALLER", 100.0, 12.0))
    w.add(_hit("VERIF_INVERT", "CALLER", 110.0, 30.0))
    w.add(_hit("URGENCY", "CALLER", 120.0, 10.0))
    w.add(_hit("RAIL_UNUSUAL", "CALLER", 130.0, 25.0))
    bonuses = evaluate_combos(w, 180.0, PACK)
    assert any(c.id == "COMBO_CRITICAL" for c in bonuses)
    assert any(c.id == "COMBO_CLASSIC" for c in bonuses)
    expected = PACK.combos["COMBO_CRITICAL"].bonus + PACK.combos["COMBO_CLASSIC"].bonus
    assert sum(c.value for c in bonuses) == expected


def test_empty_window_no_combos() -> None:
    assert evaluate_combos(EvidenceWindow(), 100.0, PACK) == []


def test_combo_window_is_pack_driven() -> None:
    """A pack with a different COMBO_CRITICAL.window_s changes the outcome —
    proves values come from the pack, not coincidentally-matching literals."""
    w = EvidenceWindow()
    w.add(_hit("AUTH_CLAIM", "CALLER", 100.0, 12.0))
    w.add(_hit("VERIF_INVERT", "CALLER", 150.0, 30.0))

    data = PACK.model_dump()
    data["combos"]["COMBO_CRITICAL"]["window_s"] = 30.0
    tight_pack = PolicyPack.model_validate(data)

    assert evaluate_combos(w, 180.0, PACK)  # default 90s window: fires
    assert evaluate_combos(w, 180.0, tight_pack) == []  # 30s window: AUTH stale
