"""T-3.3 — ingest gateway.  Uses a NullASR provider so the WS -> pipeline
-> bus -> SSE path runs with no network."""

import json

import pytest
from starlette.testclient import TestClient

from apps.gateway.app import _default_judge_factory, create_app
from packages.asr.null import NullASR
from packages.contracts.events import InProcessBus
from packages.contracts.risk import Verdict
from packages.eval.fixtures import fixture_turns, load_fixture
from packages.policy.pack import PolicyPack, load_pack

FX = load_fixture("fx_tech_support_en_001")


class _RecordingJudge:
    """Counts evaluate() calls; never touches the network."""

    def __init__(self) -> None:
        self.calls = 0

    async def evaluate(self, window: object, pack: object) -> Verdict:
        self.calls += 1
        return Verdict(
            verdict="unclear",
            adjustment=0,
            signals=(),
            protective=(),
            rationale="recording test judge",
            model_version="fake",
            latency_ms=1,
        )


def _app(**kw: object) -> TestClient:
    bus = kw.pop("bus", None) or InProcessBus()
    kw.setdefault("judge_factory", lambda pack: None)  # tests opt in to the judge explicitly
    kw.setdefault(
        "dev_mode", True
    )  # keep ?tenant= admission working; key auth is test_key_admission
    app = create_app(
        provider_factory=lambda spec: NullASR(fixture_turns(FX), speed=200.0),
        bus=bus,
        **kw,  # type: ignore[arg-type]
    )
    return TestClient(app)


def test_health_ok() -> None:
    r = _app().get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "metrics" in body and "rejected" in body["metrics"]


def test_console_static_files_are_served() -> None:
    client = _app()
    assert "speakerphone capture" in client.get("/").text
    wk = client.get("/worklet.js")
    assert wk.status_code == 200
    assert "registerProcessor" in wk.text
    assert "text/javascript" in wk.headers["content-type"]
    cap = client.get("/capture.js").text
    assert "echoCancellation: false" in cap
    # T-3.5: the single speakerphone stream opens as leg=mixed so the pipeline
    # runs the AcousticRoleClassifier and attributes each turn's role, rather
    # than pinning the whole call to one role.
    assert "leg=mixed" in cap and "leg=far" not in cap
    assert "mountConsole" in client.get("/app.js").text


@pytest.mark.parametrize(
    "params, reason",
    [
        ({"session": "s", "leg": "far", "tenant": ""}, "TENANT"),
        ({"session": "s", "leg": "far", "tenant": "testco"}, "QUOTA"),
        ({"session": "s", "leg": "far", "tenant": "testco"}, "CAPACITY"),
    ],
)
def test_admission_rejections_are_explicit_and_counted(params: dict, reason: str) -> None:
    kw: dict[str, object] = {}
    if reason == "QUOTA":
        kw["quota_per_tenant"] = 0
    if reason == "CAPACITY":
        kw["capacity"] = 0
    client = _app(**kw)

    with client.websocket_connect("/ws/capture?" + _qs(params)) as ws:
        msg = ws.receive_json()
    assert msg == {"type": "rejected", "reason": reason}
    assert client.get("/health").json()["metrics"]["rejected"][reason] == 1


def test_consent_required_when_not_dev() -> None:
    client = _app(dev_consent=False)
    with client.websocket_connect("/ws/capture?session=s&leg=far&tenant=testco") as ws:
        assert ws.receive_json() == {"type": "rejected", "reason": "CONSENT"}
    # with a token it passes admission
    with client.websocket_connect("/ws/capture?session=s2&leg=far&tenant=testco&consent=tok") as ws:
        ws.send_bytes(b"\x00\x00" * 320)


def test_streaming_a_fixture_yields_decisions_on_sse() -> None:
    bus = InProcessBus()
    client = _app(bus=bus)

    with client.websocket_connect("/ws/capture?session=g1&leg=far&tenant=testco") as ws:
        for _ in range(5):
            ws.send_bytes(b"\x00\x00" * 640)  # ignored by NullASR; drives the frame path
    # WS closed -> pipeline.end() flushed all turns -> decisions + close on the bus

    events: list[tuple[str, dict]] = []
    kind = "message"
    with client.stream("GET", "/events/g1") as resp:
        for line in resp.iter_lines():
            if line.startswith("event:"):
                kind = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                events.append((kind, json.loads(line[5:].strip())))
            if kind == "end":
                break

    turns = [p for k, p in events if k == "turn"]
    decisions = [p for k, p in events if k == "decision"]
    assert turns and turns[0]["role"] in {"CALLER", "CALLEE", "UNKNOWN"}
    assert any(t["signals"] for t in turns)  # signal chips have something to render
    assert decisions and "ALERT" in [d["state"] for d in decisions]
    assert isinstance(decisions[-1]["contributions"], list)
    assert client.get("/health").json()["metrics"]["admitted"] == 1


def test_replay_endpoint_pushes_a_fixture_to_the_console_stream() -> None:
    bus = InProcessBus()
    client = _app(bus=bus)
    r = client.post("/replay/fx_tech_support_en_001?session=demo&speed=400")
    assert r.status_code == 200 and r.json()["session"] == "demo"

    seen: list[tuple[str, dict]] = []
    kind = "message"
    with client.stream("GET", "/events/demo") as resp:
        for line in resp.iter_lines():
            if line.startswith("event:"):
                kind = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                seen.append((kind, json.loads(line[5:].strip())))
            if kind == "end":
                break
    assert any(k == "turn" for k, _ in seen)
    assert "ALERT" in [p["state"] for k, p in seen if k == "decision"]


def test_events_are_tenant_scoped_on_the_bus() -> None:
    bus = InProcessBus()
    client = _app(bus=bus)
    client.post("/replay/fx_tech_support_en_001?session=a&tenant=t1&speed=400")
    client.post("/replay/fx_gift_card_en_001?session=b&tenant=t2&speed=400")

    subjects: list[str] = []
    with client.stream("GET", "/events/a?tenant=t1") as resp:
        kind = "message"
        for line in resp.iter_lines():
            if line.startswith("event:"):
                kind = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                subjects.append(kind)
            if kind == "end":
                break
    assert "decision" in subjects  # t1's own events came through
    # a t1 subscriber can only ever match rf.t1.* — t2's session-close can't end it,
    # which is why the stream above ends on t1's own rf.t1.session.closed.


def test_judge_is_wired_into_the_capture_pipeline() -> None:
    """T-2.6b: the gateway must actually hand a judge to the Pipeline.

    fx_tech_support_en_001 fires REMOTE_ACCESS and crosses trigger_score, so
    should_trigger() fires and the judge's evaluate() is called at least once.
    """
    judge = _RecordingJudge()
    client = _app(judge_factory=lambda pack: judge)
    with client.websocket_connect("/ws/capture?session=jw&leg=far&tenant=testco") as ws:
        for _ in range(5):
            ws.send_bytes(b"\x00\x00" * 640)
    # WS close -> pipeline.end() flushes every turn through the judge path
    assert judge.calls >= 1


def test_default_judge_factory_is_none_when_pack_disables_it() -> None:
    data = load_pack("config/policy/default.yaml").model_dump()
    data["judge"]["enabled"] = False
    disabled = PolicyPack.model_validate(data)
    assert _default_judge_factory()(disabled) is None


def _qs(params: dict) -> str:
    return "&".join(f"{k}={v}" for k, v in params.items())
