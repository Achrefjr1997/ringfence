"""Twilio Media Streams ingress.

One process, one public surface, two routes:

    POST /voice   the TwiML webhook Twilio hits when a call arrives
    WS   /media   the audio fork Twilio opens because that TwiML asked for it

The gateway is untouched — this speaks to it as an ordinary capture client,
exactly as ``apps/siprec`` does, so a call forked from a CPaaS looks like
any other two-leg session.

Why this shape and not a handset SDK: call audio cannot be taken off a
phone.  iOS never exposed it and Android closed the Accessibility-API
workaround in May 2022.  Taking it from the network is the only path, and it
has the better property anyway — nothing is installed on the protected
person's phone, and they cannot turn it off.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qsl

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from apps.twilio.twiml import reject, stream_and_dial, verify_signature
from packages.ingress.capture_uplink import CaptureUplink, UplinkConfig
from packages.ingress.mediastream import MediaFormatError, leg_for_track
from packages.ingress.mediastream.twilio import parse
from packages.media.normalise import AudioNormaliser, SessionNormaliser

log = logging.getLogger("ringfence.twilio")

_SRC_RATE = 8_000


@dataclass(frozen=True, slots=True)
class Config:
    auth_token: str = ""  # Twilio account auth token -- signs the webhook
    public_url: str = ""  # https://<tunnel-or-host>, how Twilio reaches us
    dial_to: str = ""  # the protected person's real phone, E.164
    gateway_ws: str = "ws://localhost:8000/ws/capture"
    api_key: str = ""
    tenant: str | None = None
    consent_token: str | None = None
    language: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        e = env if env is not None else os.environ
        return cls(
            auth_token=e.get("RF_TWILIO_AUTH_TOKEN", ""),
            public_url=e.get("RF_TWILIO_PUBLIC_URL", "").rstrip("/"),
            dial_to=e.get("RF_TWILIO_DIAL_TO", ""),
            gateway_ws=e.get("RF_GATEWAY_WS", "ws://localhost:8000/ws/capture"),
            api_key=e.get("RF_TWILIO_API_KEY", ""),
            tenant=e.get("RF_TWILIO_TENANT") or None,
            consent_token=e.get("RF_TWILIO_CONSENT_TOKEN") or None,
            language=e.get("RF_TWILIO_LANGUAGE") or None,
        )

    @property
    def stream_url(self) -> str:
        base = self.public_url.replace("https://", "wss://").replace("http://", "ws://")
        return f"{base}/media"


class _Session:
    """One forked call: two legs, and **one** gain shared between them.

    Per-leg gain is forbidden — ``packages/media/normalise.py`` explains why
    at length.  Role inference reads the *relative* level between the two
    parties, and normalising each leg independently erases exactly that.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._gain = SessionNormaliser()
        self._legs: dict[str, AudioNormaliser] = {}

    def frames(self, leg: str, pcm_bytes: bytes) -> list[bytes]:
        norm = self._legs.get(leg)
        if norm is None:
            norm = AudioNormaliser(src_rate=_SRC_RATE, session=self._gain)
            self._legs[leg] = norm
        return norm.process(pcm_bytes)


def create_app(config: Config | None = None, *, uplink: CaptureUplink | None = None) -> Starlette:
    cfg = config or Config.from_env()
    up = uplink or CaptureUplink(
        UplinkConfig(
            gateway_ws=cfg.gateway_ws,
            api_key=cfg.api_key,
            mode="carrier",
            tenant=cfg.tenant,
            consent_token=cfg.consent_token,
            language=cfg.language,
        )
    )

    async def voice(request: Request) -> Response:
        """Twilio's webhook. Fork both tracks, then ring the real phone."""
        # Twilio posts application/x-www-form-urlencoded.  Parsed with
        # stdlib rather than Starlette's request.form(), which drags in
        # python-multipart for a multipart body Twilio never sends.
        body = (await request.body()).decode("utf-8", "replace")
        params = dict(parse_qsl(body, keep_blank_values=True))
        url = f"{cfg.public_url}{request.url.path}"
        if not verify_signature(
            auth_token=cfg.auth_token,
            url=url,
            params=params,
            signature=request.headers.get("X-Twilio-Signature", ""),
        ):
            # Unsigned means not Twilio. Refuse rather than place a call.
            log.warning("rejected unsigned voice webhook from %s", request.client)
            return Response(reject("bad signature"), media_type="text/xml", status_code=403)

        to = params.get("To", "")
        frm = params.get("From", "")
        log.info("inbound call %s -> %s", frm, to)
        return Response(
            stream_and_dial(stream_url=cfg.stream_url, dial_to=cfg.dial_to),
            media_type="text/xml",
        )

    async def media(ws: WebSocket) -> None:
        """The audio fork. One socket in, one capture leg per track out."""
        await ws.accept()
        session: _Session | None = None
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    event = parse(raw)
                except MediaFormatError:
                    # A codec we cannot decode is a misconfiguration, not a
                    # blip: keeping the socket open would feed the detector
                    # noise that scores like silence.
                    log.exception("unsupported media format; closing stream")
                    break
                if event is None:
                    continue

                if event.kind == "start":
                    sid = event.call_id or event.stream_id
                    session = _Session(sid)
                    user = event.params.get("user", "")
                    if user:
                        up.set_attribution(sid, user, event.params.get("user_label", ""))
                    log.info("stream open session=%s params=%s", sid, sorted(event.params))

                elif event.kind == "media" and session is not None and event.pcm is not None:
                    leg = leg_for_track(event.track)
                    for frame in session.frames(leg, event.pcm.astype("<i2").tobytes()):
                        await up.send(session.session_id, leg, frame)

                elif event.kind == "stop":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            if session is not None:
                await up.close_session(session.session_id)

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True, **up.stats()})

    async def metrics(_: Request) -> PlainTextResponse:
        lines = [f"ringfence_{k} {v}" for k, v in up.stats().items()]
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    app = Starlette(
        routes=[
            Route("/voice", voice, methods=["POST"]),
            WebSocketRoute("/media", media),
            Route("/health", health),
            Route("/metrics", metrics),
        ]
    )
    app.state.uplink = up
    app.state.config = cfg
    return app
