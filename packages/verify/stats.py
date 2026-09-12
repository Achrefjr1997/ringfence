"""Verification counters for ``/metrics``.

A verification can end five ways that look alike from outside -- confirmed,
unconfirmed, unanswered, failed, or never attempted -- and only two of them
cost money. Without these counters an operator cannot tell "the desk never
answers" from "nobody names an institution" from "the key is wrong", and
cannot see what the real agent is spending.

Label values are bounded by construction: outcomes and skip reasons are fixed
sets, and an error keeps only its prefix (``connect:ConnectionRefusedError``
counts as ``connect``), so no free text becomes a time series.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from packages.contracts.verify import VerificationOutcome


@dataclass(slots=True)
class VerificationStats:
    # (outcome, simulated) -> count
    outcomes: Counter[tuple[str, bool]] = field(default_factory=Counter)
    skipped: Counter[str] = field(default_factory=Counter)
    errors: Counter[str] = field(default_factory=Counter)
    # real Voice Agent session time only; simulated checks cost nothing
    agent_seconds: float = 0.0

    def skip(self, reason: str) -> None:
        self.skipped[reason] += 1

    def record(self, outcome: VerificationOutcome | None) -> None:
        if outcome is None:
            # the verifier's guard refused, or it raised -- nothing was learnt
            self.outcomes[("failed", False)] += 1
            return
        if outcome.verified is True:
            kind = "confirmed"
        elif outcome.verified is False:
            kind = "unconfirmed"
        else:
            kind = "unanswered"
        self.outcomes[(kind, outcome.simulated)] += 1
        if outcome.error:
            self.errors[outcome.error.split(":", 1)[0]] += 1
        if not outcome.simulated:
            self.agent_seconds += max(0.0, outcome.duration_s)
