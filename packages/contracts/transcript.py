from dataclasses import dataclass
from typing import Literal

Role = Literal["CALLER", "CALLEE", "UNKNOWN"]


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    start: float
    end: float
    confidence: float


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


@dataclass(frozen=True, slots=True)
class AttributedTurn:
    turn: Turn
    role: Role
    role_confidence: float
