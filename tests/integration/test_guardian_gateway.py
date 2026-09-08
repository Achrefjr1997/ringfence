"""T-4.5 wiring -- a scam through the gateway fires the tenant's guardian webhook."""

from __future__ import annotations

import httpx
import pytest
from starlette.testclient import TestClient

from apps.gateway.app import create_app
from apps.gateway.guardian import GuardianDispatcher
from packages.asr.null import NullASR
from packages.policy.pack import load_pack
from packages.policy.tenants import load_tenants

PACK = load_pack("config/policy/default.yaml")


class Recorder:
    def __init__(self) -> None:
        self.posts: list[tuple[str, bytes, dict[str, str]]] = []

    async def post(self, url: str, *, content: bytes, headers: dict[str, str], **kw: object):  # noqa: ANN201
        self.posts.append((url, content, headers))
        return httpx.Response(200)

    async def aclose(self) -> None: ...


def test_scam_replay_for_a_configured_tenant_fires_the_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RF_GUARDIAN_SECRET_ACME", "acme-guardian-secret")
    rec = Recorder()
    dispatcher = GuardianDispatcher(load_tenants(), PACK, dry_run=False, client=rec)  # type: ignore[arg-type]
    app = create_app(
        provider_factory=lambda spec: NullASR([]),
        session_secret="s",
        dev_mode=True,
        guardian_dispatcher=dispatcher,
    )

    with TestClient(app) as c:
        r = c.post("/replay/fx_gift_card_en_001?session=g1&tenant=acme&speed=64")
        assert r.status_code == 200 and r.json()["decisions"] >= 1
        for _ in range(20):  # let the dispatcher task drain the bus
            c.get("/health")
            if rec.posts:
                break

    assert rec.posts, "guardian webhook never fired on INTERVENE"
    url, body, headers = rec.posts[0]
    assert url == "https://acme.example/ringfence/guardian"
    assert headers["X-RingFence-Signature"].startswith("sha256=")
    text = body.decode().lower()
    assert '"event":"intervene"' in body.decode().replace(" ", "")
    # verdict only -- fixture transcript phrases must not appear
    assert "gift card code" not in text and "amazon fraud prevention" not in text


def test_no_webhook_for_a_tenant_without_a_url(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder()
    dispatcher = GuardianDispatcher(load_tenants(), PACK, dry_run=False, client=rec)  # type: ignore[arg-type]
    app = create_app(
        provider_factory=lambda spec: NullASR([]),
        session_secret="s",
        dev_mode=True,
        guardian_dispatcher=dispatcher,
    )
    with TestClient(app) as c:
        c.post("/replay/fx_gift_card_en_001?session=g2&tenant=default&speed=64")
        for _ in range(10):
            c.get("/health")
    assert rec.posts == []
