"""Twilio Media Streams envelope parsing.

This is the ingress that replaces the impossible one.  Call audio cannot be
taken off a handset, so it comes from the network as JSON frames carrying
base64 µ-law -- and the decode path underneath is the same one the SIPREC
adapter already uses.
"""

from __future__ import annotations

import base64
import json

import numpy as np
import pytest

from packages.ingress.mediastream import MediaFormatError, leg_for_track
from packages.ingress.mediastream.twilio import parse
from packages.ingress.siprec.g711 import ulaw_encode

_FMT = {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}


def _start(**over: object) -> str:
    start = {
        "streamSid": "MZ123",
        "accountSid": "AC1",
        "callSid": "CA999",
        "tracks": ["inbound", "outbound"],
        "customParameters": {"tenant": "acme", "lang": "fr"},
        "mediaFormat": dict(_FMT),
        **over,
    }
    return json.dumps(
        {"event": "start", "sequenceNumber": "1", "streamSid": "MZ123", "start": start}
    )


def _media(pcm: np.ndarray, track: str = "inbound") -> str:
    return json.dumps(
        {
            "event": "media",
            "sequenceNumber": "2",
            "streamSid": "MZ123",
            "media": {
                "track": track,
                "chunk": "1",
                "timestamp": "20",
                "payload": base64.b64encode(ulaw_encode(pcm)).decode(),
            },
        }
    )


# -- the happy path ----------------------------------------------------


def test_connected_frame() -> None:
    ev = parse('{"event":"connected","protocol":"Call","version":"1.0.0"}')
    assert ev is not None and ev.kind == "connected"


def test_start_carries_the_call_id_and_custom_parameters() -> None:
    ev = parse(_start())
    assert ev is not None and ev.kind == "start"
    assert ev.call_id == "CA999"
    assert ev.stream_id == "MZ123"
    assert ev.params == {"tenant": "acme", "lang": "fr"}


def test_media_decodes_to_the_original_samples() -> None:
    """µ-law is lossy, so assert the round-trip lands on the same
    representable levels rather than bit-identical input."""
    t = np.arange(160) / 8000.0
    pcm = (0.3 * 32767 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
    ev = parse(_media(pcm))
    assert ev is not None and ev.kind == "media"
    assert ev.pcm is not None
    assert ev.pcm.shape == (160,)
    assert ev.sample_rate == 8000
    # decode(encode(x)) is idempotent on a second pass
    from packages.ingress.siprec.g711 import ulaw_decode

    assert np.array_equal(ev.pcm, ulaw_decode(ulaw_encode(ev.pcm)))


def test_stop_carries_the_call_id() -> None:
    ev = parse(json.dumps({"event": "stop", "streamSid": "MZ123", "stop": {"callSid": "CA999"}}))
    assert ev is not None and ev.kind == "stop" and ev.call_id == "CA999"


@pytest.mark.parametrize("event", ["mark", "dtmf", "something-new"])
def test_unknown_events_are_inert_not_fatal(event: str) -> None:
    ev = parse(json.dumps({"event": event, "streamSid": "MZ123"}))
    assert ev is not None and ev.kind == "other"


# -- track -> leg: the thing that buys exact role attribution ----------


def test_inbound_is_the_caller_and_outbound_is_the_protected_party() -> None:
    assert leg_for_track("inbound") == "far"
    assert leg_for_track("outbound") == "near"


def test_an_unmapped_track_degrades_to_mixed_rather_than_guessing() -> None:
    assert leg_for_track(None) == "mixed"


def test_media_carries_its_track_through() -> None:
    pcm = np.zeros(160, dtype=np.int16)
    assert parse(_media(pcm, "inbound")).track == "inbound"  # type: ignore[union-attr]
    assert parse(_media(pcm, "outbound")).track == "outbound"  # type: ignore[union-attr]
    assert parse(_media(pcm, "bogus")).track is None  # type: ignore[union-attr]


# -- a bad frame must not kill a live call -----------------------------


@pytest.mark.parametrize(
    "raw",
    ["not json", "", "[]", '"a string"', '{"event":"media","media":"not a dict"}'],
)
def test_malformed_frames_are_dropped_not_raised(raw: str) -> None:
    assert parse(raw) is None


def test_undecodable_base64_is_dropped() -> None:
    bad = json.dumps(
        {"event": "media", "streamSid": "MZ1", "media": {"track": "inbound", "payload": "!!!!"}}
    )
    assert parse(bad) is None


def test_an_empty_payload_is_dropped() -> None:
    empty = json.dumps(
        {"event": "media", "streamSid": "MZ1", "media": {"track": "inbound", "payload": ""}}
    )
    assert parse(empty) is None


# -- but a wrong FORMAT is loud ----------------------------------------


@pytest.mark.parametrize(
    "fmt",
    [
        {"encoding": "audio/l16", "sampleRate": 8000, "channels": 1},
        {"encoding": "audio/x-mulaw", "sampleRate": 16000, "channels": 1},
        {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 2},
    ],
)
def test_a_format_we_cannot_decode_raises(fmt: dict[str, object]) -> None:
    """Silently mis-decoding sounds like noise and scores like silence --
    indistinguishable from 'the detector found nothing'."""
    with pytest.raises(MediaFormatError):
        parse(_start(mediaFormat=fmt))


def test_a_start_without_a_media_format_is_tolerated() -> None:
    """Not every provider sends one; absence is not a wrong format."""
    ev = parse(_start(mediaFormat={}))
    assert ev is not None and ev.kind == "start"
