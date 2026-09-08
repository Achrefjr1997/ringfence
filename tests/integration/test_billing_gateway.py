"""T-7.3 -- /usage, /orgs/plan and BILLING admission over the gateway."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from apps.gateway.app import create_app
from packages.asr.null import NullASR
from packages.billing.meter import InMemoryBillingStore

SECRET = "test-session-secret"


def _client(store: InMemoryBillingStore, *, dev_mode: bool = False) -> TestClient:
    return TestClient(
        create_app(
            provider_factory=lambda spec: NullASR([]),
            session_secret=SECRET,
            billing_store=store,
            dev_mode=dev_mode,
        )
    )


def _admin(c: TestClient) -> tuple[str, str]:
    r = c.post(
        "/auth/signup",
        json={"org_name": "Acme", "email": "a@acme.co", "password": "pw-12345678"},
    )
    assert r.status_code == 201
    return r.json()["token"], r.json()["org_id"]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_usage_reports_the_current_period(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryBillingStore()
    c = _client(store)
    token, org_id = _admin(c)
    store.record(org_id, "call_minutes", 42.0)
    store.record(org_id, "calls", 3)

    u = c.get("/usage", headers=_h(token))
    assert u.status_code == 200
    body = u.json()
    assert body["plan"] == "pilot" and body["call_minutes"] == 42.0 and body["calls"] == 3
    assert body["over_hard_cap"] is False
    assert c.get("/usage").status_code == 401  # needs auth


def test_set_plan_switches_the_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryBillingStore()
    c = _client(store)
    token, org_id = _admin(c)

    r = c.post("/orgs/plan", json={"plan": "growth"}, headers=_h(token))
    assert r.status_code == 200 and r.json()["plan"] == "growth"
    assert store.plan_id(org_id) == "growth"

    assert c.post("/orgs/plan", json={"plan": "ghost"}, headers=_h(token)).status_code == 400
    assert c.post("/orgs/plan", json={"plan": "growth"}).status_code == 401


def test_hard_capped_plan_rejects_admission_with_reason_billing() -> None:
    store = InMemoryBillingStore()
    c = _client(store)
    token, org_id = _admin(c)
    key = c.post("/orgs/keys", json={"name": "gw"}, headers=_h(token)).json()["key"]

    # under the pilot allowance (600 min): admitted
    with c.websocket_connect(f"/ws/capture?session=ok&leg=far&key={key}") as ws:
        ws.send_bytes(b"\x00\x00" * 320)
    assert c.get("/health").json()["metrics"]["admitted"] == 1

    # blow past it: next admission is refused
    store.record(org_id, "call_minutes", 1000)
    with c.websocket_connect(f"/ws/capture?session=no&leg=far&key={key}") as ws:
        assert ws.receive_json() == {"type": "rejected", "reason": "BILLING"}
    assert c.get("/health").json()["metrics"]["rejected"]["BILLING"] == 1


def test_a_metered_plan_is_never_billing_blocked() -> None:
    store = InMemoryBillingStore()
    c = _client(store)
    token, org_id = _admin(c)
    key = c.post("/orgs/keys", json={"name": "gw"}, headers=_h(token)).json()["key"]
    c.post("/orgs/plan", json={"plan": "starter"}, headers=_h(token))
    store.record(org_id, "call_minutes", 999_999)

    with c.websocket_connect(f"/ws/capture?session=s&leg=far&key={key}") as ws:
        ws.send_bytes(b"\x00\x00" * 320)
    assert c.get("/health").json()["metrics"]["admitted"] == 1
