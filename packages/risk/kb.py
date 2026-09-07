"""Scam-pattern knowledge base for the Tier-2 judge (fraud base-knowledge, §2).

A small set of short, labelled dialogue excerpts — bank impersonation, tech
support, government / prize / refund / gift-card / family-emergency /
delivery scams, plus deceptively-urgent-but-legitimate calls — rendered
into the judge's system prompt as few-shot reference patterns.  The judge
stays bounded to ``±pack.judge.max_adjustment`` (invariant #3); this only
sharpens *how* it reasons, never *how far* it can move the score.

**v1 — :class:`StaticKnowledgeBase`:** returns a fixed curated set,
independent of the call.  Fraud/benign interleaved so any prefix stays
balanced.  Still the fallback when a retrieval index can't be built.

**v2 — :class:`BM25KnowledgeBase`:** query-dependent lexical retrieval
(Okapi BM25, pure Python, no network) over the same excerpts.  The query is
the recent CALLER speech plus the active rule signals; the top matches are
returned, with a floor of a couple of benign excerpts kept for contrast so
the judge doesn't over-trigger.  This is the production default
(``apps.gateway.app``).

The original §2 plan called for Ollama-Cloud embedding retrieval, but
``https://ollama.com`` returns 401 on ``/api/embed`` for every model on
this tier while chat works — cloud embeddings aren't available.  BM25 needs
no model, runs everywhere including CI, and keeps the
:class:`KnowledgeBase` protocol and the judge's ``_build_prompt``
unchanged.  A future embedding backend is a third class implementing the
same one method.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from packages.risk.judge import DialogueWindow

KB_PATH = Path(__file__).resolve().parents[2] / "corpus" / "kb" / "scam_patterns.jsonl"

ExcerptLabel = Literal["fraud", "benign"]

_DEFAULT_K = 8
_DEFAULT_MIN_BENIGN = 2
_QUERY_WORD_BUDGET = 240  # tail of the CALLER speech used to build the query

# Deliberately tiny — role markers and a handful of function words that
# appear in nearly every excerpt and carry no retrieval signal.
_STOPWORDS = frozenset(
    """
    caller callee a an the this that is are was were be been to of and or for on in it you your
    i we they me my our so if no not do does did now here there with as at will can could would
    """.split()
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


@dataclass(frozen=True, slots=True)
class Excerpt:
    id: str
    text: str
    label: ExcerptLabel
    scam_family: str
    tactics: tuple[str, ...]


class KnowledgeBase(Protocol):
    def examples_for(self, window: DialogueWindow) -> list[Excerpt]: ...


def load_excerpts(path: Path = KB_PATH) -> list[Excerpt]:
    out: list[Excerpt] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        out.append(
            Excerpt(
                id=str(d["id"]),
                text=str(d["text"]),
                label=d["label"],
                scam_family=str(d["scam_family"]),
                tactics=tuple(d.get("tactics", [])),
            )
        )
    return out


# ---------------------------------------------------------------------------
# v1 — static
# ---------------------------------------------------------------------------


class StaticKnowledgeBase:
    """A fixed, curated, call-independent example set.

    ``examples_for`` ignores the window (by design) and always returns the
    same excerpts in the same order, fraud and benign interleaved so a
    prompt built from the head of the list is still balanced.  ``k`` caps
    how many are returned.
    """

    def __init__(self, excerpts: Sequence[Excerpt], *, k: int | None = None) -> None:
        self._excerpts = _interleave(excerpts)
        self._k = k

    @classmethod
    def load(cls, path: Path = KB_PATH, *, k: int | None = None) -> StaticKnowledgeBase:
        return cls(load_excerpts(path), k=k)

    def __len__(self) -> int:
        return len(self._excerpts)

    def examples_for(self, window: DialogueWindow) -> list[Excerpt]:  # noqa: ARG002 - static
        return list(self._excerpts if self._k is None else self._excerpts[: self._k])


def _interleave(excerpts: Sequence[Excerpt]) -> tuple[Excerpt, ...]:
    """fraud, benign, fraud, benign, ... preserving original order within each."""
    fraud = [e for e in excerpts if e.label == "fraud"]
    benign = [e for e in excerpts if e.label == "benign"]
    out: list[Excerpt] = []
    for f, b in zip(fraud, benign, strict=False):
        out.append(f)
        out.append(b)
    longer = fraud if len(fraud) > len(benign) else benign
    out.extend(longer[min(len(fraud), len(benign)) :])
    return tuple(out)


# ---------------------------------------------------------------------------
# v2 — BM25 lexical retrieval
# ---------------------------------------------------------------------------


class _Bm25:
    """Okapi BM25 over a fixed corpus of pre-tokenised documents."""

    def __init__(self, docs: Sequence[Sequence[str]], *, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._tf: list[Counter[str]] = [Counter(d) for d in docs]
        self._len = [len(d) for d in docs]
        self._avgdl = (sum(self._len) / len(self._len)) if self._len else 0.0
        n = len(docs)
        df: Counter[str] = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        self._idf = {
            term: math.log(1 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()
        }

    def scores(self, query: Iterable[str]) -> list[float]:
        q = [t for t in query if t in self._idf]
        out: list[float] = []
        for tf, dl in zip(self._tf, self._len, strict=True):
            s = 0.0
            for term in q:
                f = tf.get(term, 0)
                if not f:
                    continue
                denom = f + self._k1 * (1 - self._b + self._b * dl / (self._avgdl or 1.0))
                s += self._idf[term] * f * (self._k1 + 1) / denom
            out.append(s)
        return out


class BM25KnowledgeBase:
    """Query-dependent lexical retrieval over the excerpts.

    The document for excerpt *e* is its dialogue text plus its ``tactics``
    (underscores split), so a query term like ``remote`` (from the live
    call) or ``access`` (from a ``REMOTE_ACCESS`` signal) can match either.
    ``examples_for`` returns the ``k`` highest-scoring excerpts in score
    order, but guarantees at least ``min_benign`` benign excerpts in the
    set for contrast.  Ties and empty queries fall back to the static
    interleaved order, so the result is always deterministic.
    """

    def __init__(
        self,
        excerpts: Sequence[Excerpt],
        *,
        k: int = _DEFAULT_K,
        min_benign: int = _DEFAULT_MIN_BENIGN,
    ) -> None:
        self._excerpts = list(excerpts)
        self._k = k
        self._min_benign = min_benign
        self._fallback = _interleave(excerpts)
        docs = [
            _tokenize(e.text + " " + " ".join(e.tactics).replace("_", " ")) for e in self._excerpts
        ]
        self._bm25 = _Bm25(docs)

    @classmethod
    def load(
        cls,
        path: Path = KB_PATH,
        *,
        k: int = _DEFAULT_K,
        min_benign: int = _DEFAULT_MIN_BENIGN,
    ) -> BM25KnowledgeBase:
        return cls(load_excerpts(path), k=k, min_benign=min_benign)

    def __len__(self) -> int:
        return len(self._excerpts)

    def _query_terms(self, window: DialogueWindow) -> list[str]:
        caller_words = " ".join(text for role, text, _ in window.turns if role == "CALLER").split()
        recent = " ".join(caller_words[-_QUERY_WORD_BUDGET:])  # recent asks matter most
        signal_words = " ".join(
            s.replace("_", " ").lower() for s in (*window.active_signals, *window.active_protective)
        )
        return _tokenize(recent + " " + signal_words)

    def examples_for(self, window: DialogueWindow) -> list[Excerpt]:
        terms = self._query_terms(window)
        if not terms:
            return list(self._fallback[: self._k])

        scores = self._bm25.scores(terms)
        if not any(scores):
            return list(self._fallback[: self._k])

        # stable sort: score desc, then original file order
        order = sorted(range(len(self._excerpts)), key=lambda i: (-scores[i], i))
        picked = order[: self._k]

        n_benign = sum(1 for i in picked if self._excerpts[i].label == "benign")
        if n_benign < self._min_benign:
            need = self._min_benign - n_benign
            extra = [i for i in order if self._excerpts[i].label == "benign" and i not in picked][
                :need
            ]
            # drop the weakest fraud picks to make room, keep score order
            fraud_in_picked = [i for i in picked if self._excerpts[i].label == "fraud"]
            for i in fraud_in_picked[-len(extra) :]:
                picked.remove(i)
            picked = sorted([*picked, *extra], key=lambda i: (-scores[i], i))

        return [self._excerpts[i] for i in picked]
