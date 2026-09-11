"""One WebSocket to ``/ws/capture`` per ``(session, leg)``.

Every network ingress ends the same way: decoded 40 ms frames have to reach
the gateway, one socket per leg, with the leg id carrying the role.  That
plumbing was written for SIPREC (``apps/siprec``); this is the same code
with the SIPREC parts taken out so a second ingress does not copy it.

To the gateway a leg is just a capture socket — exactly what the browser
two-socket path opens — so nothing downstream needs to know which CPaaS,
SBC or handset produced it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from urllib.parse import urlencode

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

log = logging.getLogger("ringfence.uplink")


@dataclass(frozen=True, slots=True)
class UplinkConfig:
    gateway_ws: str = "ws://localhost:8000/ws/capture"
    api_key: str = ""
    mode: str = "carrier"
    tenant: str | None = None
    consent_token: str | None = None
    language: str | None = None


class _LegWs:
    """One capture socket plus a reader that surfaces admission rejections.

    The reader matters: without it a rejected session looks identical to a
    silent one, and ``/ws/capture`` reports refusal as a JSON message rather
    than by closing.
    """

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


class CaptureUplink:
    """Lazily opens one capture socket per ``(session, leg)`` and fans frames
    to it.  Sockets open on first frame, so a leg that never speaks never
    costs an ASR session."""

    def __init__(self, config: UplinkConfig) -> None:
        self._cfg = config
        self._legs: dict[tuple[str, str], _LegWs] = {}
        self._opening: dict[tuple[str, str], asyncio.Lock] = {}
        self._attrib: dict[str, tuple[str, str]] = {}
        self.legs_opened = 0
        self.connect_failures = 0

    def set_attribution(self, session_id: str, user_ref: str, user_label: str = "") -> None:
        """Name the employee this session belongs to, before any leg opens."""
        if user_ref:
            self._attrib[session_id] = (user_ref[:200], user_label[:200])

    async def send(self, session_id: str, leg: str, frame: bytes) -> None:
        ws = self._legs.get((session_id, leg)) or await self._open(session_id, leg)
        if ws is not None:
            await ws.send(frame)

    async def close_session(self, session_id: str) -> None:
        self._attrib.pop(session_id, None)
        for key in [k for k in self._legs if k[0] == session_id]:
            await self._legs.pop(key).close()

    async def close(self) -> None:
        await asyncio.gather(
            *(ws.close() for ws in list(self._legs.values())), return_exceptions=True
        )
        self._legs.clear()
        self._attrib.clear()

    def stats(self) -> dict[str, int]:
        return {
            "capture_legs_open": len(self._legs),
            "capture_legs_opened_total": self.legs_opened,
            "capture_connect_failures_total": self.connect_failures,
        }

    async def _open(self, session_id: str, leg: str) -> _LegWs | None:
        key = (session_id, leg)
        if key in self._legs:
            return self._legs[key]
        lock = self._opening.setdefault(key, asyncio.Lock())
        async with lock:
            if key in self._legs:
                return self._legs[key]
            query = {
                "session": session_id,
                "leg": leg,
                "key": self._cfg.api_key,
                "mode": self._cfg.mode,
            }
            if self._cfg.tenant:
                query["tenant"] = self._cfg.tenant
            if self._cfg.consent_token:
                query["consent"] = self._cfg.consent_token
            if self._cfg.language:
                query["lang"] = self._cfg.language
            attrib = self._attrib.get(session_id)
            if attrib:
                query["user"], query["user_label"] = attrib
            url = f"{self._cfg.gateway_ws}?{urlencode(query)}"
            try:
                ws = await connect(url, open_timeout=5)
            except (OSError, ConnectionClosed, asyncio.TimeoutError) as exc:
                self.connect_failures += 1
                log.warning("capture connect failed sid=%s leg=%s: %s", session_id, leg, exc)
                return None
            leg_ws = _LegWs(ws, session_id, leg)
            self._legs[key] = leg_ws
            self.legs_opened += 1
            self._opening.pop(key, None)
            return leg_ws
