"""POST /replay must be scoped to the caller's own tenant.

The route publishes to ``rf.<tenant>.*``, writes cases, and can drive that
tenant's guardian webhook.  It used to take ``?tenant=`` from the query
string with no auth at all, so an anonymous caller could pick a real
customer's tenant and inject fabricated INTERVENE decisions into their bus,
their case store and their notifications.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from apps.gateway.app import create_app
from packages.asr.null import NullASR
from packages.contracts.events import InProcessBus
from packages.intervene.cases import InMemoryCaseStore

SECRET = "test-session-secret"
FIXTURE = "fx_gift_card_en_001"


@pytest.fixture()
def bus() -> InProcessBus:
    return InProcessBus()


@pytest.fixture()
def client(bus: InProcessBus) -> TestClient:
    return TestClient(
        create_app(
            provider_factory=lambda spec: NullASR([]),
            session_secret=SECRET,
            bus=bus,
            case_store=InMemoryCaseStore(),
        )
    )


def _org(c: TestClient, org: str, email: str) -> tuple[str, str, str]:
    """(jwt, api_key, tenant) for a fresh org.  Tenant is the org id."""
    r = c.post(
        "/auth/signup",
        json={"org_name": org, "email": email, "password": "pw-12345678"},
    )
    assert r.status_code == 201
    token, org_id = str(r.json()["token"]), str(r.json()["org_id"])
    made = c.post("/orgs/keys", json={"name": "k"}, headers={"Authorization": f"Bearer {token}"})
    assert made.status_code in (200, 201)
    return token, str(made.json()["key"]), org_id


def test_replay_without_a_key_is_rejected(client: TestClient) -> None:
    r = client.post(f"/replay/{FIXTURE}?session=x&tenant=acme&speed=64")
    assert r.status_code == 401


def test_replay_ignores_the_tenant_query_param(client: TestClient) -> None:
    """A key for org Alpha must not be able to publish into org Victim.

    The replay opens ALERT+ cases; those land in the case store under the
    tenant it ran as.  So: run it with Alpha's key while asking for
    Victim's tenant, then check whose case store it landed in.
    """
    _alpha_jwt, alpha_key, _alpha_tenant = _org(client, "Alpha", "a@alpha.co")
    victim_jwt, _victim_key, victim_tenant = _org(client, "Victim", "v@victim.co")

    # The victim's REAL tenant scope -- an org's tenant is its id.
    r = client.post(
        f"/replay/{FIXTURE}?session=spoof1&tenant={victim_tenant}&speed=64",
        headers={"Authorization": f"Bearer {alpha_key}"},
    )
    assert r.status_code == 200 and r.json()["decisions"] >= 1

    victim_cases = client.get("/cases", headers={"Authorization": f"Bearer {victim_jwt}"})
    assert victim_cases.status_code == 200
    assert victim_cases.json() == [], "replay leaked into the spoofed tenant's case store"


def test_replay_with_a_valid_key_still_works(client: TestClient) -> None:
    _jwt, key, _t = _org(client, "Alpha", "a2@alpha.co")
    r = client.post(
        f"/replay/{FIXTURE}?session=ok1&speed=64",
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 200
    assert r.json()["decisions"] >= 1


def test_replay_accepts_the_key_as_a_query_param_too(client: TestClient) -> None:
    _jwt, key, _t = _org(client, "Alpha", "a3@alpha.co")
    r = client.post(f"/replay/{FIXTURE}?session=ok2&speed=64&key={key}")
    assert r.status_code == 200
