"""T-7.1d — role + tenant enforcement on the case routes."""

from starlette.testclient import TestClient

from apps.gateway.app import create_app
from apps.gateway.tokens import issue_token
from packages.asr.null import NullASR
from packages.eval.fixtures import fixture_turns, load_fixture

SECRET = "test-session-secret"
FX = load_fixture("fx_tech_support_en_001")  # fraud -> opens an ALERT case


def _client() -> TestClient:
    return TestClient(
        create_app(
            provider_factory=lambda spec: NullASR(fixture_turns(FX), speed=400.0),
            session_secret=SECRET,
            dev_mode=False,
        )
    )


def _signup(c: TestClient, email: str, org: str) -> tuple[str, str]:
    r = c.post("/auth/signup", json={"org_name": org, "email": email, "password": "pw-12345678"})
    assert r.status_code == 201
    return r.json()["token"], r.json()["org_id"]


def _member(c: TestClient, org_id: str, email: str, role: str) -> str:
    user = c.app.state.identity.add_user(  # type: ignore[attr-defined]
        org_id=org_id, email=email, password="pw-12345678", role=role
    )
    return issue_token(user_id=user.id, org_id=org_id, role=role, secret=SECRET)


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _setup() -> tuple[TestClient, dict[str, str]]:
    c = _client()
    _, org_a = _signup(c, "admin@a.co", "OrgA")
    admin_b, _ = _signup(c, "admin@b.co", "OrgB")
    tokens = {
        "op_a": _member(c, org_a, "op@a.co", "operator"),
        "guard_a": _member(c, org_a, "guard@a.co", "guardian"),
        "admin_b": admin_b,
    }
    r = c.post(f"/replay/fx_tech_support_en_001?session=caseA&tenant={org_a}&speed=400")
    assert r.status_code == 200
    return c, tokens


def test_list_is_scoped_to_the_callers_org() -> None:
    c, t = _setup()
    assert c.get("/cases").status_code == 401  # no token
    assert [x["session_id"] for x in c.get("/cases", headers=_hdr(t["op_a"])).json()] == ["caseA"]
    assert c.get("/cases", headers=_hdr(t["admin_b"])).json() == []  # OrgB has no cases


def test_operator_sees_the_full_case_guardian_sees_no_transcript() -> None:
    c, t = _setup()

    full = c.get("/cases/caseA", headers=_hdr(t["op_a"]))
    assert full.status_code == 200
    assert full.json()["transcript"] and full.json()["decisions"]

    verdict = c.get("/cases/caseA", headers=_hdr(t["guard_a"]))
    assert verdict.status_code == 200
    assert "transcript" not in verdict.json()
    assert verdict.json()["decisions"]  # the chain is still there


def test_cross_org_read_is_404_not_403() -> None:
    c, t = _setup()
    assert c.get("/cases/caseA", headers=_hdr(t["admin_b"])).status_code == 404


def test_feedback_requires_operator_and_the_right_org() -> None:
    c, t = _setup()
    assert (
        c.post(
            "/cases/caseA/feedback", json={"label": "fraud"}, headers=_hdr(t["guard_a"])
        ).status_code
        == 403
    )
    assert (
        c.post(
            "/cases/caseA/feedback", json={"label": "benign"}, headers=_hdr(t["admin_b"])
        ).status_code
        == 404
    )

    ok = c.post("/cases/caseA/feedback", json={"label": "fraud"}, headers=_hdr(t["op_a"]))
    assert ok.status_code == 200 and ok.json()["feedback"] == "fraud"
