from dataclasses import dataclass
from typing import Literal

from packages.contracts.transcript import Role

State = Literal["CALM", "WATCH", "ALERT", "INTERVENE", "RESOLVED"]


@dataclass(frozen=True, slots=True)
class SignalHit:
    signal_id: str
    weight: float
    role: Role
    t: float
    evidence: str
    evidence_span: tuple[float, float]
    extractor: str  # "lexical@1.0"


@dataclass(frozen=True, slots=True)
class Contribution:
    source: Literal["signal", "combo", "enrichment", "judge"]
    id: str
    value: float
    role: Role | None = None
    t: float | None = None
    evidence: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    decision_id: str
    session_id: str
    t: float
    state: State
    score: float
    policy_pack: str
    contributions: tuple[Contribution, ...]
    counterfactual: str | None = None


@dataclass(frozen=True, slots=True)
class Verdict:
    verdict: Literal["benign", "unclear", "suspicious", "fraud"]
    adjustment: int
    signals: tuple[str, ...]
    protective: tuple[str, ...]
    rationale: str
    model_version: str
    latency_ms: int
