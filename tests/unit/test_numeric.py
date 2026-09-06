from packages.contracts.risk import SignalHit
from packages.risk.numeric import NumericExtractor
from tests.helpers import callee_turn, caller_turn

W = {"VERIF_INVERT": 30.0}


def _extract(text: str) -> list[SignalHit]:
    return NumericExtractor(weights=W).extract(caller_turn(text))


def test_otp_4_to_8_digits_caller() -> None:
    hits = _extract("give me the code 482915")
    assert len(hits) == 1
    assert hits[0].signal_id == "VERIF_INVERT"
    assert hits[0].weight == 30.0
    assert hits[0].role == "CALLER"
    assert hits[0].evidence == "482915"
    assert hits[0].extractor == "numeric@1.0"


def test_callee_never_fires() -> None:
    hits = NumericExtractor(weights=W).extract(callee_turn("my code is 482915"))
    assert hits == []


def test_spaced_digit_sequence() -> None:
    hits = _extract("give me the code 4 8 2 9 1 5")
    assert len(hits) == 1
    assert hits[0].evidence == "4 8 2 9 1 5"


def test_otp_3_digits_does_not_fire() -> None:
    assert _extract("the code is 482") == []


def test_otp_9_digits_does_not_fire() -> None:
    assert _extract("the code is 482915123") == []


def test_pan_luhn_valid() -> None:
    hits = _extract("read me the card number 4111 1111 1111 1111")
    assert len(hits) == 1
    assert hits[0].signal_id == "VERIF_INVERT"


def test_pan_luhn_invalid_does_not_fire() -> None:
    assert _extract("read me the card number 4111 1111 1111 1112") == []


def test_iban_valid_fires() -> None:
    hits = _extract("transfer to my account TN59 1000 6035 1835 9847 8831")
    assert len(hits) == 1
    assert hits[0].signal_id == "VERIF_INVERT"


def test_iban_invalid_checksum_does_not_fire() -> None:
    assert _extract("transfer to my account TN59 1000 6035 1835 9847 8832") == []


def test_spelled_out_digits_english() -> None:
    hits = _extract("read me the code one two three four")
    assert len(hits) == 1
    assert hits[0].evidence == "one two three four"


def test_spelled_out_digits_french() -> None:
    hits = _extract("lisez le code quatre huit deux neuf un cinq")
    assert len(hits) == 1


def test_amount_with_currency_does_not_fire_verif_invert() -> None:
    """An amount is not a credential request.

    '850 dinars' appears in the benign frauddesk fixture — it must not
    fire VERIF_INVERT, or invariant 2 breaks.
    """
    assert _extract("the transaction was 850 dinars") == []
    assert _extract("send me 1500 dinars now") == []


def test_arabic_indic_digits_normalized() -> None:
    hits = _extract("الكود هو ٤٨٢٩١٥")
    assert len(hits) == 1
    assert hits[0].evidence == "٤٨٢٩١٥"


def test_extract_amounts_parses() -> None:
    ex = NumericExtractor(weights=W)
    assert ex.extract_amounts(caller_turn("the transaction was 850 dinars")) == [(850.0, "dinars")]
    assert ex.extract_amounts(caller_turn("الغرمة 1500 دينار")) == [(1500.0, "دينار")]
    assert ex.extract_amounts(caller_turn("no money involved here")) == []
