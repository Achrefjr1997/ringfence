"""Oversight console P1 -- /calls over the gateway.

A capture session must land in the ledger keyed by the API key it was
admitted with, and /calls must scope + filter it.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from apps.gateway.app import create_app
from packages.asr.null import NullASR
from packages.calls.ledger import InMemoryCallLedger

SECRET = "test-session-secret"


@pytest.fixture()
def ledger() -> InMemoryCallLedger:
    return InMemoryCallLedger()


@pytest.fixture()
def client(ledger: InMemoryCallLedger) -> TestClient:
    return TestClient(
        create_app(
            provider_factory=lambda spec: NullASR([]),
            session_secret=SECRET,
            call_ledger=ledger,
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


def test_a_capture_session_lands_in_the_ledger_under_its_key(client: TestClient) -> None:
    token, _ = _admin(client)
    key = client.post("/orgs/keys", json={"name": "gw"}, headers=_h(token)).json()
    with client.websocket_connect(f"/ws/capture?session=c1&leg=far&key={key['key']}") as ws:
        ws.send_bytes(b"\x00\x00" * 320)

    listed = client.get("/calls", headers=_h(token)).json()
    assert len(listed) == 1
    row = listed[0]
    assert row["session_id"] == "c1"
    assert row["api_key_id"] == key["id"]
    assert row["ended_at"] is not None and row["live"] is False
    assert row["leg_count"] == 1


def test_calls_are_scoped_to_the_tenant_and_filterable_by_key(client: TestClient) -> None:
    token, _ = _admin(client)
    k1 = client.post("/orgs/keys", json={"name": "one"}, headers=_h(token)).json()
    k2 = client.post("/orgs/keys", json={"name": "two"}, headers=_h(token)).json()
    for sid, k in (("s1", k1), ("s2", k2), ("s3", k1)):
        with client.websocket_connect(f"/ws/capture?session={sid}&leg=far&key={k['key']}") as ws:
            ws.send_bytes(b"\x00\x00" * 160)

    assert {r["session_id"] for r in client.get("/calls", headers=_h(token)).json()} == {
        "s1",
        "s2",
        "s3",
    }
    by_k1 = client.get(f"/calls?key_id={k1['id']}", headers=_h(token)).json()
    assert {r["session_id"] for r in by_k1} == {"s1", "s3"}


def test_call_detail_returns_the_score_series_shape(client: TestClient) -> None:
    token, _ = _admin(client)
    key = client.post("/orgs/keys", json={"name": "gw"}, headers=_h(token)).json()
    with client.websocket_connect(f"/ws/capture?session=d1&leg=far&key={key['key']}") as ws:
        ws.send_bytes(b"\x00\x00" * 320)

    detail = client.get("/calls/d1", headers=_h(token))
    assert detail.status_code == 200
    body = detail.json()
    assert body["session_id"] == "d1"
    assert isinstance(body["scores"], list)
    assert body["has_case"] is False and body["transcript"] == []

    assert client.get("/calls/nope", headers=_h(token)).status_code == 404


def test_calls_are_attributed_to_the_employee_the_integration_names(client: TestClient) -> None:
    token, _ = _admin(client)
    key = client.post("/orgs/keys", json={"name": "gw"}, headers=_h(token)).json()
    # one call via ?user=, one via the X-RingFence-User header
    with client.websocket_connect(
        f"/ws/capture?session=u1&leg=far&key={key['key']}&user=alice@corp&user_label=Alice"
    ) as ws:
        ws.send_bytes(b"\x00\x00" * 160)
    with client.websocket_connect(
        f"/ws/capture?session=u2&leg=far&key={key['key']}",
        headers={"X-RingFence-User": "bob@corp"},
    ) as ws:
        ws.send_bytes(b"\x00\x00" * 160)

    rows = {c["session_id"]: c for c in client.get("/calls", headers=_h(token)).json()}
    assert rows["u1"]["user_ref"] == "alice@corp" and rows["u1"]["user_label"] == "Alice"
    assert rows["u2"]["user_ref"] == "bob@corp"

    only_alice = client.get("/calls?user=alice@corp", headers=_h(token)).json()
    assert {c["session_id"] for c in only_alice} == {"u1"}

    users = {u["user_ref"]: u for u in client.get("/calls/users", headers=_h(token)).json()}
    assert set(users) == {"alice@corp", "bob@corp"}
    assert users["alice@corp"]["calls"] == 1 and users["alice@corp"]["user_label"] == "Alice"


def test_calls_require_auth(client: TestClient) -> None:
    assert client.get("/calls").status_code == 401
    assert client.get("/calls/x").status_code == 401
    assert client.get("/calls/users").status_code == 401


def _one_call(client: TestClient, token: str, sid: str = "cm1") -> None:
    key = client.post("/orgs/keys", json={"name": "gw"}, headers=_h(token)).json()["key"]
    with client.websocket_connect(f"/ws/capture?session={sid}&leg=far&key={key}") as ws:
        ws.send_bytes(b"\x00\x00" * 160)


def test_comment_thread_lifecycle(client: TestClient) -> None:
    token, _ = _admin(client)
    _one_call(client, token)

    r = client.post(
        "/calls/cm1/comments",
        json={"body": "check 0:12", "t_seconds": 12, "visibility": "org"},
        headers=_h(token),
    )
    assert r.status_code == 201
    top = r.json()
    assert top["t_seconds"] == 12.0 and top["author_email"] == "a@acme.co"

    rep = client.post(
        "/calls/cm1/comments",
        json={"body": "agreed", "parent_id": top["id"]},
        headers=_h(token),
    )
    assert rep.status_code == 201 and rep.json()["parent_id"] == top["id"]

    listed = client.get("/calls/cm1/comments", headers=_h(token)).json()
    assert [c["body"] for c in listed] == ["check 0:12", "agreed"]

    ed = client.patch(
        f"/calls/cm1/comments/{top['id']}",
        json={"body": "check 0:12 — scam script"},
        headers=_h(token),
    )
    assert ed.status_code == 200 and ed.json()["edited_at"] is not None

    res = client.post(f"/calls/cm1/comments/{top['id']}/resolve", json={}, headers=_h(token))
    assert res.status_code == 200 and res.json()["resolved_at"] is not None

    d = client.delete(f"/calls/cm1/comments/{rep.json()['id']}", headers=_h(token))
    assert d.status_code == 204
    assert [c["id"] for c in client.get("/calls/cm1/comments", headers=_h(token)).json()] == [
        top["id"]
    ]


def test_comment_on_unknown_call_is_404(client: TestClient) -> None:
    token, _ = _admin(client)
    assert client.get("/calls/ghost/comments", headers=_h(token)).status_code == 404
    assert (
        client.post("/calls/ghost/comments", json={"body": "x"}, headers=_h(token)).status_code
        == 404
    )


def test_comment_endpoints_need_auth(client: TestClient) -> None:
    assert client.get("/calls/x/comments").status_code == 401
    assert client.post("/calls/x/comments", json={"body": "x"}).status_code == 401


def test_empty_comment_body_is_400(client: TestClient) -> None:
    token, _ = _admin(client)
    _one_call(client, token, sid="cm2")
    assert (
        client.post("/calls/cm2/comments", json={"body": "  "}, headers=_h(token)).status_code
        == 400
    )


def test_bad_state_filter_is_rejected(client: TestClient) -> None:
    token, _ = _admin(client)
    assert client.get("/calls?state=NOPE", headers=_h(token)).status_code == 400
