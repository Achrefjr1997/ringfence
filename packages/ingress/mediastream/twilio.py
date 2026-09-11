"""Twilio Media Streams envelope -> :class:`MediaEvent`.

Wire format (``<Start><Stream>`` / ``<Connect><Stream>``), JSON text frames:

    {"event":"connected","protocol":"Call","version":"1.0.0"}
    {"event":"start","sequenceNumber":"1","streamSid":"MZ…",
     "start":{"streamSid":"MZ…","accountSid":"AC…","callSid":"CA…",
              "tracks":["inbound","outbound"],"customParameters":{…},
              "mediaFormat":{"encoding":"audio/x-mulaw","sampleRate":8000,
                             "channels":1}}}
    {"event":"media","sequenceNumber":"2","streamSid":"MZ…",
     "media":{"track":"inbound","chunk":"1","timestamp":"5","payload":"<b64>"}}
    {"event":"stop","sequenceNumber":"3","streamSid":"MZ…",
     "stop":{"accountSid":"AC…","callSid":"CA…"}}

``mark`` and ``dtmf`` also occur; both map to ``kind="other"`` and are
ignored by the audio path.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from packages.ingress.mediastream.events import MediaEvent, MediaFormatError, Track
from packages.ingress.siprec.g711 import ulaw_decode

_ENCODING = "audio/x-mulaw"
_RATE = 8_000
_CHANNELS = 1
_TRACKS: tuple[Track, ...] = ("inbound", "outbound")


def _as_track(value: object) -> Track | None:
    return value if value in _TRACKS else None  # type: ignore[return-value]


def _check_format(fmt: dict[str, Any]) -> None:
    encoding = str(fmt.get("encoding", ""))
    rate = int(fmt.get("sampleRate", 0) or 0)
    channels = int(fmt.get("channels", 0) or 0)
    if encoding != _ENCODING or rate != _RATE or channels != _CHANNELS:
        raise MediaFormatError(
            f"unsupported media format {encoding!r} {rate}Hz x{channels}; "
            f"expected {_ENCODING!r} {_RATE}Hz x{_CHANNELS}"
        )


def parse(raw: str | bytes) -> MediaEvent | None:
    """Parse one Twilio frame.  ``None`` for anything unintelligible.

    A malformed frame is dropped rather than raised: the socket carries a
    live call and one bad frame must not end it.  A wrong *format*, by
    contrast, raises -- that is a misconfiguration, and continuing would
    produce noise that scores like silence.
    """
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(msg, dict):
        return None

    event = str(msg.get("event", ""))
    stream_id = str(msg.get("streamSid", "") or "")

    if event == "connected":
        return MediaEvent(kind="connected")

    if event == "start":
        start = msg.get("start") or {}
        if not isinstance(start, dict):
            return None
        fmt = start.get("mediaFormat") or {}
        if isinstance(fmt, dict) and fmt:
            _check_format(fmt)
        params = start.get("customParameters") or {}
        return MediaEvent(
            kind="start",
            stream_id=str(start.get("streamSid", stream_id) or stream_id),
            call_id=str(start.get("callSid", "") or ""),
            sample_rate=_RATE,
            params={str(k): str(v) for k, v in params.items()} if isinstance(params, dict) else {},
        )

    if event == "media":
        media = msg.get("media") or {}
        if not isinstance(media, dict):
            return None
        try:
            payload = base64.b64decode(str(media.get("payload", "")), validate=True)
        except (binascii.Error, ValueError):
            return None
        if not payload:
            return None
        return MediaEvent(
            kind="media",
            stream_id=stream_id,
            track=_as_track(media.get("track")),
            pcm=ulaw_decode(payload),
            sample_rate=_RATE,
        )

    if event == "stop":
        stop = msg.get("stop") or {}
        call_id = str(stop.get("callSid", "") or "") if isinstance(stop, dict) else ""
        return MediaEvent(kind="stop", stream_id=stream_id, call_id=call_id)

    return MediaEvent(kind="other", stream_id=stream_id)
