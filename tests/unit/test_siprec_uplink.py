"""The P3 chain end to end: loopback SRC -> SiprecSrs -> SiprecUplink ->
a stand-in ``/ws/capture``.  Asserts the gateway would see one binary
WebSocket per leg, keyed and tagged the way the browser two-socket path is.
"""

import asyncio
from urllib.parse import parse_qs, urlparse

import numpy as np
from websockets.asyncio.server import serve

from apps.siprec.app import Config, SiprecUplink
from packages.ingress.siprec.loopback import play_call


class _FakeCapture:
    """Records what each /ws/capture connection carried."""

    def __init__(self) -> None:
        self.conns: list[dict[str, object]] = []

    async def handler(self, ws: object) -> None:
        query = parse_qs(urlparse(ws.request.path).query)  # type: ignore[attr-defined]
        rec: dict[str, object] = {
            "session": query.get("session", [""])[0],
            "leg": query.get("leg", [""])[0],
            "key": query.get("key", [""])[0],
            "frames": 0,
            "bytes": 0,
        }
        self.conns.append(rec)
        try:
            async for msg in ws:  # type: ignore[attr-defined]
                if isinstance(msg, bytes):
                    rec["frames"] = int(rec["frames"]) + 1
                    rec["bytes"] = int(rec["bytes"]) + len(msg)
        except Exception:  # noqa: BLE001 — a test stub; any close ends it
            pass


async def test_loopback_call_reaches_capture_as_two_tagged_legs() -> None:
    fake = _FakeCapture()
    server = await serve(fake.handler, "127.0.0.1", 0)
    cap_port = server.sockets[0].getsockname()[1]

    cfg = Config(
        bind_host="127.0.0.1",
        bind_port=0,
        advertise_ip="127.0.0.1",
        gateway_ws=f"ws://127.0.0.1:{cap_port}/ws/capture",
        api_key="rf_testkey",
        caller_aor="sip:caller@pstn.example",
    )
    uplink = SiprecUplink(cfg)
    srs_addr = await uplink.start()

    # 0.4 s per leg of low-level tone, 8 kHz mono.
    t = np.arange(3200) / 8000.0
    far = (0.2 * 32767 * np.sin(2 * np.pi * 300 * t)).astype(np.int16)
    near = (0.2 * 32767 * np.sin(2 * np.pi * 600 * t)).astype(np.int16)

    try:
        await play_call(srs_addr, far, near, caller_aor="sip:caller@pstn.example", speed=0.0)
        await asyncio.sleep(0.3)  # let the pump drain to the fake gateway
    finally:
        await uplink.close()
        server.close()
        await server.wait_closed()

    legs = {str(c["leg"]) for c in fake.conns}
    assert legs == {"far", "near"}
    assert all(c["key"] == "rf_testkey" for c in fake.conns)
    assert all(c["session"] == "loopback" for c in fake.conns)
    assert all(int(c["frames"]) > 0 for c in fake.conns)
    assert all(int(c["bytes"]) % (640 * 2) == 0 for c in fake.conns)  # whole 40 ms frames


async def test_uplink_close_is_idempotent_and_hangs_up_cleanly() -> None:
    fake = _FakeCapture()
    server = await serve(fake.handler, "127.0.0.1", 0)
    cap_port = server.sockets[0].getsockname()[1]
    cfg = Config(
        bind_host="127.0.0.1",
        bind_port=0,
        gateway_ws=f"ws://127.0.0.1:{cap_port}/ws/capture",
        api_key="k",
    )
    uplink = SiprecUplink(cfg)
    await uplink.start()
    try:
        await uplink.close()
        await uplink.close()  # second call must not raise
    finally:
        server.close()
        await server.wait_closed()
