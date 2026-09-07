"""Scam-pattern knowledge base for the Tier-2 judge (fraud base-knowledge, §2).

A small set of short, labelled dialogue excerpts — bank impersonation, tech
support, government / prize / refund / gift-card / family-emergency /
delivery scams, plus deceptively-urgent-but-legitimate calls — rendered
into the judge's system prompt as few-shot reference patterns.  The judge
stays bounded to ``±pack.judge.max_adjustment`` (invariant #3); this only
sharpens *how* it reasons, never *how far* it can move the score.

**v1 (this module):** :class:`StaticKnowledgeBase` returns a fixed curated
set, independent of the call.  At ~20 excerpts "return the whole set" is
within a few items of "retrieve top-k", and carries no retrieval-drift
risk.

**v2 (later):** swap the body of :meth:`StaticKnowledgeBase.examples_for`
for an Ollama-Cloud-embedding cosine top-k over the same excerpts (query =
the live window).  The :class:`KnowledgeBase` protocol and the judge's
``_build_prompt`` do not change — only this one method does.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from packages.risk.judge import DialogueWindow

KB_PATH = Path(__file__).resolve().parents[2] / "corpus" / "kb" / "scam_patterns.jsonl"

ExcerptLabel = Literal["fraud", "benign"]


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


class StaticKnowledgeBase:
    """v1 KB: a fixed, curated, call-independent example set.

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

    def examples_for(self, window: DialogueWindow) -> list[Excerpt]:  # noqa: ARG002 - v1 is static
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
