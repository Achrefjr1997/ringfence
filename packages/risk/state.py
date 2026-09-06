from copy import copy
from uuid import uuid4

from packages.contracts.risk import Contribution, Decision, State
from packages.policy.pack import PolicyPack


class RiskStateMachine:
    """Hysteresis state machine per production §7.3.

    One transition per ``update`` call.  Escalation requires
    ``sustain_turns`` consecutive turns above threshold; de-escalation
    requires a quiet period below ``threshold - margin``.  COMBO_CRITICAL
    escalates ``ALERT → INTERVENE`` immediately (no sustain needed).
    """

    def __init__(self, pack: PolicyPack, session_id: str = "") -> None:
        self.pack = pack
        self.session_id = session_id
        self.state: State = "CALM"
        self._sustain = 0
        self._quiet_since: float | None = None
        self._intervene_at: float | None = None

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    def update(
        self, score: float, contributions: list[Contribution], now: float
    ) -> Decision | None:
        prev = self.state
        self._apply(score, contributions, now)
        if self.state == prev:
            return None
        return self._decision(score, contributions, now, prev)

    def end(self, now: float) -> Decision | None:
        """Transition to RESOLVED at call end."""
        if self.state == "RESOLVED":
            return None
        prev = self.state
        self.state = "RESOLVED"
        return self._decision(0.0, (), now, prev)

    # ------------------------------------------------------------------
    # transition
    # ------------------------------------------------------------------

    def _apply(self, score: float, contributions: list[Contribution], now: float) -> None:
        t = self.pack.thresholds
        has_combo = any(c.id == "COMBO_CRITICAL" for c in contributions)

        if self.state == "CALM":
            if score >= t.watch:
                self._sustain += 1
                if self._sustain >= t.sustain_turns:
                    self.state = "WATCH"
                    self._sustain = 0
            else:
                self._sustain = 0

        elif self.state == "WATCH":
            if score >= t.alert:
                self._sustain += 1
                if self._sustain >= t.sustain_turns:
                    self.state = "ALERT"
                    self._sustain = 0
                    self._quiet_since = None
            else:
                self._sustain = 0
            if self.state == "WATCH":
                self._de_escalate(
                    score < t.watch - t.watch_margin,
                    t.watch_quiet_s,
                    "CALM",
                    now,
                )

        elif self.state == "ALERT":
            if score >= t.intervene or has_combo:
                self.state = "INTERVENE"
                self._intervene_at = now
                self._quiet_since = None
            else:
                self._de_escalate(
                    score < t.alert - t.alert_margin,
                    t.alert_quiet_s,
                    "WATCH",
                    now,
                )

        elif self.state == "INTERVENE":
            if (
                self._intervene_at is not None
                and now - self._intervene_at >= self.pack.interventions.cooldown_s
            ):
                self.state = "ALERT"

    def _de_escalate(self, below: bool, quiet_s: float, target: State, now: float) -> None:
        if below:
            if self._quiet_since is None:
                self._quiet_since = now
            elif now - self._quiet_since >= quiet_s:
                self.state = target
                self._quiet_since = None
        else:
            self._quiet_since = None

    # ------------------------------------------------------------------
    # decision / counterfactual
    # ------------------------------------------------------------------

    def _decision(
        self,
        score: float,
        contributions: list[Contribution] | tuple[Contribution, ...],
        now: float,
        prev: State,
    ) -> Decision:
        return Decision(
            decision_id=uuid4().hex,
            session_id=self.session_id,
            t=now,
            state=self.state,
            score=score,
            policy_pack=self._pack_label(),
            contributions=tuple(contributions),
            counterfactual=self._counterfactual(score, contributions, now, prev)
            if self.state in ("ALERT", "INTERVENE")
            else None,
        )

    def _counterfactual(
        self,
        score: float,
        contributions: list[Contribution] | tuple[Contribution, ...],
        now: float,
        prev: State,
    ) -> str | None:
        if not contributions:
            return None
        largest = max(contributions, key=lambda c: c.value)
        if largest.value <= 0:
            return None
        reduced = max(0.0, min(100.0, score - largest.value))
        remaining = [c for c in contributions if c is not largest]
        clone = copy(self)
        clone.state = prev
        clone._sustain = 0
        clone._quiet_since = None
        clone._intervene_at = None
        clone._apply(reduced, remaining, now)
        return f"Without {largest.id} the score would be {reduced:.1f} ({clone.state})."

    def _pack_label(self) -> str:
        m = self.pack.metadata
        return f"{m.tenant}@{m.version}"