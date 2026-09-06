import re

from packages.contracts.risk import SignalHit
from packages.contracts.transcript import AttributedTurn

SPELLED_DIGIT_MAP = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "zéro": "0",
    "un": "1",
    "deux": "2",
    "trois": "3",
    "quatre": "4",
    "cinq": "5",
    "sept": "7",
    "huit": "8",
    "neuf": "9",
    "واحد": "1",
    "اثنين": "2",
    "ثلاثة": "3",
    "أربعة": "4",
    "خمسة": "5",
    "ستة": "6",
    "سبعة": "7",
    "ثمانية": "8",
    "تسعة": "9",
    "wa7ed": "1",
    "tnin": "2",
    "tleta": "3",
    "arb3a": "4",
    "khomsa": "5",
    "setta": "6",
    "sab3a": "7",
    "thmenya": "8",
    "tes3a": "9",
}
_SPELLED = "|".join(re.escape(w) for w in SPELLED_DIGIT_MAP)
SPELLED_RUN_RE = re.compile(rf"\b(?:{_SPELLED})(?:\s+(?:{_SPELLED}))+\b", re.IGNORECASE)
DIGIT_RUN_RE = re.compile(r"\d(?:[\d \-]*\d)?|\d")
IBAN_COMPACT_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{9,30}\b")
IBAN_GROUPED_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?\d{4}){2,6}\b")
CURRENCY_WORDS = {
    "dinar",
    "dinars",
    "dollar",
    "dollars",
    "euro",
    "euros",
    "دينار",
    "دنانير",
    "دولار",
    "يورو",
}
CURRENCY_PATTERN = r"\b(?:dinar|dinars|dollar|dollars|euro|euros|دينار|دنانير|دولار|يورو)\b"
AMOUNT_RE = re.compile(
    rf"({CURRENCY_PATTERN}\s+\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s+{CURRENCY_PATTERN})"
)
WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _iban_ok(iban: str) -> bool:
    s = iban.upper().replace(" ", "")
    rearranged = s[4:] + s[:4]
    digits = "".join(str(ord(c) - 55) if c.isalpha() else c for c in rearranged)
    return int(digits) % 97 == 1


def _classify_run(digits: str) -> str | None:
    n = len(digits)
    if 13 <= n <= 19 and _luhn_ok(digits):
        return "PAN"
    if 4 <= n <= 8:
        return "OTP"
    return None


def _adjacent_currency(text: str, start: int, end: int) -> bool:
    after = re.match(r"\s*([^\W\d_]+)", text[end:])
    if after and after.group(1).lower() in CURRENCY_WORDS:
        return True
    before = WORD_RE.findall(text[:start])
    if before and before[-1].lower() in CURRENCY_WORDS:
        return True
    return False


class NumericExtractor:
    """OTP / PAN / IBAN / amount shapes. Language-independent.

    VERIF_INVERT fires only when role == CALLER — a callee reading
    their own code back is the victim, not the attacker. Amounts
    adjacent to a currency word are never credentials.
    """

    version = "numeric@1.0"

    def __init__(self, weights: dict[str, float]) -> None:
        self.weights = weights

    def extract(self, turn: AttributedTurn) -> list[SignalHit]:
        if turn.role != "CALLER":
            return []
        text = turn.turn.text
        hits: list[SignalHit] = []

        iban_spans: list[tuple[int, int]] = []
        for rx in (IBAN_COMPACT_RE, IBAN_GROUPED_RE):
            for m in rx.finditer(text):
                span = (m.start(), m.end())
                if span not in iban_spans:
                    iban_spans.append(span)
                if _iban_ok(text[m.start() : m.end()]):
                    hits.append(self._hit(turn, m.group(0)))

        for m in SPELLED_RUN_RE.finditer(text):
            digits = "".join(SPELLED_DIGIT_MAP[w.lower()] for w in m.group(0).split())
            if 4 <= len(digits) <= 8:
                hits.append(self._hit(turn, m.group(0)))

        for m in DIGIT_RUN_RE.finditer(text):
            if any(s <= m.start() and m.end() <= e for s, e in iban_spans):
                continue
            if _adjacent_currency(text, m.start(), m.end()):
                continue
            digits = "".join(ch for ch in m.group(0) if ch.isdigit())
            if _classify_run(digits) is not None:
                hits.append(self._hit(turn, m.group(0)))
        return hits

    def extract_amounts(self, turn: AttributedTurn) -> list[tuple[float, str]]:
        amounts: list[tuple[float, str]] = []
        for m in AMOUNT_RE.finditer(turn.turn.text):
            token = m.group(0)
            num = re.search(r"\d+(?:[.,]\d+)?", token)
            cur = re.search(CURRENCY_PATTERN, token)
            if num and cur:
                amounts.append((float(num.group(0).replace(",", ".")), cur.group(0)))
        return amounts

    def _hit(self, turn: AttributedTurn, evidence: str) -> SignalHit:
        return SignalHit(
            signal_id="VERIF_INVERT",
            weight=self.weights.get("VERIF_INVERT", 0.0),
            role=turn.role,
            t=turn.turn.t_start,
            evidence=evidence,
            evidence_span=(turn.turn.t_start, turn.turn.t_end),
            extractor=self.version,
        )
