"""T-7.5 -- Prometheus text exposition."""

from __future__ import annotations

from packages.obs.metrics import MetricsSnapshot, prometheus_text


def _lines(snap: MetricsSnapshot) -> list[str]:
    return prometheus_text(snap).splitlines()


def test_core_series_are_always_present() -> None:
    body = prometheus_text(MetricsSnapshot(active=3, capacity=500, admitted=42))
    assert "ringfence_sessions_active 3" in body
    assert "ringfence_session_capacity 500" in body
    assert "ringfence_admitted_total 42" in body
    assert "# TYPE ringfence_admitted_total counter" in body
    assert body.endswith("\n")


def test_rejected_reasons_are_labelled_and_sorted() -> None:
    body = prometheus_text(
        MetricsSnapshot(active=0, capacity=1, admitted=0, rejected={"QUOTA": 1, "AUTH": 5})
    )
    auth = body.index('ringfence_rejected_total{reason="AUTH"} 5')
    quota = body.index('ringfence_rejected_total{reason="QUOTA"} 1')
    assert auth < quota  # sorted


def test_tenant_and_breaker_blocks_are_omitted_when_empty() -> None:
    body = prometheus_text(MetricsSnapshot(active=0, capacity=1, admitted=0))
    assert "tenant_sessions_active" not in body
    assert "asr_breaker_state" not in body


def test_breaker_block_marks_the_live_state() -> None:
    body = prometheus_text(
        MetricsSnapshot(active=0, capacity=1, admitted=0, asr_breakers={"assemblyai": "open"})
    )
    assert 'ringfence_asr_breaker_state{provider="assemblyai",state="open"} 1' in body
    assert 'ringfence_asr_breaker_state{provider="assemblyai",state="closed"} 0' in body


def test_label_values_are_escaped() -> None:
    body = prometheus_text(
        MetricsSnapshot(active=1, capacity=1, admitted=1, active_by_tenant={'a"b\\c': 1})
    )
    assert r'ringfence_tenant_sessions_active{tenant="a\"b\\c"} 1' in body
