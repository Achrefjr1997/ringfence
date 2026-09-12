"""``case_access`` used to short-circuit to "no auth, everything visible"
the instant ``dev_mode`` was on, *even with a real, valid session on the
request*.  In a docker-compose deployment that runs a real identity
backend with ``RF_DEV_MODE=1`` (the default -- it exists to relax
``/ws/capture`` admission, not to disable the console's own auth), that
meant:

* a logged-in admin's own ``/calls`` request resolved no tenant at all
  ("tenant unresolved", 400) because the endpoint only ever looked for a
  session-less ``?tenant=`` query param;
* had it not 400'd, ``/cases`` would have shown every org's cases to
  anyone who happened to be logged in, and any role would have passed the
  ``_CASE_ROLES`` gate -- both silently, since dev_mode never even called
  ``authenticate()``.

A real session must always win when one is presented.  dev_mode's
permissive fallback is for the case *nobody is logged in at all* -- a bare
curl in a pure local demo -- not a substitute for checking the session
that is actually there.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from apps.gateway.app import create_app
from packages.asr.null import NullASR
from packages.eval.fixtures import fixture_turns, load_fixture

SECRET = "test-session-secret"
FX = load_fixture("fx_tech_support_en_001")  # fraud -> opens an ALERT case


def _client() -> TestClient:
    return TestClient(
        create_app(
            provider_factory=lambda spec: NullASR(fixture_turns(FX), speed=400.0),
            session_secret=SECRET,
            dev_mode=True,
        )
    )


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _signup(c: TestClient, email: str, org: str) -> tuple[str, str]:
    r = c.post("/auth/signup", json={"org_name": org, "email": email, "password": "pw-12345678"})
    assert r.status_code == 201
    return r.json()["token"], r.json()["org_id"]


def test_no_session_still_gets_the_old_unscoped_dev_behaviour() -> None:
    """The one thing that must not regress: a bare request with no token
    at all, in dev_mode, is still treated as a full-access dev viewer.
    /cases has no tenant of its own to require and returns everything;
    /calls always needed *some* tenant scope to query the ledger with, so
    anonymous dev access still needs ?tenant= -- that part is unchanged,
    only the "a real session is ignored" bug is what this file is about."""
    c = _client()
    assert c.get("/cases").status_code == 200
    assert c.get("/calls?tenant=whatever").status_code == 200


def test_a_real_session_resolves_its_own_tenant_without_a_query_param() -> None:
    """This is the exact failure the user hit: a logged-in admin's /calls
    request 400'd with "tenant unresolved" because the endpoint never
    looked at the session, only at ?tenant=."""
    c = _client()
    token, _org = _signup(c, "admin@meridian.example", "Meridian")
    r = c.get("/calls", headers=_hdr(token))
    assert r.status_code == 200


def test_a_real_session_is_scoped_to_its_own_org_not_every_org() -> None:
    """The silent half of the bug: without this fix, dev_mode showed every
    org's cases to any logged-in user, because case_access() never learned
    who they were."""
    c = _client()
    token_a, org_a = _signup(c, "admin@a.example", "OrgA")
    token_b, _org_b = _signup(c, "admin@b.example", "OrgB")

    key = c.post("/orgs/keys", json={"name": "replay"}, headers=_hdr(token_a)).json()["key"]
    # read_tenant() (a separate function, unaffected by this fix) resolves
    # dev_mode's tenant from ?tenant=, not from ?key= -- see its docstring.
    r = c.post(f"/replay/fx_tech_support_en_001?session=caseA&speed=400&key={key}&tenant={org_a}")
    assert r.status_code == 200

    assert [x["session_id"] for x in c.get("/cases", headers=_hdr(token_a)).json()] == ["caseA"]
    assert c.get("/cases", headers=_hdr(token_b)).json() == []  # not OrgA's case


def test_an_invalid_bearer_token_still_falls_back_to_dev_access_not_401() -> None:
    """A stray or expired token from an earlier run must not turn dev_mode
    into a hard lockout -- authenticate() returns None for it exactly as it
    would for no token at all, and dev_mode's fallback applies the same
    way."""
    c = _client()
    r = c.get("/cases", headers=_hdr("garbage.not-a-real-token"))
    assert r.status_code == 200
