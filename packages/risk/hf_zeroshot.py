"""The real :class:`~packages.risk.semantic.ZeroShotClassifier` — a thin
wrapper over a Hugging Face zero-shot pipeline.

``transformers`` / ``torch`` are imported lazily in ``__init__`` so that
importing this module costs nothing and the rest of the risk engine stays
free of the dependency.  Install with ``pip install -e ".[semantic]"``.
"""

from __future__ import annotations

from collections.abc import Sequence

DEFAULT_MODEL = "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"


class HFZeroShotClassifier:
    def __init__(self, model_name: str = DEFAULT_MODEL, *, device: int = -1) -> None:
        try:
            from transformers import pipeline
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "HFZeroShotClassifier needs the 'semantic' extra: pip install -e '.[semantic]'"
            ) from exc
        self.model_name = model_name
        self._pipe = pipeline("zero-shot-classification", model=model_name, device=device)

    def score(self, premise: str, hypotheses: Sequence[str]) -> list[float]:
        labels = list(hypotheses)
        out = self._pipe(premise, labels, multi_label=True)
        by_label = dict(zip(out["labels"], out["scores"], strict=True))
        return [float(by_label[h]) for h in labels]
