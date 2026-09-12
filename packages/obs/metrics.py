"""Prometheus text exposition for the gateway (T-7.5).

Kept free of gateway imports so it stays unit-testable: the endpoint hands
it a plain :class:`MetricsSnapshot` and gets back the ``text/plain;
version=0.0.4`` body.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from packages.risk.judge import JudgeStats
from packages.verify.stats import VerificationStats

_PREFIX = "ringfence"


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    active: int
    capacity: int
    admitted: int
    rejected: Mapping[str, int] = field(default_factory=dict)
    active_by_tenant: Mapping[str, int] = field(default_factory=dict)
    asr_breakers: Mapping[str, str] = field(default_factory=dict)  # provider -> state
    judge: JudgeStats | None = None  # None when the Tier-2 judge is not configured
    verify: VerificationStats | None = None  # None when RF_VERIFY_ENABLED is off


def _escape(label_value: str) -> str:
    return label_value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _block(name: str, kind: str, help_text: str, lines: list[str]) -> list[str]:
    out = [f"# HELP {_PREFIX}_{name} {help_text}", f"# TYPE {_PREFIX}_{name} {kind}"]
    out.extend(f"{_PREFIX}_{name}{line}" for line in lines)
    return out


def _judge_blocks(stats: JudgeStats) -> list[str]:
    """The Tier-2 judge degrades silently -- a timeout returns adjustment=0
    and the call proceeds rules-only.  Without these, "contributed nothing"
    and "said benign" are indistinguishable from outside."""
    out = _block(
        "judge_calls_total", "counter", "Judge evaluations attempted.", [f" {stats.calls}"]
    )
    out += _block(
        "judge_misses_total",
        "counter",
        "Judge evaluations that contributed nothing, by reason.",
        [f'{{reason="{_escape(r)}"}} {n}' for r, n in sorted(stats.misses.items())],
    )
    cumulative = 0
    lines: list[str] = []
    for edge in sorted(stats.buckets):
        cumulative = stats.buckets[edge]
        lines.append(f'_bucket{{le="{edge}"}} {cumulative}')
    lines.append(f'_bucket{{le="+Inf"}} {stats.latency_count}')
    lines.append(f"_sum {stats.latency_sum_s:.4f}")
    lines.append(f"_count {stats.latency_count}")
    out += [
        f"# HELP {_PREFIX}_judge_latency_seconds Judge round-trip latency.",
        f"# TYPE {_PREFIX}_judge_latency_seconds histogram",
        *(f"{_PREFIX}_judge_latency_seconds{line}" for line in lines),
    ]
    return out


def _verify_blocks(v: VerificationStats) -> list[str]:
    """Only two of a verification's endings cost money, and they look like
    the other three from outside -- see packages/verify/stats.py."""
    out = _block(
        "verifications_total",
        "counter",
        "Verifications that reached the verifier, by outcome.",
        [
            f'{{outcome="{_escape(o)}",simulated="{str(sim).lower()}"}} {n}'
            for (o, sim), n in sorted(v.outcomes.items())
        ],
    )
    out += _block(
        "verifications_skipped_total",
        "counter",
        "INTERVENE decisions that did not verify, by reason.",
        [f'{{reason="{_escape(r)}"}} {n}' for r, n in sorted(v.skipped.items())],
    )
    out += _block(
        "verification_errors_total",
        "counter",
        "Verifications that ended without an answer, by cause.",
        [f'{{error="{_escape(e)}"}} {n}' for e, n in sorted(v.errors.items())],
    )
    out += _block(
        "verification_agent_seconds_total",
        "counter",
        "Real Voice Agent session time in seconds (billed; simulated checks excluded).",
        [f" {v.agent_seconds:.2f}"],
    )
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
    if snap.judge is not None:
        out += _judge_blocks(snap.judge)
    if snap.verify is not None:
        out += _verify_blocks(snap.verify)
    return "\n".join(out) + "\n"
