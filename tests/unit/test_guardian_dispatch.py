"""T-4.5 wiring -- GuardianDispatcher: INTERVENE on the bus -> signed webhook."""

from __future__ import annotations

import httpx
import pytest

from apps.gateway.guardian import GuardianDispatcher
from packages.contracts.events import InProcessBus
from packages.intervene.webhook import verify_signature
from packages.policy.pack import load_pack
from packages.policy.tenants import TenantConfig, TenantRegistry

PACK = load_pack("config/policy/default.yaml")

_SCAM_TRANSCRIPT_WORDS = ("gift card", "one-time code", "fraud department")


class Recorder:
    def __init__(self) -> None:
        self.posts: list[tuple[str, bytes, dict[str, str]]] = []

    async def post(self, url: str, *, content: bytes, headers: dict[str, str], **kw: object):  # noqa: ANN201
        self.posts.append((url, content, headers))
        return httpx.Response(200)

    async def aclose(self) -> None: ...


def _registry() -> TenantRegistry:
    return TenantRegistry(
        {
            "default": TenantConfig(tenant_id="default"),
            "acme": TenantConfig(
                tenant_id="acme",
                guardian_webhook_url="https://acme.example/guard",
                guardian_webhook_secret_env="RF_GS_ACME",
            ),
            "beta": TenantConfig(
                tenant_id="beta",
                guardian_webhook_url="https://beta.example/guard",
                guardian_webhook_secret_env="RF_GS_BETA",
            ),
            "nourl": TenantConfig(tenant_id="nourl"),
        }
    )


def _event(state: str = "INTERVENE") -> dict[str, object]:
    return {
        "session_id": "s1",
        "decision_id": "d1",
        "t": 27.0,
        "state": state,
        "score": 100.0,
        "counterfactual": None,
        "contributions": [
            {"source": "signal", "id": "AUTH_CLAIM", "value": 9.9, "role": "CALLER"},
            {"source": "signal", "id": "VERIF_INVERT", "value": 28.4, "role": "CALLER"},
            {"source": "combo", "id": "COMBO_CLASSIC", "value": 45.0, "role": None},
        ],
    }


def _disp(rec: Recorder, *, dry_run: bool = False) -> GuardianDispatcher:
    return GuardianDispatcher(_registry(), PACK, dry_run=dry_run, client=rec)  # type: ignore[arg-type]


async def test_intervene_fires_a_signed_verdict_only_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RF_GS_ACME", "acme-secret")
    rec = Recorder()
    assert await _disp(rec).dispatch("acme", _event()) is True

    url, body, headers = rec.posts[0]
    assert url == "https://acme.example/guard"
    assert verify_signature(
        "acme-secret", body, headers["X-RingFence-Signature"], headers["X-RingFence-Timestamp"]
    )
    text = body.decode().lower()
    assert '"session_id":"s1"' in body.decode().replace(" ", "")
    assert "auth_claim" in text and "verif_invert" in text  # signals, ids only
    for w in _SCAM_TRANSCRIPT_WORDS:
        assert w not in text  # verdict only, never transcript


async def test_no_url_or_no_secret_does_not_fire(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder()
    d = _disp(rec)
    assert await d.dispatch("nourl", _event()) is False  # no url
    assert await d.dispatch("acme", _event()) is False  # url but RF_GS_ACME unset
    assert rec.posts == []


async def test_each_tenant_signs_with_its_own_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RF_GS_ACME", "secret-a")
    monkeypatch.setenv("RF_GS_BETA", "secret-b")
    rec = Recorder()
    d = _disp(rec)
    await d.dispatch("acme", _event())
    await d.dispatch("beta", _event())

    (_, b1, h1), (_, b2, h2) = rec.posts
    assert verify_signature(
        "secret-a", b1, h1["X-RingFence-Signature"], h1["X-RingFence-Timestamp"]
    )
    assert verify_signature(
        "secret-b", b2, h2["X-RingFence-Signature"], h2["X-RingFence-Timestamp"]
    )
    assert not verify_signature(
        "secret-a", b2, h2["X-RingFence-Signature"], h2["X-RingFence-Timestamp"]
    )


async def test_dry_run_suppresses_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RF_GS_ACME", "x")
    rec = Recorder()
    assert await _disp(rec, dry_run=True).dispatch("acme", _event()) is False
    assert rec.posts == []


async def test_rate_limited_per_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RF_GS_ACME", "x")
    rec = Recorder()
    d = _disp(rec)
    fired = [await d.dispatch("acme", _event()) for _ in range(7)]
    assert fired[:5] == [True] * 5 and fired[5:] == [False, False]  # max_per_hour=5
    assert len(rec.posts) == 5


async def test_run_consumes_the_bus_and_ignores_non_intervene_and_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RF_GS_ACME", "x")
    rec = Recorder()
    d = _disp(rec)
    bus = InProcessBus()

    import asyncio

    task = asyncio.create_task(d.run(bus))
    await asyncio.sleep(0)
    await bus.publish("rf.acme.decision", {**_event(), "state": "ALERT"})  # ignored
    await bus.publish("rf.replay.decision", _event())  # replay tenant skipped
    await bus.publish("rf.acme.decision", _event())  # fires
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert [u for u, _, _ in rec.posts] == ["https://acme.example/guard"]
