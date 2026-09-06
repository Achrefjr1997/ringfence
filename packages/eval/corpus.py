"""Corpus and label schema (T-5.1, MVP §13.2).

A *corpus item* is a real recording (16 kHz mono wav) plus a label file
under ``corpus/labels/``.  The label carries ground-truth turns with
``signals`` (not transcript text — that comes from ASR at replay time),
the value-transfer moment, and the language / family.

The hand-written ``corpus/fixtures/*.json`` used everywhere else are a
separate, transcript-carrying schema (see ``eval.fixtures``).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, field_validator

from packages.eval.fixtures import Label, Language
from packages.policy.pack import KNOWN_SIGNAL_IDS

CORPUS_DIR = Path(__file__).resolve().parents[2] / "corpus"
LABEL_DIR = CORPUS_DIR / "labels"

_LABEL_ALIASES = {"scam": "fraud", "legit": "benign", "legitimate": "benign"}


class CorpusError(ValueError):
    pass


class LabelTurn(BaseModel):
    t_start: float
    t_end: float
    role: str  # CALLER | CALLEE | UNKNOWN
    signals: list[str] = []

    @field_validator("signals")
    @classmethod
    def _known_signals(cls, v: list[str]) -> list[str]:
        unknown = set(v) - KNOWN_SIGNAL_IDS
        if unknown:
            raise ValueError(f"unknown signal ids: {sorted(unknown)}")
        return v


class CorpusItem(BaseModel):
    id: str
    file: str  # wav path, relative to corpus/
    language: Language
    label: Label
    scam_family: str = "unknown"
    transfer_line_t: float | None = None
    turns: list[LabelTurn] = []

    @field_validator("label", mode="before")
    @classmethod
    def _normalise_label(cls, v: object) -> object:
        return _LABEL_ALIASES.get(str(v), v)

    @property
    def wav_path(self) -> Path:
        return CORPUS_DIR / self.file

    @property
    def first_signal_t(self) -> float | None:
        ts = [t.t_start for t in self.turns if t.signals]
        return min(ts) if ts else None


def load_corpus_item(item_id: str) -> CorpusItem:
    path = LABEL_DIR / f"{item_id}.json"
    if not path.exists():
        raise CorpusError(f"no label file: {path}")
    return CorpusItem.model_validate_json(path.read_text(encoding="utf-8"))


def iter_corpus(label: str | None = None) -> list[CorpusItem]:
    items = [load_corpus_item(p.stem) for p in sorted(LABEL_DIR.glob("*.json"))]
    return items if label is None else [i for i in items if i.label == label]


def validate_wav(path: Path) -> None:
    """Raise :class:`CorpusError` unless ``path`` is a 16 kHz mono wav."""
    import soundfile as sf

    if not path.exists():
        raise CorpusError(f"missing audio: {path}")
    if path.suffix.lower() != ".wav":
        raise CorpusError(f"not a wav: {path}")
    info = sf.info(path)
    if info.samplerate != 16_000:
        raise CorpusError(f"{path.name}: {info.samplerate} Hz, expected 16000")
    if info.channels != 1:
        raise CorpusError(f"{path.name}: {info.channels} channels, expected mono")


def validate_corpus() -> list[str]:
    """Return a list of problems; empty means every item's wav checks out."""
    errors: list[str] = []
    for item in iter_corpus():
        try:
            validate_wav(item.wav_path)
        except CorpusError as exc:
            errors.append(f"{item.id}: {exc}")
    return errors
