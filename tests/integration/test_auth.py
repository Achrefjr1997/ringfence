"""T-7.1b — signup / login / whoami over the gateway."""

import pytest
from starlette.testclient import TestClient

from apps.gateway.app import create_app
from apps.gateway.tokens import issue_token
from packages.asr.null import NullASR

SECRET = "test-session-secret"


def _client() -> TestClient:
    return TestClient(
        create_app(provider_factory=lambda spec: NullASR([]), session_secret=SECRET)
    )


def _signup(c: TestClient, **over: str) -> dict:
    body = {"org_name": "Acme", "email": "admin@acme.co", "password": "pw-12345678", **over}
    return c.post("/auth/signup", json=body)  # type: ignore[return-value]


def test_signup_creates_admin_and_a_working_token() -> None:
    c = _client()
    r = _signup(c)
    assert r.status_code == 201
    b = r.json()
    assert b["role"] == "admin" and b["verified"] is True and b["email"] == "admin@acme.co"

    w = c.get("/auth/whoami", headers={"Authorization": f"Bearer {b['token']}"})
    assert w.status_code == 200
    assert w.json()["email"] == "admin@acme.co" and w.json()["org_id"] == b["org_id"]


def test_signup_duplicate_email_is_409() -> None:
    c = _client()
    assert _signup(c, email="dup@acme.co").status_code == 201
    assert _signup(c, email="dup@acme.co", org_name="Other").status_code == 409


@pytest.mark.parametrize(
    "over",
    [
        {"org_name": ""},
        {"email": "not-an-email"},
        {"password": ""},
    ],
)
def test_signup_bad_input_is_400(over: dict) -> None:
    assert _signup(_client(), **over).status_code == 400


def test_login_ok_wrong_password_and_unknown_email() -> None:
    c = _client()
    _signup(c, email="l@acme.co")
    assert c.post("/auth/login", json={"email": "l@acme.co", "password": "pw-12345678"}).status_code == 200
    assert c.post("/auth/login", json={"email": "l@acme.co", "password": "nope"}).status_code == 401
    assert c.post("/auth/login", json={"email": "ghost@acme.co", "password": "x"}).status_code == 401


def test_whoami_needs_a_valid_bearer_token() -> None:
    c = _client()
    _signup(c)
    assert c.get("/auth/whoami").status_code == 401
    assert c.get("/auth/whoami", headers={"Authorization": "Bearer garbage"}).status_code == 401
    assert c.get("/auth/whoami", headers={"Authorization": "Basic abc"}).status_code == 401


def test_expired_and_foreign_secret_tokens_are_rejected() -> None:
    c = _client()
    _signup(c)
    dead = issue_token(user_id="x", org_id="y", role="admin", secret=SECRET, ttl_s=-1.0)
    foreign = issue_token(user_id="x", org_id="y", role="admin", secret="different-secret")
    assert c.get("/auth/whoami", headers={"Authorization": f"Bearer {dead}"}).status_code == 401
    assert c.get("/auth/whoami", headers={"Authorization": f"Bearer {foreign}"}).status_code == 401


def test_token_for_a_deleted_user_does_not_resolve() -> None:
    c = _client()
    token = issue_token(user_id="never-existed", org_id="o", role="admin", secret=SECRET)
    assert c.get("/auth/whoami", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_logout_is_204() -> None:
    assert _client().post("/auth/logout").status_code == 204
