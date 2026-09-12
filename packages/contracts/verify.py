"""Verification-agent contracts.

The agent opens a conversation with the institution a caller *claims* to be
from, asks one question, and reports what it was told. These types cross the
dispatcher / agent / bus boundary, so per AGENTS.md they live here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Where a verification got to. Published on ``rf.<tenant>.verification`` so a
# viewer can watch it happen rather than waiting for a verdict in silence.
VerificationStage = Literal[
    "skipped",  # no institution resolved, or budget/guard refused -- never ran
    "dialing",
    "connected",
    "transcript",
    "result",
    "failed",
]


@dataclass(frozen=True, slots=True)
class VerificationOutcome:
    """What the institution said, or why we never found out.

    ``verified`` is deliberately **tri-state**. ``None`` means the session
    ended without an answer -- nobody picked up, the line dropped, the
    duration cap fired. That is a different claim from ``False`` ("they told
    us they never called"), and conflating the two would let "we could not
    reach them" render as an accusation. Never coerce one into the other.
    """

    verified: bool | None
    reason: str
    confidence: Literal["high", "low"] | None = None
    duration_s: float = 0.0
    error: str | None = None
