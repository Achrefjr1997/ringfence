"""Telnyx ingress end to end, without telephony.

Telnyx is here because Twilio would not send an SMS verification code to a
Tunisian number, so the account could not be created at all.  The parser
being provider-neutral is what made that a day of work rather than a
rewrite -- so these tests check the two things that actually differ:
Ed25519 signatures instead of HMAC-SHA1, and a snake_case envelope with a
negotiable codec.
"""

from __future__ import annotations

import base64
import json
import time

import numpy as np
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from starlette.testclient import TestClient

from apps.telnyx.app import Config, create_app
from apps.telnyx.webhook import verify_signature
from packages.ingress.mediastream import MediaFormatError
from packages.ingress.mediastream.telnyx import parse
from packages.ingress.siprec.g711 import alaw_encode, ulaw_encode

_KEY = ed25519.Ed25519PrivateKey.generate()
PUBLIC_KEY = base64.b64encode(_KEY.public_key().public_bytes_raw()).decode()

CFG = Config(
    public_key=PUBLIC_KEY,
    public_url="https://demo.example",
    dial_to="+21612345678",
    api_key="rf_key",
    language="fr",
)


class _RecordingUplink:
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


def _sign(body: bytes, ts: str) -> str:
    return base64.b64encode(_KEY.sign(ts.encode() + b"|" + body)).decode()


def _tone(hz: int, ms: int = 200) -> np.ndarray:
    n = int(8_000 * ms / 1000)
    t = np.arange(n) / 8_000.0
    return (0.3 * 32767 * np.sin(2 * np.pi * hz * t)).astype(np.int16)


def _start(encoding: str = "PCMU") -> str:
    return json.dumps(
        {
            "event": "start",
            "sequence_number": "1",
            "stream_id": "st-uuid",
            "start": {
                "call_control_id": "CC-demo",
                "call_session_id": "CS-1",
                "from": "+216111",
                "to": "+216222",
                "parameters": {"user": "alice@corp", "user_label": "Alice"},
                "media_format": {"encoding": encoding, "sample_rate": 8000, "channels": 1},
            },
        }
    )


