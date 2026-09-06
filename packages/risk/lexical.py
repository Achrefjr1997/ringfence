import re
import unicodedata
from collections import deque

from packages.contracts.risk import SignalHit
from packages.contracts.transcript import AttributedTurn, Turn, Word

ARABIC_DIACRITICS = re.compile(
    "[\u064b-\u065f\u0670\u06d6-\u06dc\u06df-\u06e4\u06e7-\u06e8\u06ea-\u06ed]"
)


def _normalize(text: str) -> tuple[str, list[int]]:
    """NFKC + lowercase + strip Arabic diacritics.

    Returns (normalized_text, orig_idx) where orig_idx[k] is the index in
    the original text of the k-th normalized character.
    """
    norm_chars: list[str] = []
    orig_idx: list[int] = []
    for i, ch in enumerate(text):
        for nch in unicodedata.normalize("NFKC", ch).lower():
            if ARABIC_DIACRITICS.match(nch):
                continue
            norm_chars.append(nch)
            orig_idx.append(i)
    return "".join(norm_chars), orig_idx


class _Node:
    __slots__ = ("next", "fail", "outputs")

    def __init__(self) -> None:
        self.next: dict[str, _Node] = {}
        self.fail: _Node | None = None
        self.outputs: list[tuple[str, str, int, bool]] = []


def _build_trie(lexicons: dict[str, list[str]]) -> _Node:
    root = _Node()
    for signal_id, terms in lexicons.items():
        for term in terms:
            norm, _ = _normalize(term)
            if not norm:
                continue
            node = root
            for ch in norm:
                node = node.next.setdefault(ch, _Node())
            is_latin = any(c.isascii() and c.isalpha() for c in norm)
            node.outputs.append((signal_id, term, len(norm), is_latin))
    queue: deque[_Node] = deque()
    for child in root.next.values():
        child.fail = root
        queue.append(child)
    while queue:
        node = queue.popleft()
        for ch, child in node.next.items():
            fail = node.fail
            while fail is not None and ch not in fail.next:
                fail = fail.fail
            child.fail = fail.next[ch] if fail else root
            inherited = child.fail.outputs if child.fail else []
            child.outputs = list(dict.fromkeys(child.outputs + inherited))
            queue.append(child)
    return root


def _boundary_ok(norm: str, start: int, end: int) -> bool:
    left_ok = start == 0 or not norm[start - 1].isalnum()
    right_ok = end == len(norm) or not norm[end].isalnum()
    return left_ok and right_ok


def _find_matches(root: _Node, norm: str) -> list[tuple[int, int, str, str]]:
    """(start, end, signal_id, term) in normalized coordinates."""
    matches: list[tuple[int, int, str, str]] = []
    node = root
    for i, ch in enumerate(norm):
        while node is not root and ch not in node.next:
            fail = node.fail
            if fail is None:
                break
            node = fail
        if ch in node.next:
            node = node.next[ch]
        for signal_id, term, norm_len, is_latin in node.outputs:
            start = i + 1 - norm_len
            if is_latin and not _boundary_ok(norm, start, i + 1):
                continue
            matches.append((start, i + 1, signal_id, term))
    return matches


def _resolve_longest(
    matches: list[tuple[int, int, str, str]],
) -> list[tuple[int, int, str, str]]:
    """Longest match wins on overlap; output ordered by position."""
    ordered = sorted(matches, key=lambda m: (-(m[1] - m[0]), m[0]))
    kept: list[tuple[int, int, str, str]] = []
    for m in ordered:
        if any(not (m[1] <= k[0] or k[1] <= m[0]) for k in kept):
            continue
        kept.append(m)
    return sorted(kept, key=lambda m: (m[0], m[1]))


def _word_ranges(text: str, words: tuple[Word, ...]) -> list[tuple[int, int, Word]] | None:
    ranges: list[tuple[int, int, Word]] = []
    pos = 0
    for w in words:
        start = text.find(w.text, pos)
        if start < 0:
            return None
        ranges.append((start, start + len(w.text), w))
        pos = start + len(w.text)
    return ranges


def _offset_to_time(offset: int, turn: Turn, ranges: list[tuple[int, int, Word]]) -> float | None:
    for start, end, w in ranges:
        if start <= offset < end:
            frac = (offset - start) / len(w.text)
            return w.start + frac * (w.end - w.start)
    return None


class LexicalExtractor:
    """Aho-Corasick multi-pattern matcher over per-signal lexicons.

    Pure function of the turn: case-insensitive, NFKC-normalised, Arabic
    diacritics stripped, word boundaries for Latin-script terms, longest
    match wins on overlap. No I/O.
    """

    version = "lexical@1.0"

    def __init__(self, lexicons: dict[str, list[str]], weights: dict[str, float]) -> None:
        self.weights = weights
        self._root = _build_trie(lexicons)

    def extract(self, turn: AttributedTurn) -> list[SignalHit]:
        norm, idx_map = _normalize(turn.turn.text)
        if not norm:
            return []
        matches = _find_matches(self._root, norm)
        ranges = _word_ranges(turn.turn.text, turn.turn.words) if turn.turn.words else None
        hits: list[SignalHit] = []
        for start, end, signal_id, _ in _resolve_longest(matches):
            orig_start = idx_map[start]
            orig_end = idx_map[end - 1] + 1
            evidence = turn.turn.text[orig_start:orig_end]
            if ranges is not None:
                t0 = _offset_to_time(orig_start, turn.turn, ranges)
                t1 = _offset_to_time(orig_end - 1, turn.turn, ranges)
                if t0 is None or t1 is None:
                    t0, t1 = turn.turn.t_start, turn.turn.t_end
            else:
                t0, t1 = turn.turn.t_start, turn.turn.t_end
            hits.append(
                SignalHit(
                    signal_id=signal_id,
                    weight=self.weights.get(signal_id, 0.0),
                    role=turn.role,
                    t=t0,
                    evidence=evidence,
                    evidence_span=(t0, t1),
                    extractor=self.version,
                )
            )
        return hits
