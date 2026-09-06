"""T-4.4 — console case view and the feedback control."""

import pytest
from starlette.testclient import TestClient

from apps.gateway.app import create_app
from packages.asr.null import NullASR
from packages.contracts.events import InProcessBus
from packages.eval.fixtures import fixture_turns, load_fixture


def _client(fixture_id: str) -> TestClient:
    fx = load_fixture(fixture_id)
    app = create_app(
        provider_factory=lambda spec: NullASR(fixture_turns(fx), speed=400.0),
        bus=InProcessBus(),
    )
    return TestClient(app)


def _open_case(client: TestClient, fixture_id: str, session: str = "c1") -> None:
    r = client.post(f"/replay/{fixture_id}?session={session}&speed=400")
    assert r.status_code == 200


def test_alert_fixture_opens_a_case_with_full_chain() -> None:
    client = _client("fx_tech_support_en_001")
    _open_case(client, "fx_tech_support_en_001")

    listing = client.get("/cases").json()
    assert [c["session_id"] for c in listing] == ["c1"]
    assert listing[0]["peak_state"] in {"ALERT", "INTERVENE"}
    assert listing[0]["feedback"] is None

    case = client.get("/cases/c1").json()
    assert case["decisions"], "decision chain is empty"
    states = [d["state"] for d in case["decisions"]]
    assert "ALERT" in states
    assert all("contributions" in d for d in case["decisions"])
    assert any(d["contributions"] for d in case["decisions"])
    assert any(d["counterfactual"] for d in case["decisions"])
    assert case["transcript"] and case["transcript"][0]["role"] in {"CALLER", "CALLEE"}


def test_benign_fixture_opens_no_case() -> None:
    client = _client("fx_delivery_legit_en_001")
    _open_case(client, "fx_delivery_legit_en_001")
    assert client.get("/cases").json() == []
    assert client.get("/cases/c1").status_code == 404


@pytest.mark.parametrize("label", ["fraud", "benign", "unclear"])
def test_feedback_is_recorded_and_read_back(label: str) -> None:
    client = _client("fx_gift_card_en_001")
    _open_case(client, "fx_gift_card_en_001")

    r = client.post("/cases/c1/feedback", json={"label": label, "note": "reviewed"})
    assert r.status_code == 200
    assert r.json()["feedback"] == label

    reread = client.get("/cases/c1").json()
    assert reread["feedback"] == label
    assert reread["feedback_note"] == "reviewed"
    assert (
        next(c for c in client.get("/cases").json() if c["session_id"] == "c1")["feedback"] == label
    )


def test_feedback_rejects_bad_label_and_unknown_case() -> None:
    client = _client("fx_gift_card_en_001")
    _open_case(client, "fx_gift_card_en_001")

    assert client.post("/cases/c1/feedback", json={"label": "spam"}).status_code == 400
    assert client.post("/cases/nope/feedback", json={"label": "fraud"}).status_code == 404


def test_case_view_page_is_served() -> None:
    client = _client("fx_gift_card_en_001")
    body = client.get("/case.html").text
    assert 'data-label="fraud"' in body and "Decision chain" in body
