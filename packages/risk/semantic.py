"""Zero-shot NLI signal extraction (T-1.7).

The paraphrase-tolerant complement to :class:`~packages.risk.lexical.LexicalExtractor`.
Each signal carries one or more natural-language *hypotheses*; a turn is the
*premise*.  The signal fires when the maximum entailment probability across
its hypotheses clears a threshold, and the emitted weight is the pack weight
scaled by that probability — a marginal match contributes less than a
confident one.

No model is imported here.  The extractor takes a :class:`ZeroShotClassifier`;
the real one (:class:`packages.risk.hf_zeroshot.HFZeroShotClassifier`) wraps a
Hugging Face pipeline and pulls in ``torch`` only when constructed, so this
module and its tests run with neither installed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from packages.contracts.risk import SignalHit
from packages.contracts.transcript import AttributedTurn


@runtime_checkable
class ZeroShotClassifier(Protocol):
    def score(self, premise: str, hypotheses: Sequence[str]) -> list[float]:
        """Entailment probability of each hypothesis given the premise,
        independently (multi-label), in the order the hypotheses were given."""
        ...


class SemanticExtractor:
    version = "semantic@1.0"

    def __init__(
        self,
        hypotheses: dict[str, list[str]],
        weights: dict[str, float],
        classifier: ZeroShotClassifier,
        *,
        threshold: float = 0.7,
        scale_weight: bool = True,
    ) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError(f"threshold must be in (0, 1], got {threshold}")
        self._weights = weights
        self._classifier = classifier
        self._threshold = threshold
        self._scale_weight = scale_weight
        # Flatten once; keep a parallel map back to the owning signal.
        self._flat: list[str] = []
        self._owner: list[str] = []
        for signal_id, hyps in hypotheses.items():
            for h in hyps:
                self._flat.append(h)
                self._owner.append(signal_id)

    def extract(self, turn: AttributedTurn) -> list[SignalHit]:
        text = turn.turn.text.strip()
        if not text or not self._flat:
            return []

        probs = self._classifier.score(text, self._flat)
        if len(probs) != len(self._flat):
            raise ValueError(
                f"classifier returned {len(probs)} scores for {len(self._flat)} hypotheses"
            )

        # Best hypothesis per signal.
        best: dict[str, tuple[float, str]] = {}
        for owner, hyp, prob in zip(self._owner, self._flat, probs, strict=True):
            if owner not in best or prob > best[owner][0]:
                best[owner] = (prob, hyp)

        hits: list[SignalHit] = []
        for signal_id, (prob, hyp) in best.items():
            if prob < self._threshold:
                continue
            base = self._weights.get(signal_id, 0.0)
            weight = base * prob if self._scale_weight else base
            hits.append(
                SignalHit(
                    signal_id=signal_id,
                    weight=weight,
                    role=turn.role,
                    t=turn.turn.t_start,
                    evidence=hyp,
                    evidence_span=(turn.turn.t_start, turn.turn.t_end),
                    extractor=self.version,
                )
            )
        return hits
