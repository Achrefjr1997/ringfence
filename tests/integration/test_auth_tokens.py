"""T-7.1d -- verification, password reset and invite flows over the gateway."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from apps.gateway.app import create_app
from packages.asr.null import NullASR

SECRET = "test-session-secret"


def _client() -> TestClient:
    return TestClient(create_app(provider_factory=lambda spec: NullASR([]), session_secret=SECRET))


def _signup(c: TestClient, email: str = "admin@acme.co", pw: str = "pw-12345678") -> dict:
    r = c.post("/auth/signup", json={"org_name": "Acme", "email": email, "password": pw})
    assert r.status_code == 201
    return r.json()  # type: ignore[no-any-return]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# -- RF_SESSION_SECRET enforcement -----------------------------------


def test_create_app_refuses_to_start_without_a_secret_outside_dev_mode() -> None:
    with pytest.raises(RuntimeError, match="RF_SESSION_SECRET"):
        create_app(provider_factory=lambda spec: NullASR([]), session_secret=None, dev_mode=False)
    # dev_mode still tolerates it
    create_app(provider_factory=lambda spec: NullASR([]), session_secret=None, dev_mode=True)


# -- email verification --------------------------------------------


def test_verify_request_then_confirm_flips_verified() -> None:
    c = _client()
    admin = _signup(c)  # admin starts verified; make an unverified operator via invite
    inv = c.post(
        "/orgs/users",
        json={"email": "op@acme.co", "role": "operator"},
        headers=_auth(admin["token"]),
    ).json()
    accepted = c.post(
        "/auth/accept", json={"token": inv["invite_token"], "password": "pw-op-123456"}
    ).json()
    # accept already verifies; re-run the explicit verify flow to prove it works
    tok = c.get("/auth/whoami", headers=_auth(accepted["token"]))
    assert tok.json()["verified"] is True

    req = c.post("/auth/verify/request", headers=_auth(accepted["token"]))
    assert req.status_code == 200 and "token" in req.json()
    ok = c.post("/auth/verify/confirm", json={"token": req.json()["token"]})
    assert ok.status_code == 200 and ok.json()["verified"] is True

    assert c.post("/auth/verify/confirm", json={"token": "garbage"}).status_code == 400
    assert c.post("/auth/verify/request").status_code == 401  # needs auth


# -- password reset ---------------------------------------------


def test_reset_changes_the_password_and_the_token_is_single_use() -> None:
    c = _client()
    _signup(c, pw="pw-original-1")

    r = c.post("/auth/reset/request", json={"email": "admin@acme.co"})
    assert r.status_code == 202
    token = r.json()["token"]

    done = c.post("/auth/reset/confirm", json={"token": token, "password": "pw-brand-new-9"})
    assert done.status_code == 200

    assert (
        c.post(
            "/auth/login", json={"email": "admin@acme.co", "password": "pw-original-1"}
        ).status_code
        == 401
    )
    assert (
        c.post(
            "/auth/login", json={"email": "admin@acme.co", "password": "pw-brand-new-9"}
        ).status_code
        == 200
    )

    # the spent token no longer works: its bind is stale
    again = c.post("/auth/reset/confirm", json={"token": token, "password": "pw-third-try-7"})
    assert again.status_code == 400


def test_reset_request_for_an_unknown_email_is_202_with_no_token() -> None:
    c = _client()
    r = c.post("/auth/reset/request", json={"email": "nobody@nowhere.co"})
    assert r.status_code == 202 and "token" not in r.json()


def test_reset_confirm_rejects_a_too_short_password() -> None:
    c = _client()
    _signup(c)
    token = c.post("/auth/reset/request", json={"email": "admin@acme.co"}).json()["token"]
    assert c.post("/auth/reset/confirm", json={"token": token, "password": ""}).status_code == 400


# -- invites --------------------------------------------------------


def test_admin_invites_a_guardian_who_accepts_and_is_logged_in() -> None:
    c = _client()
    admin = _signup(c)

    inv = c.post(
        "/orgs/users",
        json={"email": "guard@acme.co", "role": "guardian"},
        headers=_auth(admin["token"]),
    )
    assert inv.status_code == 201
    body = inv.json()
    assert body["role"] == "guardian" and "invite_token" in body

    acc = c.post("/auth/accept", json={"token": body["invite_token"], "password": "pw-guard-123"})
    assert acc.status_code == 201
    assert acc.json()["role"] == "guardian" and acc.json()["verified"] is True

    # logged straight in
    who = c.get("/auth/whoami", headers=_auth(acc.json()["token"]))
    assert who.json()["email"] == "guard@acme.co"
    # invite token is single-use
    assert (
        c.post("/auth/accept", json={"token": body["invite_token"], "password": "pw-x"}).status_code
        == 400
    )


def test_orgs_users_is_admin_only_and_lists_the_org() -> None:
    c = _client()
    admin = _signup(c)
    c.post(
        "/orgs/users",
        json={"email": "op@acme.co", "role": "operator"},
        headers=_auth(admin["token"]),
    )
    listed = c.get("/orgs/users", headers=_auth(admin["token"]))
    assert listed.status_code == 200
    emails = {u["email"] for u in listed.json()}
    assert emails == {"admin@acme.co", "op@acme.co"}

    assert c.get("/orgs/users").status_code == 401
    assert c.post("/orgs/users", json={"email": "x@acme.co", "role": "operator"}).status_code == 401


def test_invite_rejects_an_unknown_role() -> None:
    c = _client()
    admin = _signup(c)
    r = c.post(
        "/orgs/users", json={"email": "x@acme.co", "role": "admin"}, headers=_auth(admin["token"])
    )
    assert r.status_code == 400
