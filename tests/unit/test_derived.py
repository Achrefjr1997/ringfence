"""T-1.5b — the NO_ACTION_ASKED call-level deriver."""

import pytest

from packages.contracts.risk import SignalHit
from packages.policy.pack import PolicyPack, load_pack
from packages.risk.derived import evaluate_derived
from packages.risk.scoring import EvidenceWindow

PACK = load_pack("config/policy/default.yaml")
WEIGHT = PACK.signals["NO_ACTION_ASKED"].weight  # -20


def _hit(sig: str, role: str, t: float) -> SignalHit:
    return SignalHit(
        signal_id=sig,
        weight=10.0,
        role=role,  # type: ignore[arg-type]
        t=t,
        evidence="e",
        evidence_span=(t, t + 1.0),
        extractor="test@1.0",
    )


def test_does_not_fire_before_the_elapsed_threshold() -> None:
    w = EvidenceWindow()
    w.add(_hit("AUTH_CLAIM", "CALLER", 5.0))
    assert evaluate_derived(w, 29.9, PACK) == []


def test_fires_once_a_quiet_call_has_run_long_enough() -> None:
    w = EvidenceWindow()
    w.add(_hit("AUTH_CLAIM", "CALLER", 5.0))
    w.add(_hit("URGENCY", "CALLER", 15.0))
    out = evaluate_derived(w, 32.0, PACK)
    assert [c.id for c in out] == ["NO_ACTION_ASKED"]
    assert out[0].value == WEIGHT and out[0].source == "enrichment"


def test_a_long_call_with_no_signals_at_all_still_fires() -> None:
    assert len(evaluate_derived(EvidenceWindow(), 40.0, PACK)) == 1


@pytest.mark.parametrize("sig", ["RAIL_UNUSUAL", "VERIF_INVERT", "REMOTE_ACCESS"])
def test_any_caller_transfer_ask_suppresses_it(sig: str) -> None:
    w = EvidenceWindow()
    w.add(_hit(sig, "CALLER", 24.0))
    assert evaluate_derived(w, 40.0, PACK) == []


def test_a_callee_reading_a_code_aloud_does_not_suppress_it() -> None:
    w = EvidenceWindow()
    w.add(_hit("VERIF_INVERT", "CALLEE", 24.0))  # the victim, not the caller
    assert len(evaluate_derived(w, 40.0, PACK)) == 1


def test_absent_from_the_pack_yields_nothing() -> None:
    data = PACK.model_dump()
    del data["signals"]["NO_ACTION_ASKED"]
    p = PolicyPack.model_validate(data)
    assert evaluate_derived(EvidenceWindow(), 100.0, p) == []


def test_folds_into_the_score_as_an_extra() -> None:
    from packages.risk.scoring import score_window

    w = EvidenceWindow()
    w.add(_hit("AUTH_CLAIM", "CALLER", 5.0))
    base, _ = score_window(w, 32.0, PACK)
    extras = tuple(evaluate_derived(w, 32.0, PACK))
    lowered, contribs = score_window(w, 32.0, PACK, extras=extras)
    assert lowered < base
    assert any(c.id == "NO_ACTION_ASKED" and c.value == WEIGHT for c in contribs)
