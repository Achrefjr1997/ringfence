"""Twilio ingress end to end, without telephony.

A scripted client speaks Twilio's envelope at the real app, the way
`packages/ingress/siprec/loopback.py` fakes an SBC.  Asserts the two things
that make this ingress worth building: both tracks arrive as *separate*
capture legs with the right roles, and the webhook refuses anything it
cannot prove came from Twilio.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import numpy as np
from starlette.testclient import TestClient

from apps.twilio.app import Config, create_app
from apps.twilio.twiml import stream_and_dial, verify_signature
from packages.ingress.siprec.g711 import ulaw_encode

TOKEN = "test-auth-token"
CFG = Config(
    auth_token=TOKEN,
    public_url="https://demo.example",
    dial_to="+21612345678",
    api_key="rf_key",
    language="fr",
)


class _RecordingUplink:
    """Stands in for the gateway: remembers what each leg received."""

    def __init__(self) -> None:
        self.frames: dict[tuple[str, str], list[bytes]] = {}
        self.attrib: dict[str, tuple[str, str]] = {}
        self.closed: list[str] = []

    def set_attribution(self, session_id: str, user_ref: str, user_label: str = "") -> None:
        self.attrib[session_id] = (user_ref, user_label)

    async def send(self, session_id: str, leg: str, frame: bytes) -> None:
        self.frames.setdefault((session_id, leg), []).append(frame)

    async def close_session(self, session_id: str) -> None:
        self.closed.append(session_id)

    def stats(self) -> dict[str, int]:
        return {"capture_legs_open": len(self.frames)}


def _sign(url: str, params: dict[str, str]) -> str:
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    return base64.b64encode(
        hmac.new(TOKEN.encode(), payload.encode(), hashlib.sha1).digest()
    ).decode()


def _tone(hz: int, ms: int = 200) -> np.ndarray:
    n = int(8_000 * ms / 1000)
    t = np.arange(n) / 8_000.0
    return (0.3 * 32767 * np.sin(2 * np.pi * hz * t)).astype(np.int16)


def _media_frames(pcm: np.ndarray, track: str) -> list[str]:
    """Twilio sends 20 ms chunks: 160 µ-law bytes each."""
    out = []
    for i in range(0, len(pcm), 160):
        chunk = pcm[i : i + 160]
        out.append(
            json.dumps(
                {
                    "event": "media",
                    "streamSid": "MZ1",
                    "media": {
                        "track": track,
                        "chunk": str(i // 160),
                        "timestamp": str(20 * (i // 160)),
                        "payload": base64.b64encode(ulaw_encode(chunk)).decode(),
                    },
                }
            )
        )
    return out


# -- the webhook -------------------------------------------------------


def test_an_unsigned_webhook_is_refused_and_places_no_call() -> None:
    """It starts a billable call and opens a media stream. /replay shipped
    with no auth once (#62); not again."""
    with TestClient(create_app(CFG, uplink=_RecordingUplink())) as c:  # type: ignore[arg-type]
        r = c.post("/voice", data={"From": "+216111", "To": "+216222"})
    assert r.status_code == 403
    assert "<Hangup/>" in r.text
    assert "<Dial>" not in r.text


def test_a_wrongly_signed_webhook_is_refused() -> None:
    with TestClient(create_app(CFG, uplink=_RecordingUplink())) as c:  # type: ignore[arg-type]
        r = c.post(
            "/voice",
            data={"From": "+216111"},
            headers={"X-Twilio-Signature": "not-the-right-signature"},
        )
    assert r.status_code == 403


def test_a_correctly_signed_webhook_returns_stream_then_dial() -> None:
    params = {"From": "+216111", "To": "+216222", "CallSid": "CA1"}
    url = "https://demo.example/voice"
    with TestClient(create_app(CFG, uplink=_RecordingUplink())) as c:  # type: ignore[arg-type]
        r = c.post("/voice", data=params, headers={"X-Twilio-Signature": _sign(url, params)})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/xml")
    # <Start> not <Connect>: the fork begins AND the phone still rings.
    assert "<Start><Stream" in r.text
    assert 'track="both_tracks"' in r.text
    assert "wss://demo.example/media" in r.text
    assert "<Dial><Number>+21612345678</Number></Dial>" in r.text


def test_signature_helper_matches_twilios_scheme() -> None:
    url = "https://demo.example/voice"
    params = {"b": "2", "a": "1"}  # must be sorted by key, not insertion order
    assert verify_signature(auth_token=TOKEN, url=url, params=params, signature=_sign(url, params))
    assert not verify_signature(auth_token=TOKEN, url=url, params=params, signature="x")
    assert not verify_signature(auth_token="", url=url, params=params, signature=_sign(url, params))


def test_twiml_escapes_attributes() -> None:
    xml = stream_and_dial(stream_url='wss://x/media?a=1&b="2"', dial_to="+1&2")
    assert "&amp;" in xml and '\\"' not in xml


# -- the media stream --------------------------------------------------


def test_both_tracks_arrive_as_separate_legs_with_the_right_roles() -> None:
    """The whole reason to use both_tracks: we know who is who, so neither
    leg should fall back to MIXED and the acoustic classifier."""
    up = _RecordingUplink()
    with TestClient(create_app(CFG, uplink=up)) as c:  # type: ignore[arg-type]
        with c.websocket_connect("/media") as ws:
            ws.send_text(json.dumps({"event": "connected", "protocol": "Call"}))
            ws.send_text(
                json.dumps(
                    {
                        "event": "start",
                        "streamSid": "MZ1",
                        "start": {
                            "streamSid": "MZ1",
                            "callSid": "CA-demo",
                            "customParameters": {"user": "alice@corp", "user_label": "Alice"},
                            "mediaFormat": {
                                "encoding": "audio/x-mulaw",
                                "sampleRate": 8000,
                                "channels": 1,
                            },
                        },
                    }
                )
            )
            for frame in _media_frames(_tone(300), "inbound"):
                ws.send_text(frame)
            for frame in _media_frames(_tone(600), "outbound"):
                ws.send_text(frame)
            ws.send_text(json.dumps({"event": "stop", "streamSid": "MZ1", "stop": {}}))

    legs = {leg for (_sid, leg) in up.frames}
    assert legs == {"far", "near"}, legs
    assert all(sid == "CA-demo" for (sid, _leg) in up.frames)
    # 40 ms @ 16 kHz after the 8k->16k resample
    assert all(len(f) == 640 * 2 for frames in up.frames.values() for f in frames)
    assert up.attrib["CA-demo"] == ("alice@corp", "Alice")
    assert up.closed == ["CA-demo"]


def test_an_unsupported_codec_forwards_nothing() -> None:
    """Mis-decoding sounds like noise and scores like silence, which looks
    exactly like 'the detector found nothing'. Refuse the stream instead."""
    up = _RecordingUplink()
    with TestClient(create_app(CFG, uplink=up)) as c:  # type: ignore[arg-type]
        with c.websocket_connect("/media") as ws:
            ws.send_text(
                json.dumps(
                    {
                        "event": "start",
                        "streamSid": "MZ2",
                        "start": {
                            "callSid": "CA2",
                            "mediaFormat": {
                                "encoding": "audio/l16",
                                "sampleRate": 16000,
                                "channels": 1,
                            },
                        },
                    }
                )
            )
            for frame in _media_frames(_tone(300), "inbound")[:2]:
                ws.send_text(frame)
    assert up.frames == {}, "nothing should have been forwarded"


def test_media_before_start_is_ignored_not_crashed() -> None:
    up = _RecordingUplink()
    with TestClient(create_app(CFG, uplink=up)) as c:  # type: ignore[arg-type]
        with c.websocket_connect("/media") as ws:
            for frame in _media_frames(_tone(300), "inbound"):
                ws.send_text(frame)
            ws.send_text(json.dumps({"event": "stop", "streamSid": "MZ3", "stop": {}}))
    assert up.frames == {}


def test_health_and_metrics_are_served() -> None:
    with TestClient(create_app(CFG, uplink=_RecordingUplink())) as c:  # type: ignore[arg-type]
        assert c.get("/health").json()["ok"] is True
        assert "ringfence_capture_legs_open" in c.get("/metrics").text
