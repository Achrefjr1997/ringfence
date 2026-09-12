"""The verification desk's HTTP surface: the page, its offers, its audio line.

``packages/verify/desk.py`` holds the logic; this module only adapts it to
Starlette. Four routes, registered only when verification is enabled:

* ``GET /verify-desk`` -- the desk page (``apps/console/verify-desk.html``);
* ``GET /verify-desk/offers`` -- SSE: ``offer`` when the agent rings,
  ``withdrawn`` when it stops. Each carries the institution's name and a
  ticket, nothing about the protected person's call;
* ``WS /ws/verify-desk/{ticket}`` -- the audio line. An unknown, expired or
  already-used ticket is refused **before** ``accept()``, so a guessed or
  replayed ticket never gets as far as an open socket;
* ``WS /ws/verify-desk/echo`` -- the desk microphone straight back to the desk
  speaker, no agent and no cost, time-limited. It exists to prove the
  browser's 24 kHz audio path on its own before any agent is in the loop.

Demo scope: the desk is a page we serve ourselves, reachable by anyone who can
reach the gateway. The ticket is what stops a stranger joining a call already
in progress; the budget in ``packages/verify/budget.py`` is what bounds how
many conversations can exist at all.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

from sse_starlette.sse import EventSourceResponse
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from packages.verify.desk import DeskExchange, DeskLine

_PAGE = Path(__file__).resolve().parents[1] / "console" / "verify-desk.html"
# One desk frame is 50 ms of 24 kHz PCM16: 2,400 bytes. Anything far larger
# did not come from our page, and is not forwarded to a paid session.
_MAX_FRAME_BYTES = 16_000
_ECHO_LIMIT_S = 60.0
REJECT_CODE = 4404


async def bridge(ws: WebSocket, line: DeskLine) -> None:
    """Pump one accepted desk socket against its line until either side ends."""

    async def down() -> None:
        while (item := await line.next_outbound()) is not None:
            if isinstance(item, bytes):
                await ws.send_bytes(item)
            else:
                await ws.send_text(item)

    async def up() -> None:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                return
            data = message.get("bytes")
            if isinstance(data, bytes) and 0 < len(data) <= _MAX_FRAME_BYTES:
                line.push_audio(data[: len(data) - len(data) % 2])

    tasks = [asyncio.create_task(down()), asyncio.create_task(up())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        line.hang_up()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(BaseException):
                await task
        with contextlib.suppress(Exception):
            await ws.close()


def build_verify_desk_routes(exchange: DeskExchange) -> list[Route | WebSocketRoute]:
    async def page(_: Request) -> Response:
        if not _PAGE.is_file():
            return PlainTextResponse("verification desk page missing", status_code=404)
        return FileResponse(_PAGE, media_type="text/html")

    async def offers(_: Request) -> EventSourceResponse:
        async def stream() -> AsyncIterator[dict[str, str]]:
            async with contextlib.aclosing(exchange.notices()) as notices:
                async for kind, offer in notices:
                    yield {"event": kind, "data": json.dumps(offer.public())}

        return EventSourceResponse(stream())

    async def line_ws(ws: WebSocket) -> None:
        line = exchange.redeem(str(ws.path_params["ticket"]))
        if line is None:
            await ws.close(code=REJECT_CODE)  # before accept: the handshake fails
            return
        await ws.accept()
        await bridge(ws, line)

    async def echo_ws(ws: WebSocket) -> None:
        await ws.accept()
        deadline = time.monotonic() + _ECHO_LIMIT_S
        with contextlib.suppress(TimeoutError, WebSocketDisconnect, RuntimeError):
            while (remaining := deadline - time.monotonic()) > 0:
                message = await asyncio.wait_for(ws.receive(), remaining)
                if message["type"] == "websocket.disconnect":
                    return
                data = message.get("bytes")
                if isinstance(data, bytes) and 0 < len(data) <= _MAX_FRAME_BYTES:
                    await ws.send_bytes(data)
        with contextlib.suppress(Exception):
            await ws.close()

    return [
        Route("/verify-desk", page),
        Route("/verify-desk/offers", offers),
        # before the {ticket} route, or "echo" would be redeemed as a ticket
        WebSocketRoute("/ws/verify-desk/echo", echo_ws),
        WebSocketRoute("/ws/verify-desk/{ticket}", line_ws),
    ]
