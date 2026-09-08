"""T-7.5 -- GET /metrics over the gateway."""

from __future__ import annotations

from starlette.testclient import TestClient

from apps.gateway.app import create_app
from packages.asr.null import NullASR

SECRET = "test-session-secret"


def _client(*, dev_mode: bool = True) -> TestClient:
    return TestClient(
        create_app(
            provider_factory=lambda spec: NullASR([]),
            session_secret=SECRET,
            dev_mode=dev_mode,
        )
    )


def test_metrics_is_prometheus_text_and_starts_empty() -> None:
    c = _client()
    r = c.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "ringfence_sessions_active 0" in r.text
    assert "ringfence_admitted_total 0" in r.text


def test_admission_moves_the_counters_and_tenant_gauge() -> None:
    c = _client()
    with c.websocket_connect("/ws/capture?session=m1&leg=far&tenant=testco") as ws:
        ws.send_bytes(b"\x00\x00" * 320)
        body = c.get("/metrics").text
        assert "ringfence_admitted_total 1" in body
        assert 'ringfence_tenant_sessions_active{tenant="testco"} 1' in body

    # session closed on disconnect -> gauge falls, counter stays
    body = c.get("/metrics").text
    assert "ringfence_sessions_active 0" in body
    assert "ringfence_admitted_total 1" in body


def test_rejections_are_counted() -> None:
    c = _client(dev_mode=False)
    with c.websocket_connect("/ws/capture?session=x&leg=far") as ws:
        assert ws.receive_json()["reason"] == "AUTH"
    assert 'ringfence_rejected_total{reason="AUTH"} 1' in c.get("/metrics").text
