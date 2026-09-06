from pathlib import Path

import pytest

from packages.risk import lexicons as lexicons_module
from packages.risk.lexicons import load_lexicons

TIER1 = (
    "AUTH_CLAIM",
    "RAIL_UNUSUAL",
    "VERIF_INVERT",
    "SECRECY",
    "CALLBACK_SUPPRESS",
    "REMOTE_ACCESS",
    "URGENCY",
)
PROTECTIVE = (
    "REFUSE_SECRETS",
    "OFFER_CALLBACK",
    "BRANCH_REFERRAL",
)

LANGUAGES = ("en", "fr", "ar_tn")


@pytest.mark.parametrize("lang", LANGUAGES)
@pytest.mark.parametrize("sig", TIER1 + PROTECTIVE)
def test_signal_has_min_terms(lang: str, sig: str) -> None:
    lex = load_lexicons(lang)
    assert len(lex[sig]) >= 8, f"{lang}/{sig} thin: {len(lex[sig])}"


@pytest.mark.parametrize("sig", TIER1 + PROTECTIVE)
def test_ar_tn_has_both_scripts(sig: str) -> None:
    terms = load_lexicons("ar_tn")[sig]
    is_arabic = [t for t in terms if any("\u0600" <= c <= "\u06ff" for c in t)]
    is_arabizi = [t for t in terms if not any("\u0600" <= c <= "\u06ff" for c in t)]
    assert is_arabic, f"{sig}: no Arabic-script terms"
    assert is_arabizi, f"{sig}: no Arabizi terms"


def test_rejects_unknown_signal_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = tmp_path / "en.yaml"
    bad.write_text("AUTH_CLAIM:\n  - bank\nNOT_A_SIGNAL:\n  - x\n", encoding="utf-8")
    monkeypatch.setattr(lexicons_module, "LEXICON_DIR", tmp_path)
    with pytest.raises(ValueError, match="unknown signal"):
        load_lexicons("en")


@pytest.mark.parametrize("lang", LANGUAGES)
def test_no_action_asked_is_not_lexicon_driven(lang: str) -> None:
    """NO_ACTION_ASKED is an absence over the whole call, not a phrase.

    A scammer saying "no payment needed, just verify" would earn an
    unearned -20. It is derived at scoring time (T-1.5), never matched
    lexically — so it must not appear in any lexicon file.
    """
    lex = load_lexicons(lang)
    assert "NO_ACTION_ASKED" not in lex
