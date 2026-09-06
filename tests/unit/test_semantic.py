"""Zero-shot NLI extractor (T-1.7), exercised with a fake classifier so the
suite needs neither transformers nor torch.  The live model is covered by
tests/integration/test_semantic_model.py (``-m needs_model``)."""

import pytest

from packages.risk.hypotheses import load_hypotheses
from packages.risk.semantic import SemanticExtractor, ZeroShotClassifier
from tests.helpers import callee_turn, caller_turn

WEIGHTS = {"AUTH_CLAIM": 12.0, "VERIF_INVERT": 30.0, "RAIL_UNUSUAL": 25.0, "URGENCY": 10.0}


class FakeClassifier:
    """Returns a fixed probability for any hypothesis containing one of the
    keywords, near-zero otherwise.  Records every call."""

    def __init__(self, rules: dict[str, float]) -> None:
        self.rules = rules
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def score(self, premise: str, hypotheses: list[str]) -> list[float]:
        self.calls.append((premise, tuple(hypotheses)))
        out = []
        for h in hypotheses:
            p = 0.02
            for needle, prob in self.rules.items():
                if needle in h:
                    p = max(p, prob)
            out.append(p)
        return out


HYP = {
    "AUTH_CLAIM": ["the speaker claims to be from a bank"],
    "VERIF_INVERT": ["the speaker asks for a one-time code", "the speaker asks for a card number"],
    "RAIL_UNUSUAL": ["the speaker asks for gift cards"],
}


def _ex(
    clf: ZeroShotClassifier, *, threshold: float = 0.7, scale: bool = True
) -> SemanticExtractor:
    return SemanticExtractor(HYP, WEIGHTS, clf, threshold=threshold, scale_weight=scale)


def test_fires_signal_above_threshold() -> None:
    clf = FakeClassifier({"bank": 0.95})
    hits = _ex(clf).extract(caller_turn("hello, I work for your bank's fraud team"))
    assert [h.signal_id for h in hits] == ["AUTH_CLAIM"]
    assert hits[0].evidence == "the speaker claims to be from a bank"
    assert hits[0].extractor == "semantic@1.0"


def test_does_not_fire_below_threshold() -> None:
    clf = FakeClassifier({"bank": 0.55})
    assert _ex(clf, threshold=0.7).extract(caller_turn("anything")) == []


def test_weight_is_scaled_by_probability() -> None:
    clf = FakeClassifier({"bank": 0.8})
    hit = _ex(clf, scale=True).extract(caller_turn("x"))[0]
    assert hit.weight == pytest.approx(12.0 * 0.8)

    clf2 = FakeClassifier({"bank": 0.8})
    hit2 = _ex(clf2, scale=False).extract(caller_turn("x"))[0]
    assert hit2.weight == 12.0


def test_takes_max_hypothesis_per_signal() -> None:
    # VERIF_INVERT has two hypotheses; the "card number" one wins.
    clf = FakeClassifier({"one-time code": 0.72, "card number": 0.91})
    hit = next(h for h in _ex(clf).extract(caller_turn("x")) if h.signal_id == "VERIF_INVERT")
    assert hit.evidence == "the speaker asks for a card number"
    assert hit.weight == pytest.approx(30.0 * 0.91)


def test_one_classifier_call_per_turn_covering_all_hypotheses() -> None:
    clf = FakeClassifier({})
    _ex(clf).extract(caller_turn("x"))
    assert len(clf.calls) == 1
    assert len(clf.calls[0][1]) == 4  # 1 + 2 + 1 flattened hypotheses


def test_role_is_passed_through() -> None:
    clf = FakeClassifier({"bank": 0.95})
    hit = _ex(clf).extract(callee_turn("I told him he was from the bank"))[0]
    assert hit.role == "CALLEE"  # scoring zeroes CALLEE downstream, same as lexical


def test_empty_turn_makes_no_call() -> None:
    clf = FakeClassifier({"bank": 0.95})
    assert _ex(clf).extract(caller_turn("   ")) == []
    assert clf.calls == []


def test_bad_threshold_rejected() -> None:
    with pytest.raises(ValueError):
        SemanticExtractor(HYP, WEIGHTS, FakeClassifier({}), threshold=0.0)
    with pytest.raises(ValueError):
        SemanticExtractor(HYP, WEIGHTS, FakeClassifier({}), threshold=1.5)


def test_classifier_length_mismatch_raises() -> None:
    class Broken:
        def score(self, premise: str, hypotheses: list[str]) -> list[float]:
            return [0.9]  # wrong length

    with pytest.raises(ValueError):
        _ex(Broken()).extract(caller_turn("x"))


def test_hypotheses_load_for_en_and_fr_and_reject_unknown_signal(tmp_path) -> None:
    for lang in ("en", "fr"):
        hyp = load_hypotheses(lang)
        assert "AUTH_CLAIM" in hyp and all(isinstance(v, list) and v for v in hyp.values())

    bad = tmp_path / "xx.yaml"
    bad.write_text("NOT_A_SIGNAL:\n  - 'x'\n", encoding="utf-8")
    import packages.risk.hypotheses as mod

    orig = mod.HYPOTHESIS_DIR
    mod.HYPOTHESIS_DIR = tmp_path
    try:
        with pytest.raises(ValueError):
            load_hypotheses("xx")
    finally:
        mod.HYPOTHESIS_DIR = orig
