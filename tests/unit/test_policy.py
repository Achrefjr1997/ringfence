import pytest
from pydantic import ValidationError

from packages.policy.pack import PolicyPack, load_pack

DEFAULT_PACK = "config/policy/default.yaml"


def test_default_pack_loads() -> None:
    p = load_pack(DEFAULT_PACK)
    assert p.thresholds.alert == 55
    assert p.thresholds.watch < p.thresholds.alert < p.thresholds.intervene
    assert p.signals["VERIF_INVERT"].weight == 30


def _pack(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "apiVersion": "ringfence/v1",
        "kind": "PolicyPack",
        "metadata": {"tenant": "t", "version": 1},
        "thresholds": {"watch": 30, "alert": 55, "intervene": 75},
        "signals": {"AUTH_CLAIM": {"weight": 12}},
        "combos": {},
        "judge": {"enabled": True, "max_adjustment": 30},
        "interventions": {"channels": ["in_ear"], "cooldown_s": 60},
    }
    base.update(overrides)
    return base


def test_rejects_unknown_signal_id() -> None:
    data = _pack(
        signals={
            "AUTH_CLAIM": {"weight": 12},
            "NOT_A_SIGNAL": {"weight": 5},
        }
    )
    with pytest.raises(ValidationError, match="unknown signal"):
        PolicyPack.model_validate(data)


def test_rejects_negative_combo_window() -> None:
    data = _pack(combos={"COMBO_CRITICAL": {"bonus": 35, "window_s": -1}})
    with pytest.raises(ValidationError, match="negative window"):
        PolicyPack.model_validate(data)


def test_rejects_bad_threshold_ordering() -> None:
    data = _pack(thresholds={"watch": 55, "alert": 55, "intervene": 75})
    with pytest.raises(ValidationError, match="watch < alert < intervene"):
        PolicyPack.model_validate(data)


def test_rejects_judge_adjustment_over_40() -> None:
    data = _pack(judge={"enabled": True, "max_adjustment": 41})
    with pytest.raises(ValidationError, match="max_adjustment"):
        PolicyPack.model_validate(data)
