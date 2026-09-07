"""T-7.1c — API key CRUD (admin-only) and key-based gateway admission."""

import pytest
from starlette.testclient import TestClient

from apps.gateway.app import create_app
from apps.gateway.tokens import issue_token
from packages.asr.null import NullASR

SECRET = "test-session-secret"


def _client(*, dev_mode: bool = False) -> TestClient:
    return TestClient(
        create_app(
            provider_factory=lambda spec: NullASR([]),
            session_secret=SECRET,
            dev_mode=dev_mode,
        )
    )


def _admin_token(c: TestClient) -> tuple[str, str]:
    r = c.post(
        "/auth/signup",
        json={"org_name": "Acme", "email": "admin@acme.co", "password": "pw-12345678"},
    )
    assert r.status_code == 201
    return r.json()["token"], r.json()["org_id"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# -- key CRUD ------------------------------------------------------------


def test_create_list_and_revoke_a_key() -> None:
    c = _client()
    token, _ = _admin_token(c)

    r = c.post("/orgs/keys", json={"name": "prod"}, headers=_auth(token))
    assert r.status_code == 201
    key = r.json()
    assert key["key"].startswith("rf_") and key["prefix"].startswith("rf_")

    listed = c.get("/orgs/keys", headers=_auth(token)).json()
    assert [k["id"] for k in listed] == [key["id"]]
    assert "key" not in listed[0] and listed[0]["revoked"] is False

    assert c.delete(f"/orgs/keys/{key['id']}", headers=_auth(token)).status_code == 204
    assert c.get("/orgs/keys", headers=_auth(token)).json()[0]["revoked"] is True
    assert c.delete("/orgs/keys/nope", headers=_auth(token)).status_code == 404


def test_key_routes_require_admin() -> None:
    c = _client()
    token, org_id = _admin_token(c)
    # forge an operator token for a real operator user in the same org
    op = c.app.state.identity.add_user(  # type: ignore[attr-defined]
        org_id=org_id, email="op@acme.co", password="pw-12345678", role="operator"
    )
    op_token = issue_token(user_id=op.id, org_id=org_id, role="operator", secret=SECRET)

    assert c.post("/orgs/keys", json={"name": "x"}).status_code == 401  # no token
    assert c.get("/orgs/keys", headers=_auth(op_token)).status_code == 403
    assert c.post("/orgs/keys", json={"name": "x"}, headers=_auth(op_token)).status_code == 403


# -- admission by key --------------------------------------------------


def test_capture_requires_a_valid_key_outside_dev_mode() -> None:
    c = _client(dev_mode=False)
    with c.websocket_connect("/ws/capture?session=s&leg=far") as ws:
        assert ws.receive_json() == {"type": "rejected", "reason": "AUTH"}
    assert c.get("/health").json()["metrics"]["rejected"]["AUTH"] == 1


def test_capture_admits_with_a_key_and_scopes_to_the_org_tenant() -> None:
    c = _client(dev_mode=False)
    token, org_id = _admin_token(c)
    plaintext = c.post("/orgs/keys", json={"name": "gw"}, headers=_auth(token)).json()["key"]

    with c.websocket_connect(f"/ws/capture?session=k1&leg=far&key={plaintext}") as ws:
        ws.send_bytes(b"\x00\x00" * 320)
    assert c.get("/health").json()["metrics"]["admitted"] == 1

    # events for this session require the same key and are scoped to the org tenant
    assert c.get("/events/k1").status_code == 401
    with c.stream("GET", f"/events/k1?key={plaintext}") as resp:
        assert resp.status_code == 200


def test_revoked_key_is_rejected() -> None:
    c = _client(dev_mode=False)
    token, _ = _admin_token(c)
    created = c.post("/orgs/keys", json={"name": "gw"}, headers=_auth(token)).json()
    c.delete(f"/orgs/keys/{created['id']}", headers=_auth(token))

    with c.websocket_connect(f"/ws/capture?session=s&leg=far&key={created['key']}") as ws:
        assert ws.receive_json() == {"type": "rejected", "reason": "AUTH"}


def test_dev_mode_still_admits_on_a_bare_tenant_param() -> None:
    c = _client(dev_mode=True)  # 'testco' -> default profile, no consent required
    with c.websocket_connect("/ws/capture?session=s&leg=far&tenant=testco") as ws:
        ws.send_bytes(b"\x00\x00" * 320)
    assert c.get("/health").json()["metrics"]["admitted"] == 1


@pytest.mark.parametrize(
    "path", ["/ws/capture?session=s&leg=far", "/ws/capture?session=s&leg=far&key=rf_bogus"]
)
def test_bad_or_missing_key_is_auth_rejected(path: str) -> None:
    c = _client(dev_mode=False)
    with c.websocket_connect(path) as ws:
        assert ws.receive_json() == {"type": "rejected", "reason": "AUTH"}
