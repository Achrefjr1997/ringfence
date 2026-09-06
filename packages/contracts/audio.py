from dataclasses import dataclass
from enum import Enum


class Mode(str, Enum):
    CARRIER = "carrier"
    SDK = "sdk"
    ENTERPRISE = "enterprise"
    REPLAY = "replay"


class RoleHint(str, Enum):
    CALLER = "caller_hint"
    CALLEE = "callee_hint"
    MIXED = "mixed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class LegSpec:
    leg_id: str
    role_hint: RoleHint
    sample_rate: int


@dataclass(frozen=True, slots=True)
class SessionDescriptor:
    session_id: str
    tenant_id: str
    mode: Mode
    legs: tuple[LegSpec, ...]
    started_at: float  # unix seconds
    language: str | None = None
    consent_token: str | None = None


@dataclass(frozen=True, slots=True)
class Frame:
    session_id: str
    leg_id: str
    pcm: bytes  # int16 little-endian, mono
    sample_rate: int
    seq: int
    captured_at: float  # unix seconds — root of the latency measurement
