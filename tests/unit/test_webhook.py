"""T-4.5 — guardian webhook: signed, rate-limited, verdict only."""

import json
import uuid

import httpx
import pytest

from packages.contracts.audio import Mode
from packages.contracts.risk import Contribution, Decision
from packages.intervene.webhook import GuardianWebhook, sign, verify_signature
from packages.policy.pack import load_pack

PACK = load_pack("config/policy/default.yaml")
SECRET = "s3cr3t-guardian-key"

# phrases a real transcript would contain — none may appear in the payload
TRANSCRIPT = [
    "This is your bank's fraud department",
    "read me the one-time code",
    "buy a 500 dollar gift card",
    "do not hang up",
]


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


class Recorder:
    def __init__(self) -> None:
        self.posts: list[tuple[bytes, dict[str, str]]] = []

    async def post(self, url: str, *, content: bytes, headers: dict[str, str], **kw: object):  # noqa: ANN201
        self.posts.append((content, headers))
        return httpx.Response(200)


def _decision(state: str = "INTERVENE") -> Decision:
    return Decision(
        decision_id=uuid.uuid4().hex,
        session_id="s1",
        t=27.0,
        state=state,  # type: ignore[arg-type]
        score=100.0,
        policy_pack="default@1",
        contributions=(
            Contribution(
                source="signal", id="AUTH_CLAIM", value=12.0, role="CALLER", evidence=TRANSCRIPT[0]
            ),
            Contribution(
                source="signal",
                id="VERIF_INVERT",
                value=30.0,
                role="CALLER",
                evidence=TRANSCRIPT[1],
            ),
            Contribution(source="signal", id="REFUSE_SECRETS", value=-30.0, role="CALLER"),
            Contribution(source="combo", id="COMBO_CRITICAL", value=35.0),
        ),
    )


def _wh(clock: Clock, rec: Recorder, **kw: object) -> GuardianWebhook:
    wh = GuardianWebhook(
        "https://guardian.example/hook", SECRET, PACK, dry_run=False, now=clock, **kw
    )  # type: ignore[arg-type]
    wh._client = rec  # type: ignore[assignment]
    return wh


async def test_fires_only_on_intervene() -> None:
    rec = Recorder()
    wh = _wh(Clock(), rec)
    assert await wh.notify(_decision("ALERT"), mode=Mode.SDK) is False
    assert await wh.notify(_decision("INTERVENE"), mode=Mode.SDK) is True
    assert len(rec.posts) == 1


async def test_signature_verifies_and_rejects_tampering() -> None:
    clock = Clock()
    rec = Recorder()
    await _wh(clock, rec).notify(
        _decision(), mode=Mode.SDK, rationale="caller impersonated the bank"
    )
    body, headers = rec.posts[0]
    sig, ts = headers["X-RingFence-Signature"], headers["X-RingFence-Timestamp"]

    assert verify_signature(SECRET, body, sig, ts, now=clock())
    assert not verify_signature("wrong-key", body, sig, ts, now=clock())
    assert not verify_signature(SECRET, body + b" ", sig, ts, now=clock())  # tampered body
    assert not verify_signature(SECRET, body, sig, ts, now=clock() + 10_000)  # stale timestamp


async def test_payload_is_verdict_only_no_transcript_anywhere() -> None:
    rec = Recorder()
    await _wh(Clock(), rec).notify(
        _decision(), mode=Mode.SDK, rationale="bank impersonation, code requested"
    )
    payload = json.loads(rec.posts[0][0])

    assert set(payload) == {
        "event",
        "session_id",
        "decision_id",
        "t",
        "state",
        "score",
        "signals",
        "protective",
        "rationale",
    }
    assert payload["signals"] == ["AUTH_CLAIM", "VERIF_INVERT"]
    assert payload["protective"] == ["REFUSE_SECRETS"]

    # walk every string value in the payload; no transcript phrase may appear
    def strings(o: object):  # noqa: ANN202
        if isinstance(o, str):
            yield o
        elif isinstance(o, dict):
            for k, v in o.items():
                yield from strings(k)
                yield from strings(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                yield from strings(v)

    blob = " ".join(strings(payload)).lower()
    for phrase in TRANSCRIPT:
        assert phrase.lower() not in blob, f"transcript leaked: {phrase!r}"
    for key in ("transcript", "text", "audio", "evidence", "utterance"):
        assert key not in blob


async def test_rate_limited_per_guardian_per_hour() -> None:
    clock = Clock()
    rec = Recorder()
    wh = _wh(clock, rec, max_per_hour=3)
    for _ in range(5):
        await wh.notify(_decision(), mode=Mode.SDK)
    assert len(rec.posts) == 3  # capped

    clock.t += 3601  # an hour later the window has rolled
    assert await wh.notify(_decision(), mode=Mode.SDK) is True
    assert len(rec.posts) == 4


@pytest.mark.parametrize("dry_run, mode", [(True, Mode.SDK), (False, Mode.REPLAY)])
async def test_guard_suppresses(dry_run: bool, mode: Mode) -> None:
    rec = Recorder()
    wh = GuardianWebhook("https://g/h", SECRET, PACK, dry_run=dry_run, now=Clock())
    wh._client = rec  # type: ignore[assignment]
    assert await wh.notify(_decision(), mode=mode) is False
    assert rec.posts == []


def test_empty_secret_rejected() -> None:
    with pytest.raises(ValueError):
        GuardianWebhook("https://g/h", "", PACK)


def test_sign_is_deterministic_and_timestamp_bound() -> None:
    b = b'{"a":1}'
    assert sign(SECRET, b, "100") == sign(SECRET, b, "100")
    assert sign(SECRET, b, "100") != sign(SECRET, b, "101")
