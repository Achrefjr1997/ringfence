"""Prometheus text exposition for the gateway (T-7.5).

Kept free of gateway imports so it stays unit-testable: the endpoint hands
it a plain :class:`MetricsSnapshot` and gets back the ``text/plain;
version=0.0.4`` body.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

_PREFIX = "ringfence"


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    active: int
    capacity: int
    admitted: int
    rejected: Mapping[str, int] = field(default_factory=dict)
    active_by_tenant: Mapping[str, int] = field(default_factory=dict)
    asr_breakers: Mapping[str, str] = field(default_factory=dict)  # provider -> state


def _escape(label_value: str) -> str:
    return label_value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _block(name: str, kind: str, help_text: str, lines: list[str]) -> list[str]:
    out = [f"# HELP {_PREFIX}_{name} {help_text}", f"# TYPE {_PREFIX}_{name} {kind}"]
    out.extend(f"{_PREFIX}_{name}{line}" for line in lines)
    return out


def prometheus_text(snap: MetricsSnapshot) -> str:
    out: list[str] = []
    out += _block(
        "sessions_active", "gauge", "Currently admitted capture sessions.", [f" {snap.active}"]
    )
    out += _block(
        "session_capacity", "gauge", "Maximum concurrent sessions.", [f" {snap.capacity}"]
    )
    out += _block(
        "admitted_total", "counter", "Capture sessions admitted since start.", [f" {snap.admitted}"]
    )
    out += _block(
        "rejected_total",
        "counter",
        "Admission rejections since start, by reason.",
        [f'{{reason="{_escape(r)}"}} {n}' for r, n in sorted(snap.rejected.items())],
    )
    if snap.active_by_tenant:
        out += _block(
            "tenant_sessions_active",
            "gauge",
            "Currently admitted sessions, by tenant.",
            [f'{{tenant="{_escape(t)}"}} {n}' for t, n in sorted(snap.active_by_tenant.items())],
        )
    if snap.asr_breakers:
        # one series per (provider, state); 1 marks the live state, 0 the rest
        states = ("closed", "open", "half_open")
        lines = [
            f'{{provider="{_escape(p)}",state="{s}"}} {int(cur == s)}'
            for p, cur in sorted(snap.asr_breakers.items())
            for s in states
        ]
        out += _block(
            "asr_breaker_state", "gauge", "ASR circuit-breaker state per provider.", lines
        )
    return "\n".join(out) + "\n"
