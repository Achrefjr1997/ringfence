"""Telnyx media-streaming ingress.

The same shape as ``apps/twilio`` -- one Starlette app, one port, a TeXML
webhook and a media WebSocket -- because TeXML is TwiML-compatible and the
audio envelope differs only in spelling.  What actually differs:

* **Ed25519** webhook signatures instead of HMAC-SHA1;
* ``stream_id`` / ``call_control_id`` / ``sample_rate`` naming;
* the codec is negotiable (PCMU or PCMA), agreed in the ``start`` frame and
  threaded into every later media frame.

Telnyx exists in this repo because Twilio would not send an SMS
verification code to a Tunisian number, so the account could not be created
at all.  That is precisely the situation the provider-neutral parser in
``packages/ingress/mediastream`` was built for.
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

from apps.telnyx.webhook import SignatureUnavailableError, verify_signature
from packages.ingress.capture_uplink import CaptureUplink, UplinkConfig, read_gateway_key
from packages.ingress.mediastream import MediaFormatError, leg_for_track
from packages.ingress.mediastream.markup import reject, stream_and_dial, stream_and_hold
from packages.ingress.mediastream.session import StreamSession
from packages.ingress.mediastream.telnyx import parse

log = logging.getLogger("ringfence.telnyx")

# RingFence language codes are bare ("fr"); <Say> wants a locale.
_SAY_LOCALES = {"fr": "fr-FR", "en": "en-US"}


def _say_language(language: str | None) -> str | None:
    return _SAY_LOCALES.get(language or "", None)


@dataclass(frozen=True, slots=True)
class Config:
    public_key: str = ""  # Telnyx account Ed25519 public key (base64), verifies inbound
    public_url: str = ""  # https://<tunnel-or-host> Telnyx reaches us on
    dial_to: str = ""  # the protected person's phone, E.164; empty => hold mode
    hold_notice: str = ""  # spoken before holding, when there is no second leg
    gateway_ws: str = "ws://localhost:8000/ws/capture"
    api_key: str = ""  # RingFence key, authenticates us outbound -- not a Telnyx one
    tenant: str | None = None
    consent_token: str | None = None
    language: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        e = env if env is not None else os.environ
        return cls(
            public_key=e.get("RF_TELNYX_PUBLIC_KEY", ""),
            public_url=e.get("RF_TELNYX_PUBLIC_URL", "").rstrip("/"),
            dial_to=e.get("RF_TELNYX_DIAL_TO", ""),
            hold_notice=e.get("RF_TELNYX_HOLD_NOTICE", ""),
            gateway_ws=e.get("RF_GATEWAY_WS", "ws://localhost:8000/ws/capture"),
            api_key=read_gateway_key(e, "RF_TELNYX_GATEWAY_KEY"),
            tenant=e.get("RF_TELNYX_TENANT") or None,
            consent_token=e.get("RF_TELNYX_CONSENT_TOKEN") or None,
            language=e.get("RF_TELNYX_LANGUAGE") or None,
        )

    @property
    def stream_url(self) -> str:
        base = self.public_url.replace("https://", "wss://").replace("http://", "ws://")
        return f"{base}/media"


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
        """TeXML webhook: fork both tracks, then ring the real phone."""
        body = await request.body()
        try:
            ok = verify_signature(
                public_key=cfg.public_key,
                body=body,
                signature=request.headers.get("telnyx-signature-ed25519", ""),
                timestamp=request.headers.get("telnyx-timestamp", ""),
            )
        except SignatureUnavailableError:
            # "cannot verify" is not "forged" -- say so with a different code
            # so a missing dependency is never read as an attack.
            log.exception("cannot verify Telnyx signatures; refusing the call")
            return Response(
                reject("verification unavailable"), media_type="text/xml", status_code=503
            )
        if not ok:
            log.warning("rejected unsigned TeXML webhook from %s", request.client)
            return Response(reject("bad signature"), media_type="text/xml", status_code=403)

        params = dict(parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True))
        log.info("inbound call %s -> %s", params.get("From", ""), params.get("To", ""))
        if not cfg.dial_to:
            # No second leg to bridge -- a trial account cannot place one.
            # Capture still works; every turn just arrives as CALLER.
            log.warning("no RF_TELNYX_DIAL_TO: holding the line, single-leg capture only")
            return Response(
                stream_and_hold(
                    stream_url=cfg.stream_url,
                    notice=cfg.hold_notice or None,
                    language=_say_language(cfg.language),
                ),
                media_type="text/xml",
            )
        return Response(
            stream_and_dial(stream_url=cfg.stream_url, dial_to=cfg.dial_to),
            media_type="text/xml",
        )

    async def media(ws: WebSocket) -> None:
        await ws.accept()
        session: StreamSession | None = None
        codec = "PCMU"
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    event = parse(raw, encoding=codec)
                except MediaFormatError:
                    log.exception("unsupported media format; closing stream")
                    break
                if event is None:
                    continue

                if event.kind == "start":
                    sid = event.call_id or event.stream_id
                    codec = event.params.get("encoding", codec)
                    session = StreamSession(sid)
                    user = event.params.get("user", "")
                    if user:
                        up.set_attribution(sid, user, event.params.get("user_label", ""))
                    log.info("stream open session=%s codec=%s", sid, codec)

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
