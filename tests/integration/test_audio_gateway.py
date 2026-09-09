"""P7 -- call-audio recording, playback and retention over the gateway."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from apps.gateway.app import create_app
from packages.asr.null import NullASR
from packages.calls.ledger import InMemoryCallLedger
from packages.policy.tenants import TenantConfig, TenantRegistry
from packages.storage.objectstore import LocalFsObjectStore

SECRET = "test-session-secret"


def _client(tmp_path: Path, *, ledger: InMemoryCallLedger, retain: bool) -> TestClient:
    return TestClient(
        create_app(
            provider_factory=lambda spec: NullASR([]),
            session_secret=SECRET,
            call_ledger=ledger,
            object_store=LocalFsObjectStore(tmp_path / "audio"),
            tenants=TenantRegistry({"default": TenantConfig(retain_audio=retain)}),
        )
    )


def _admin(c: TestClient) -> str:
    r = c.post(
        "/auth/signup",
        json={"org_name": "Acme", "email": "a@acme.co", "password": "pw-12345678"},
    )
    assert r.status_code == 201
    return r.json()["token"]


def _h(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _run_call(c: TestClient, key: str, sid: str) -> None:
    with c.websocket_connect(f"/ws/capture?session={sid}&leg=far&key={key}") as ws:
        ws.send_bytes(b"\x11\x00" * 8000)  # 0.5s of PCM


def test_recording_is_stored_and_streamable(tmp_path: Path) -> None:
    lg = InMemoryCallLedger()
    c = _client(tmp_path, ledger=lg, retain=True)
    token = _admin(c)
    key = c.post("/orgs/keys", json={"name": "gw"}, headers=_h(token)).json()["key"]
    _run_call(c, key, "a1")

    rec = lg.get("a1")
    assert rec is not None and rec.audio_key and rec.audio_bytes and rec.audio_bytes > 0
    assert (tmp_path / "audio" / rec.audio_key).is_file()

    detail = c.get("/calls/a1", headers=_h(token)).json()
    assert detail["audio"] is True

    full = c.get("/calls/a1/audio", headers=_h(token))
    assert full.status_code == 200
    assert full.headers["content-type"] == "audio/ogg"
    assert full.headers["accept-ranges"] == "bytes"
    assert len(full.content) == rec.audio_bytes

    part = c.get("/calls/a1/audio", headers={**_h(token), "Range": "bytes=0-9"})
    assert part.status_code == 206
    assert part.headers["content-range"] == f"bytes 0-9/{rec.audio_bytes}"
    assert part.content == full.content[:10]

    dl = c.get("/calls/a1/audio?download=1", headers=_h(token))
    assert "attachment" in dl.headers["content-disposition"]

    # every play/download is on the audit trail
    actions = [e["action"] for e in c.get("/calls/a1/access-log", headers=_h(token)).json()]
    assert "play" in actions and "download" in actions


def test_a_login_token_in_the_query_string_authorises_playback(tmp_path: Path) -> None:
    lg = InMemoryCallLedger()
    c = _client(tmp_path, ledger=lg, retain=True)
    token = _admin(c)
    key = c.post("/orgs/keys", json={"name": "gw"}, headers=_h(token)).json()["key"]
    _run_call(c, key, "a2")

    r = c.get(f"/calls/a2/audio?token={token}")  # no Authorization header
    assert r.status_code == 200 and len(r.content) > 0
    assert c.get("/calls/a2/audio").status_code == 401
    assert c.get("/calls/a2/audio?token=garbage").status_code == 401


def test_nothing_is_recorded_when_the_tenant_has_not_opted_in(tmp_path: Path) -> None:
    lg = InMemoryCallLedger()
    c = _client(tmp_path, ledger=lg, retain=False)
    token = _admin(c)
    key = c.post("/orgs/keys", json={"name": "gw"}, headers=_h(token)).json()["key"]
    _run_call(c, key, "a3")

    assert lg.get("a3").audio_key is None  # type: ignore[union-attr]
    assert c.get("/calls/a3", headers=_h(token)).json()["audio"] is False
    assert c.get("/calls/a3/audio", headers=_h(token)).status_code == 404
    assert not any((tmp_path / "audio").rglob("*.opus"))


@pytest.mark.invariant
def test_no_object_store_means_no_recording_path(tmp_path: Path) -> None:
    lg = InMemoryCallLedger()
    # object_store omitted and RF_RETAIN_AUDIO unset -> the store is never built
    c = TestClient(
        create_app(
            provider_factory=lambda spec: NullASR([]),
            session_secret=SECRET,
            call_ledger=lg,
        )
    )
    token = _admin(c)
    key = c.post("/orgs/keys", json={"name": "gw"}, headers=_h(token)).json()["key"]
    _run_call(c, key, "a4")
    assert lg.get("a4").audio_key is None  # type: ignore[union-attr]
    assert c.get("/calls/a4/audio", headers=_h(token)).status_code == 404


def test_retention_sweep_drops_expired_recordings(tmp_path: Path) -> None:
    lg = InMemoryCallLedger()
    store = LocalFsObjectStore(tmp_path / "audio")
    lg.open("s1", tenant="acme", api_key_id="k1", started_at=1000.0)
    store.put("acme/2020-01/s1.opus", b"old-audio")
    lg.set_audio("s1", key="acme/2020-01/s1.opus", size=9, retain_until=time.time() - 5)

    expired = lg.expired_audio(time.time())
    assert expired == [("s1", "acme/2020-01/s1.opus")]
    for sid, k in expired:
        store.delete(k)
        lg.clear_audio(sid)
    assert store.exists("acme/2020-01/s1.opus") is False
    assert lg.get("s1").audio_key is None  # type: ignore[union-attr]
