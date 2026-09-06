import pytest

from packages.intervene.templates import LANGUAGES, load_templates, select_template
from packages.policy.pack import KNOWN_SIGNAL_IDS


def test_every_signal_has_a_template_in_every_language() -> None:
    t = load_templates()
    for sid in KNOWN_SIGNAL_IDS:
        assert sid in t, f"no template for {sid}"
        for lang in LANGUAGES:
            assert t[sid].get(lang, "").strip(), f"{sid}/{lang} empty"
    assert set(t["DEFAULT"]) >= set(LANGUAGES)


@pytest.mark.parametrize("lang", LANGUAGES)
def test_select_template_by_signal(lang: str) -> None:
    tid, text = select_template("RAIL_UNUSUAL", lang)
    assert tid == "RAIL_UNUSUAL"
    assert text and text == load_templates()["RAIL_UNUSUAL"][lang]


def test_select_template_falls_back_to_default() -> None:
    tid, text = select_template(None, "en")
    assert tid == "DEFAULT"
    tid, _ = select_template("NOT_A_SIGNAL", "fr")
    assert tid == "DEFAULT"


def test_select_template_falls_back_to_english_for_unknown_language() -> None:
    _, text = select_template("VERIF_INVERT", "de")
    assert text == load_templates()["VERIF_INVERT"]["en"]
