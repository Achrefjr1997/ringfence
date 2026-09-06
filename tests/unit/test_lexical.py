import pytest

from packages.contracts.transcript import Word
from packages.risk.lexical import LexicalExtractor
from tests.helpers import callee_turn, caller_turn

EN = {
    "AUTH_CLAIM": ["bank", "police"],
    "RAIL_UNUSUAL": ["gift card", "bitcoin"],
    "VERIF_INVERT": ["card number", "code"],
    "URGENCY": ["immediately", "right now"],
}
WEIGHTS = {
    "AUTH_CLAIM": 12.0,
    "RAIL_UNUSUAL": 25.0,
    "VERIF_INVERT": 30.0,
    "URGENCY": 10.0,
}


def test_exact_match() -> None:
    ex = LexicalExtractor(EN, WEIGHTS)
    hits = ex.extract(caller_turn("This is your bank calling"))
    assert [h.signal_id for h in hits] == ["AUTH_CLAIM"]
    assert hits[0].evidence == "bank"
    assert hits[0].extractor == "lexical@1.0"


def test_case_insensitive() -> None:
    ex = LexicalExtractor(EN, WEIGHTS)
    hits = ex.extract(caller_turn("THIS IS YOUR BANK CALLING"))
    assert [h.signal_id for h in hits] == ["AUTH_CLAIM"]
    assert hits[0].evidence == "BANK"


def test_arabic_script() -> None:
    ex = LexicalExtractor({"RAIL_UNUSUAL": ["فلوسي", "د17"]}, WEIGHTS)
    hits = ex.extract(caller_turn("تحويل عبر فلوسي توا"))
    assert [h.signal_id for h in hits] == ["RAIL_UNUSUAL"]
    assert hits[0].evidence == "فلوسي"


def test_arabizi() -> None:
    ex = LexicalExtractor({"RAIL_UNUSUAL": ["kart ta3bia", "flouci"]}, WEIGHTS)
    hits = ex.extract(caller_turn("3tini kart ta3bia mte3ek"))
    assert [h.signal_id for h in hits] == ["RAIL_UNUSUAL"]
    assert hits[0].evidence == "kart ta3bia"


def test_arabic_diacritics_stripped() -> None:
    ex = LexicalExtractor({"RAIL_UNUSUAL": ["كارت تعبئة"]}, WEIGHTS)
    hits = ex.extract(caller_turn("اشريلي كَاَرْت تَعْبِئَة من فضلك"))
    assert [h.signal_id for h in hits] == ["RAIL_UNUSUAL"]
    assert hits[0].evidence == "كَاَرْت تَعْبِئَة"


def test_no_false_match_on_substring() -> None:
    ex = LexicalExtractor({"RAIL_UNUSUAL": ["card"]}, WEIGHTS)
    assert ex.extract(caller_turn("I bought a cardigan today")) == []
    assert ex.extract(caller_turn("my card number is 482915")) != []


def test_multi_signal_single_turn() -> None:
    ex = LexicalExtractor(EN, WEIGHTS)
    hits = ex.extract(caller_turn("This is your bank. Buy a gift card immediately."))
    assert sorted(h.signal_id for h in hits) == [
        "AUTH_CLAIM",
        "RAIL_UNUSUAL",
        "URGENCY",
    ]


def test_longest_match_wins_on_overlap() -> None:
    ex = LexicalExtractor({"RAIL_UNUSUAL": ["gift cards", "gift card"]}, WEIGHTS)
    hits = ex.extract(caller_turn("Buy gift cards now"))
    assert len(hits) == 1
    assert hits[0].evidence == "gift cards"


def test_role_is_attributed() -> None:
    ex = LexicalExtractor(EN, WEIGHTS)
    hits = ex.extract(callee_turn("my bank called me"))
    assert hits[0].role == "CALLEE"


def test_no_hits_on_clean_text() -> None:
    ex = LexicalExtractor(EN, WEIGHTS)
    assert ex.extract(caller_turn("What time is dinner")) == []


def test_evidence_span_maps_word_timings() -> None:
    words = (
        Word(text="This", start=0.0, end=0.4, confidence=1.0),
        Word(text="is", start=0.4, end=0.7, confidence=1.0),
        Word(text="your", start=0.7, end=1.1, confidence=1.0),
        Word(text="bank", start=1.1, end=1.6, confidence=1.0),
        Word(text="calling", start=1.6, end=2.2, confidence=1.0),
    )
    ex = LexicalExtractor(EN, WEIGHTS)
    hits = ex.extract(caller_turn("This is your bank calling", words=words))
    assert len(hits) == 1
    t0, t1 = hits[0].evidence_span
    assert t0 == pytest.approx(1.1)
    assert t1 == pytest.approx(1.475)


def test_evidence_span_falls_back_without_words() -> None:
    ex = LexicalExtractor(EN, WEIGHTS)
    hits = ex.extract(caller_turn("This is your bank calling", t_start=3.0, t_end=9.0))
    assert hits[0].evidence_span == (3.0, 9.0)
