"""Verification counters and their Prometheus exposition."""

from __future__ import annotations

from packages.contracts.verify import VerificationOutcome
from packages.obs.metrics import MetricsSnapshot, prometheus_text
from packages.verify.stats import VerificationStats


def _stats() -> VerificationStats:
    s = VerificationStats()
    s.record(VerificationOutcome(verified=False, reason="no record", duration_s=31.5))
    s.record(VerificationOutcome(verified=True, reason="yes", duration_s=20.0))
    s.record(VerificationOutcome(verified=None, reason="x", error="connect:ConnectionRefusedError"))
    s.record(VerificationOutcome(verified=False, reason="sim", duration_s=2.0, simulated=True))
    s.record(None)
    s.skip("no_institution")
    s.skip("no_institution")
    return s


def test_outcomes_are_split_by_kind_and_simulation() -> None:
    s = _stats()
    assert s.outcomes[("unconfirmed", False)] == 1
    assert s.outcomes[("confirmed", False)] == 1
    assert s.outcomes[("unanswered", False)] == 1
    assert s.outcomes[("unconfirmed", True)] == 1
    assert s.outcomes[("failed", False)] == 1
    assert s.skipped["no_institution"] == 2


def test_error_labels_keep_only_their_prefix() -> None:
    """No free text becomes a time series."""
    assert dict(_stats().errors) == {"connect": 1}


def test_simulated_checks_add_no_agent_seconds() -> None:
    assert _stats().agent_seconds == 51.5


def test_prometheus_exposes_the_verification_series() -> None:
    body = prometheus_text(MetricsSnapshot(active=0, capacity=1, admitted=0, verify=_stats()))
    assert 'ringfence_verifications_total{outcome="unconfirmed",simulated="false"} 1' in body
    assert 'ringfence_verifications_total{outcome="unconfirmed",simulated="true"} 1' in body
    assert 'ringfence_verifications_skipped_total{reason="no_institution"} 2' in body
    assert 'ringfence_verification_errors_total{error="connect"} 1' in body
    assert "ringfence_verification_agent_seconds_total 51.50" in body


def test_no_verification_series_when_verification_is_off() -> None:
    body = prometheus_text(MetricsSnapshot(active=0, capacity=1, admitted=0))
    assert "verification" not in body
