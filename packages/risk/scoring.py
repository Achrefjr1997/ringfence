from dataclasses import dataclass, field

from packages.contracts.risk import Contribution, SignalHit
from packages.contracts.transcript import Role
from packages.policy.pack import PolicyPack


@dataclass
class EvidenceWindow:
    """Rolling evidence window.

    Dedup keeps the MOST RECENT occurrence per (signal_id, role) —
    age_decay must answer "how fresh is the current evidence", not how
    old the first sighting was. Repetition is ESCALATION's job, not
    summation's.
    """

    span_s: float = 180.0
    hits: dict[tuple[str, Role], SignalHit] = field(default_factory=dict)

    def add(self, hit: SignalHit) -> None:
        self.hits[(hit.signal_id, hit.role)] = hit
        cutoff = hit.t - self.span_s
        self.hits = {k: h for k, h in self.hits.items() if h.t >= cutoff}

    def has(self, signal_id: str, within_s: float, now: float, role: Role = "CALLER") -> bool:
        hit = self.hits.get((signal_id, role))
        return hit is not None and now - hit.t <= within_s

    def active(self, now: float) -> list[SignalHit]:
        cutoff = now - self.span_s
        return sorted(
            (h for h in self.hits.values() if h.t >= cutoff),
            key=lambda h: h.t,
        )


def score_window(
    window: EvidenceWindow,
    now: float,
    pack: PolicyPack,
    extras: tuple[Contribution, ...] = (),
) -> tuple[float, list[Contribution]]:
    contributions: list[Contribution] = []
    total = 0.0
    for hit in window.active(now):
        age_decay = max(0.4, 2 ** (-(now - hit.t) / pack.thresholds.decay_half_life_s))
        role_factor = 1.0 if hit.role == "CALLER" else (0.5 if hit.role == "UNKNOWN" else 0.0)
        value = hit.weight * age_decay * role_factor
        total += value
        contributions.append(
            Contribution(
                source="signal",
                id=hit.signal_id,
                value=value,
                role=hit.role,
                t=hit.t,
                evidence=hit.evidence,
            )
        )
    for extra in extras:
        total += extra.value
        contributions.append(extra)
    return max(0.0, min(100.0, total)), contributions
