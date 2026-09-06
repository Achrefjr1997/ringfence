"""Live zero-shot model check.  Runs only with the 'semantic' extra:

    pip install -e ".[semantic]"
    pytest tests/integration/test_semantic_model.py -q -m needs_model

Skips cleanly when transformers/torch are absent.
"""

import importlib.util

import pytest

from packages.risk.hypotheses import load_hypotheses
from packages.risk.semantic import SemanticExtractor
from tests.helpers import caller_turn

pytestmark = pytest.mark.needs_model

_HAVE = importlib.util.find_spec("transformers") and importlib.util.find_spec("torch")

WEIGHTS = {
    "AUTH_CLAIM": 12.0,
    "URGENCY": 10.0,
    "VERIF_INVERT": 30.0,
    "RAIL_UNUSUAL": 25.0,
    "REMOTE_ACCESS": 25.0,
    "SECRECY": 20.0,
    "CALLBACK_SUPPRESS": 18.0,
    "EMOTION_LEVER": 12.0,
    "REFUSE_SECRETS": -30.0,
    "OFFER_CALLBACK": -25.0,
    "BRANCH_REFERRAL": -15.0,
}


@pytest.fixture(scope="module")
def extractor_en() -> SemanticExtractor:
    if not _HAVE:
        pytest.skip("semantic extra not installed")
    from packages.risk.hf_zeroshot import HFZeroShotClassifier

    return SemanticExtractor(load_hypotheses("en"), WEIGHTS, HFZeroShotClassifier(), threshold=0.7)


@pytest.fixture(scope="module")
def extractor_fr() -> SemanticExtractor:
    if not _HAVE:
        pytest.skip("semantic extra not installed")
    from packages.risk.hf_zeroshot import HFZeroShotClassifier

    return SemanticExtractor(load_hypotheses("fr"), WEIGHTS, HFZeroShotClassifier(), threshold=0.7)


def _fired(extractor: SemanticExtractor, text: str) -> set[str]:
    return {h.signal_id for h in extractor.extract(caller_turn(text))}


def test_english_paraphrases_the_lexicon_would_miss(extractor_en: SemanticExtractor) -> None:
    # Zero-shot NLI is not exact; require the majority of these clear cases,
    # none of which appear in en.yaml verbatim.
    got = [
        "VERIF_INVERT"
        in _fired(extractor_en, "Just tell me the six digits we texted you to confirm it's you."),
        "RAIL_UNUSUAL"
        in _fired(extractor_en, "Go to the shop and load 500 dollars onto an iTunes voucher."),
        "AUTH_CLAIM" in _fired(extractor_en, "Hi, this is Amazon fraud prevention calling."),
    ]
    assert sum(got) >= 2, got


def test_french_paraphrases(extractor_fr: SemanticExtractor) -> None:
    assert "VERIF_INVERT" in _fired(
        extractor_fr, "Lisez-le moi, le code que vous venez de recevoir par SMS."
    )


def test_benign_small_talk_fires_nothing(extractor_en: SemanticExtractor) -> None:
    assert (
        _fired(extractor_en, "Your parcel will arrive between three and five, no signature needed.")
        == set()
    )


def test_live_model_keeps_fraud_fixtures_alerting() -> None:
    if not _HAVE:
        pytest.skip("semantic extra not installed")
    from packages.eval.fixtures import iter_fixtures
    from packages.eval.harness import run_fixture
    from packages.risk.hf_zeroshot import HFZeroShotClassifier

    clf = HFZeroShotClassifier()
    for fx in iter_fixtures(label="fraud"):
        if fx.language not in ("en", "fr"):
            continue
        r = run_fixture(fx.id, semantic=clf)
        assert r.first_alert_t is not None and r.first_alert_t <= fx.transfer_request_t, fx.id


@pytest.mark.xfail(
    reason="Zero-shot hypotheses/threshold not yet tuned: mDeBERTa spuriously entails "
    "REMOTE_ACCESS/URGENCY on legit French bank calls, and AUTH_CLAIM is too broad "
    "(a real bank rep also 'claims to be from the bank'). Tighten hypotheses to "
    "fraud-pretext framing, raise threshold, exclude protective signals — then flip "
    "pack.semantic.enabled. Tracked for T-5.x tuning.",
    strict=True,
)
def test_live_model_holds_the_fpr_bar_on_benign_fixtures() -> None:
    if not _HAVE:
        pytest.skip("semantic extra not installed")
    from packages.eval.fixtures import iter_fixtures
    from packages.eval.harness import run_fixture
    from packages.risk.hf_zeroshot import HFZeroShotClassifier

    clf = HFZeroShotClassifier()
    for fx in iter_fixtures(label="benign"):
        if fx.language not in ("en", "fr"):
            continue
        r = run_fixture(fx.id, semantic=clf)
        assert r.peak_state != "INTERVENE", fx.id
