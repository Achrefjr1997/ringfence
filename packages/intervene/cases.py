"""Case builder (T-4.4, §8.4).

Every ``ALERT`` or above opens a case for analyst review: the decision
chain, the transcript, and a feedback label (``fraud | benign | unclear``).
The feedback is the primary input to the labelling loop (§11.2) — treat it
as a first-class feature, not an afterthought.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from packages.contracts.risk import Decision, State
from packages.contracts.transcript import Role

FeedbackLabel = Literal["fraud", "benign", "unclear"]
_FEEDBACK: frozenset[str] = frozenset(("fraud", "benign", "unclear"))

_RANK: dict[State, int] = {"CALM": 0, "WATCH": 1, "ALERT": 2, "INTERVENE": 3, "RESOLVED": 0}


@dataclass
class Case:
    session_id: str
    opened_at: float
    tenant: str = ""  # the org this case belongs to (T-7.1d access scoping)
    decisions: list[Decision] = field(default_factory=list)
    transcript: list[tuple[Role, str, float]] = field(default_factory=list)
    feedback: FeedbackLabel | None = None
    feedback_note: str = ""

    @property
    def peak_state(self) -> State:
        return max((d.state for d in self.decisions), key=lambda s: _RANK[s], default="CALM")

    @property
    def peak_score(self) -> float:
        return max((d.score for d in self.decisions), default=0.0)


class CaseStore:
    def __init__(self) -> None:
        self._cases: dict[str, Case] = {}

    def record(
        self,
        session_id: str,
        decision: Decision,
        transcript: list[tuple[Role, str, float]],
        *,
        tenant: str = "",
    ) -> Case:
        """Upsert the case for ``session_id`` with an ALERT+ decision."""
        case = self._cases.get(session_id)
        if case is None:
            case = Case(session_id=session_id, opened_at=decision.t, tenant=tenant)
            self._cases[session_id] = case
        case.decisions.append(decision)
        case.transcript = list(transcript)
        return case

    def get(self, session_id: str) -> Case | None:
        return self._cases.get(session_id)

    def list(self) -> list[Case]:
        return sorted(self._cases.values(), key=lambda c: c.opened_at)

    def set_feedback(self, session_id: str, label: str, note: str = "") -> Case:
        if label not in _FEEDBACK:
            raise ValueError(f"label must be one of {sorted(_FEEDBACK)}, got {label!r}")
        case = self._cases.get(session_id)
        if case is None:
            raise KeyError(session_id)
        case.feedback = label  # type: ignore[assignment]
        case.feedback_note = note
        return case
