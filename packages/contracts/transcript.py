from dataclasses import dataclass
from typing import Literal

Role = Literal["CALLER", "CALLEE", "UNKNOWN"]


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    start: float
    end: float
    confidence: float
    # Which of the provider's diarized voices said this word ("A", "B", ...).
    # Not a Role -- diarization tells voices apart, it does not know which
    # one is the caller. None whenever diarization was not requested or the
    # provider does not offer it. See packages/media/role.py for how this
    # feeds a cross-check on the acoustic classifier's guess.
    speaker_label: str | None = None


@dataclass(frozen=True, slots=True)
class Turn:
    session_id: str
    leg_id: str
    turn_order: int
    text: str
    is_final: bool
    is_formatted: bool
    t_start: float  # seconds from session start
    t_end: float
    words: tuple[Word, ...]
    confidence: float
    language: str | None = None
    speaker_label: str | None = None  # see Word.speaker_label


@dataclass(frozen=True, slots=True)
class AttributedTurn:
    turn: Turn
    role: Role
    role_confidence: float
