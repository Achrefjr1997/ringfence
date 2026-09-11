"""Telnyx media-streaming envelope -> :class:`MediaEvent`.

The same shape as Twilio with different names, which is exactly why the
neutral event exists:

    {"event":"connected","version":"1.0.0"}
    {"event":"start","sequence_number":"1","stream_id":"<uuid>",
     "start":{"user_id":"…","call_control_id":"…","call_session_id":"…",
              "from":"…","to":"…","client_state":"…",
              "media_format":{"encoding":"PCMU","sample_rate":8000,
                              "channels":1}}}
    {"event":"media","sequence_number":"2","stream_id":"<uuid>",
     "media":{"track":"inbound","chunk":"1","timestamp":"20",
              "payload":"<base64 RTP payload>"}}
    {"event":"stop","stream_id":"<uuid>",
     "stop":{"user_id":"…","call_control_id":"…"}}

Differences that matter: ``stream_id`` not ``streamSid``,
``call_control_id`` not ``callSid``, ``sample_rate`` not ``sampleRate``,
and the encoding is named **PCMU** rather than ``audio/x-mulaw``.  Same
codec, different spelling -- so the decode underneath is identical.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from packages.ingress.mediastream.events import MediaEvent, MediaFormatError, Track
from packages.ingress.siprec.g711 import alaw_decode, ulaw_decode

_RATE = 8_000
_CHANNELS = 1
_TRACKS: tuple[Track, ...] = ("inbound", "outbound")
# Telnyx will negotiate several codecs; these are the two we decode.
_DECODERS = {"PCMU": ulaw_decode, "PCMA": alaw_decode}


def _as_track(value: object) -> Track | None:
    return value if value in _TRACKS else None  # type: ignore[return-value]


def _encoding(fmt: dict[str, Any]) -> str:
    encoding = str(fmt.get("encoding", "")).upper()
    rate = int(fmt.get("sample_rate", 0) or 0)
    channels = int(fmt.get("channels", 0) or 0)
    if encoding not in _DECODERS or rate != _RATE or channels != _CHANNELS:
        raise MediaFormatError(
            f"unsupported media format {encoding!r} {rate}Hz x{channels}; "
            f"expected one of {sorted(_DECODERS)} at {_RATE}Hz x{_CHANNELS}"
        )
    return encoding


def parse(raw: str | bytes, *, encoding: str = "PCMU") -> MediaEvent | None:
    """Parse one Telnyx frame.  ``None`` for anything unintelligible.

    ``encoding`` carries the codec agreed in the ``start`` frame; the caller
    threads it back in because Telnyx does not repeat it on every media
    frame the way the payload type does in RTP.
    """
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(msg, dict):
        return None

    event = str(msg.get("event", ""))
    stream_id = str(msg.get("stream_id", "") or "")

    if event == "connected":
        return MediaEvent(kind="connected")

    if event == "start":
        start = msg.get("start") or {}
        if not isinstance(start, dict):
            return None
        fmt = start.get("media_format") or {}
        codec = _encoding(fmt) if isinstance(fmt, dict) and fmt else encoding
        params: dict[str, str] = {}
        raw_params = start.get("parameters") or start.get("custom_parameters") or {}
        if isinstance(raw_params, dict):
            params = {str(k): str(v) for k, v in raw_params.items()}
        client_state = start.get("client_state")
        if client_state:
            params.setdefault("client_state", str(client_state))
        return MediaEvent(
            kind="start",
            stream_id=stream_id,
            # call_control_id is stable for the call; call_session_id is not
            # guaranteed present on every deployment.
            call_id=str(start.get("call_control_id", "") or start.get("call_session_id", "") or ""),
            sample_rate=_RATE,
            params={**params, "encoding": codec},
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
        decode = _DECODERS.get(encoding.upper())
        if decode is None:
            raise MediaFormatError(f"no decoder for {encoding!r}")
        return MediaEvent(
            kind="media",
            stream_id=stream_id,
            track=_as_track(media.get("track")),
            pcm=decode(payload),
            sample_rate=_RATE,
        )

    if event == "stop":
        stop = msg.get("stop") or {}
        call_id = str(stop.get("call_control_id", "") or "") if isinstance(stop, dict) else ""
        return MediaEvent(kind="stop", stream_id=stream_id, call_id=call_id)

    return MediaEvent(kind="other", stream_id=stream_id)