def _media(pcm: np.ndarray, track: str, encode=ulaw_encode) -> list[str]:  # noqa: ANN001
    out = []
    for i in range(0, len(pcm), 160):
        out.append(
            json.dumps(
                {
                    "event": "media",
                    "stream_id": "st-uuid",
                    "media": {
                        "track": track,
                        "chunk": str(i // 160),
                        "timestamp": str(20 * (i // 160)),
                        "payload": base64.b64encode(encode(pcm[i : i + 160])).decode(),
                    },
                }
            )
        )
    return out


# -- Ed25519, the real difference from Twilio --------------------------


def test_a_correctly_signed_body_verifies() -> None:
    body, ts = b"a=1&b=2", str(int(time.time()))
    assert verify_signature(
        public_key=PUBLIC_KEY, body=body, signature=_sign(body, ts), timestamp=ts
    )


def test_a_tampered_body_fails() -> None:
    ts = str(int(time.time()))
    sig = _sign(b"a=1", ts)
    assert not verify_signature(public_key=PUBLIC_KEY, body=b"a=2", signature=sig, timestamp=ts)


def test_an_old_signature_is_refused_even_though_it_is_valid() -> None:
    """Replay protection. The signature is genuine; the timestamp is not
    recent, so a captured POST cannot be re-sent later."""
    body = b"a=1"
    old = str(int(time.time()) - 3600)
    assert not verify_signature(
        public_key=PUBLIC_KEY, body=body, signature=_sign(body, old), timestamp=old
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"public_key": ""},
        {"signature": ""},
        {"timestamp": ""},
        {"timestamp": "not-a-number"},
        {"signature": "!!!not base64!!!"},
        {"public_key": base64.b64encode(b"too short").decode()},
    ],
)
def test_malformed_inputs_fail_closed(kwargs: dict[str, str]) -> None:
    body, ts = b"a=1", str(int(time.time()))
    args = {
        "public_key": PUBLIC_KEY,
        "body": body,
        "signature": _sign(body, ts),
        "timestamp": ts,
        **kwargs,
    }
    assert not verify_signature(**args)  # type: ignore[arg-type]


# -- the webhook -------------------------------------------------------


def test_an_unsigned_webhook_is_refused_and_places_no_call() -> None:
    with TestClient(create_app(CFG, uplink=_RecordingUplink())) as c:  # type: ignore[arg-type]
        r = c.post("/voice", content=b"From=%2B216111")
    assert r.status_code == 403
    assert "<Hangup/>" in r.text and "<Dial>" not in r.text


def test_a_signed_webhook_returns_texml_with_stream_then_dial() -> None:
    body, ts = b"From=%2B216111&To=%2B216222", str(int(time.time()))
    with TestClient(create_app(CFG, uplink=_RecordingUplink())) as c:  # type: ignore[arg-type]
        r = c.post(
            "/voice",
            content=body,
            headers={"telnyx-signature-ed25519": _sign(body, ts), "telnyx-timestamp": ts},
        )
    assert r.status_code == 200
    # TeXML is TwiML-compatible, so this is the same markup Twilio gets.
    assert "<Start><Stream" in r.text
    assert 'track="both_tracks"' in r.text
    assert "wss://demo.example/media" in r.text
    assert "<Dial><Number>+21612345678</Number></Dial>" in r.text


def test_without_a_dial_target_the_line_is_held_and_capture_still_starts() -> None:
    """A trial account has one verified number and cannot bridge a second
    leg. Rather than emit a <Dial> that will fail, hold the line -- the fork
    is what the demo needs, and it is unchanged."""
    body, ts = b"From=%2B216111&To=%2B12025550100", str(int(time.time()))
    cfg = Config(
        public_key=PUBLIC_KEY,
        public_url="https://demo.example",
        dial_to="",
        hold_notice="Cet appel est analyse.",
        api_key="rf_key",
        language="fr",
    )
    with TestClient(create_app(cfg, uplink=_RecordingUplink())) as c:  # type: ignore[arg-type]
        r = c.post(
            "/voice",
            content=body,
            headers={"telnyx-signature-ed25519": _sign(body, ts), "telnyx-timestamp": ts},
        )
    assert r.status_code == 200
    assert 'track="both_tracks"' in r.text  # capture is identical
    assert "wss://demo.example/media" in r.text
    assert "<Dial>" not in r.text  # nobody to bridge to
    assert "<Pause" in r.text  # but the call stays up
    assert 'language="fr-FR"' in r.text and "Cet appel est analyse." in r.text


def test_a_dial_target_still_bridges() -> None:
    """Hold mode must not become the default by accident."""
    body, ts = b"From=%2B216111", str(int(time.time()))
    with TestClient(create_app(CFG, uplink=_RecordingUplink())) as c:  # type: ignore[arg-type]
        r = c.post(
            "/voice",
            content=body,
            headers={"telnyx-signature-ed25519": _sign(body, ts), "telnyx-timestamp": ts},
        )
    assert "<Dial><Number>+21612345678</Number></Dial>" in r.text
    assert "<Pause" not in r.text


# -- the envelope ------------------------------------------------------


def test_start_reads_snake_case_ids_and_parameters() -> None:
    ev = parse(_start())
    assert ev is not None and ev.kind == "start"
    assert ev.call_id == "CC-demo"  # call_control_id, not callSid
    assert ev.stream_id == "st-uuid"  # stream_id, not streamSid
    assert ev.params["user"] == "alice@corp"
    assert ev.params["encoding"] == "PCMU"


def test_pcma_is_negotiable_and_decodes_with_the_agreed_codec() -> None:
    """Telnyx does not repeat the codec on every media frame, so the caller
    threads it back in from the start frame."""
    ev = parse(_start("PCMA"))
    assert ev is not None and ev.params["encoding"] == "PCMA"
    pcm = _tone(300, ms=20)
    frame = parse(_media(pcm, "inbound", encode=alaw_encode)[0], encoding="PCMA")
    assert frame is not None and frame.pcm is not None and frame.pcm.shape == (160,)


def test_an_unsupported_codec_raises() -> None:
    with pytest.raises(MediaFormatError):
        parse(_start("OPUS"))


def test_both_tracks_arrive_as_separate_legs_with_the_right_roles() -> None:
    up = _RecordingUplink()
    with TestClient(create_app(CFG, uplink=up)) as c:  # type: ignore[arg-type]
        with c.websocket_connect("/media") as ws:
            ws.send_text(json.dumps({"event": "connected", "version": "1.0.0"}))
            ws.send_text(_start())
            for frame in _media(_tone(300), "inbound"):
                ws.send_text(frame)
            for frame in _media(_tone(600), "outbound"):
                ws.send_text(frame)
            ws.send_text(
                json.dumps(
                    {
                        "event": "stop",
                        "stream_id": "st-uuid",
                        "stop": {"call_control_id": "CC-demo"},
                    }
                )
            )

    assert {leg for (_sid, leg) in up.frames} == {"far", "near"}
    assert all(sid == "CC-demo" for (sid, _leg) in up.frames)
    assert all(len(f) == 640 * 2 for frames in up.frames.values() for f in frames)
    assert up.attrib["CC-demo"] == ("alice@corp", "Alice")
    assert up.closed == ["CC-demo"]


def test_health_and_metrics_are_served() -> None:
    with TestClient(create_app(CFG, uplink=_RecordingUplink())) as c:  # type: ignore[arg-type]
        assert c.get("/health").json()["ok"] is True
        assert "ringfence_capture_legs_open" in c.get("/metrics").text
