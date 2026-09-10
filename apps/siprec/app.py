"""SRS -> ``/ws/capture`` bridge.

``SiprecUplink`` owns one :class:`SiprecSrs` and, per ``(session_id, leg)``,
one WebSocket to the gateway.  A recorded leg's 40 ms frames go straight
onto its socket as binary; the gateway admits the call on the API key and
treats ``leg=far`` / ``leg=near`` as caller / callee just like the browser
two-socket path.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlencode

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from packages.ingress.siprec.srs import SiprecSession, SiprecSrs

log = logging.getLogger("ringfence.siprec")


@dataclass(frozen=True, slots=True)
class Config:
    bind_host: str = "0.0.0.0"  # noqa: S104 — a recording server listens for the SBC
    bind_port: int = 5060
    advertise_ip: str = "127.0.0.1"
    gateway_ws: str = "ws://localhost:8000/ws/capture"
    api_key: str = ""
    caller_aor: str | None = None
    tenant: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        e = env if env is not None else os.environ
        bind = e.get("RF_SIPREC_BIND", "0.0.0.0:5060")
        host, _, port = bind.rpartition(":")
        return cls(
            bind_host=host or "0.0.0.0",  # noqa: S104
            bind_port=int(port) if port.isdigit() else 5060,
            advertise_ip=e.get("RF_SIPREC_ADVERTISE_IP", "127.0.0.1"),
            gateway_ws=e.get("RF_GATEWAY_WS", "ws://localhost:8000/ws/capture"),
            api_key=e.get("RF_SIPREC_API_KEY", ""),
            caller_aor=e.get("RF_SIPREC_CALLER_AOR") or None,
            tenant=e.get("RF_SIPREC_TENANT") or None,
        )


class _LegWs:
    """One WebSocket to ``/ws/capture`` plus a reader that logs rejections
    and keeps the connection from stalling."""

    def __init__(self, ws: ClientConnection, session_id: str, leg: str) -> None:
        self._ws = ws
        self._sid = session_id
        self._leg = leg
        self._reader = asyncio.ensure_future(self._drain())

    async def _drain(self) -> None:
        with contextlib.suppress(ConnectionClosed):
            async for msg in self._ws:
                text = msg.decode() if isinstance(msg, bytes) else msg
                if "rejected" in text:
                    log.warning("capture rejected sid=%s leg=%s: %s", self._sid, self._leg, text)

    async def send(self, frame: bytes) -> None:
        with contextlib.suppress(ConnectionClosed):
            await self._ws.send(frame)

    async def close(self) -> None:
        self._reader.cancel()
        with contextlib.suppress(Exception):
            await self._ws.close()
        with contextlib.suppress(Exception):
            await self._reader


class SiprecUplink:
    def __init__(self, config: Config) -> None:
        self._cfg = config
        self._srs = SiprecSrs(
            on_audio=self._on_audio,
            on_session_start=self._on_start,
            on_session_end=self._on_end,
            advertise_ip=config.advertise_ip,
            caller_aor=config.caller_aor,
        )
        self._legs: dict[tuple[str, str], _LegWs] = {}
        self._opening: dict[tuple[str, str], asyncio.Lock] = {}

    async def start(self) -> tuple[str, int]:
        return await self._srs.start(self._cfg.bind_host, self._cfg.bind_port)

    async def close(self) -> None:
        await self._srs.close()
        await asyncio.gather(
            *(ws.close() for ws in list(self._legs.values())), return_exceptions=True
        )
        self._legs.clear()

    # -- SRS callbacks ------------------------------------------------

    async def _on_start(self, session: SiprecSession) -> None:
        log.info(
            "session %s up — call-id=%s legs=%s", session.session_id, session.call_id, session.legs
        )
        for leg in session.legs:
            await self._ensure(session.session_id, leg)

    async def _on_audio(self, session_id: str, leg: str, frame: bytes) -> None:
        ws = self._legs.get((session_id, leg)) or await self._ensure(session_id, leg)
        if ws is not None:
            await ws.send(frame)

    async def _on_end(self, session_id: str, reason: str) -> None:
        log.info("session %s down — %s", session_id, reason)
        for key in [k for k in self._legs if k[0] == session_id]:
            await self._legs.pop(key).close()

    # -- ws plumbing ------------------------------------------------

    async def _ensure(self, session_id: str, leg: str) -> _LegWs | None:
        key = (session_id, leg)
        if key in self._legs:
            return self._legs[key]
        lock = self._opening.setdefault(key, asyncio.Lock())
        async with lock:
            if key in self._legs:
                return self._legs[key]
            query = {"session": session_id, "leg": leg, "key": self._cfg.api_key}
            if self._cfg.tenant:
                query["tenant"] = self._cfg.tenant
            url = f"{self._cfg.gateway_ws}?{urlencode(query)}"
            try:
                ws = await connect(url, open_timeout=5)
            except (OSError, ConnectionClosed, asyncio.TimeoutError) as exc:
                log.warning("capture connect failed sid=%s leg=%s: %s", session_id, leg, exc)
                return None
            leg_ws = _LegWs(ws, session_id, leg)
            self._legs[key] = leg_ws
            self._opening.pop(key, None)
            return leg_ws


async def run(config: Config | None = None) -> None:
    cfg = config or Config.from_env()
    if not cfg.api_key:
        raise SystemExit("RF_SIPREC_API_KEY is required")
    uplink = SiprecUplink(cfg)
    host, port = await uplink.start()
    log.info("SIPREC SRS on udp/%s:%d -> %s", host, port, cfg.gateway_ws)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
    finally:
        await uplink.close()
