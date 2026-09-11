"""AssemblyAI v3 streaming provider (production §5.2).

Protocol specifics that bite if you get them wrong:

* ``wss://streaming.assemblyai.com/v3/ws?sample_rate=16000&format_turns=true``
* ``Authorization: <key>`` header with **no ``Bearer`` prefix** — a 4xx on
  connect is almost always this.
* audio goes up as **binary** frames, PCM16 little-endian mono
* messages down are JSON: ``Begin`` / ``Turn`` / ``Termination``
* close by sending ``{"type": "Terminate"}``
* a session is capped at 3 h; reconnect at 2h45m and carry the last 30 s of
  audio so the new session has acoustic context.

The send path never blocks the caller: a bounded queue (200 frames) drops
on overflow rather than stalling capture.  Downstream only ever sees
``contracts.transcript.Turn`` — never a message dict.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from packages.asr.provider import ASRCapabilities, StreamSpec
from packages.contracts.transcript import Turn, Word

log = logging.getLogger("ringfence.asr")

_DEFAULT_WS = "wss://streaming.assemblyai.com/v3/ws"
_SEND_QUEUE_MAX = 200
_SESSION_MAX_S = 3 * 60 * 60
_RECONNECT_AT_S = 2 * 60 * 60 + 45 * 60
_STITCH_CONTEXT_S = 30.0
# v3 bills the wall-clock time the socket is open, idle included -- an
# unclosed session can bill for hours.  We open one per leg per call, and
# under SIPREC the hangup belongs to an SBC we do not control, so a stuck leg
# is an unbounded, silent cost leak.  This is the backstop.
_MAX_SESSION_S = 2 * 60 * 60

# v3 streaming rejects audio chunks outside 50–1000 ms; the browser worklet
# emits 40 ms frames, so we coalesce to ~100 ms before sending.
_MIN_CHUNK_MS = 50
_TARGET_CHUNK_MS = 100

# close codes that mean "do not reconnect" — auth (1008) and every 3xxx/4xxx
# application error (bad chunk duration, quota, …).  Reconnecting on these
# just storms the session quota.
_FATAL_CLOSE_CODES = frozenset({1008}) | frozenset(range(3000, 5000))


def _turn_from_message(
    msg: dict[str, Any], *, session_id: str, leg_id: str, language: str | None
) -> Turn:
    raw_words = msg.get("words") or []
    words = tuple(
        Word(
            text=str(w["text"]),
            start=float(w["start"]) / 1000.0,
            end=float(w["end"]) / 1000.0,
            confidence=float(w.get("confidence", 1.0)),
        )
        for w in raw_words
    )
    t_start = words[0].start if words else 0.0
    t_end = words[-1].end if words else t_start
    conf = msg.get("end_of_turn_confidence")
    confidence = (
        float(conf)
        if conf is not None
        else (sum(w.confidence for w in words) / len(words) if words else 1.0)
    )
    return Turn(
        session_id=session_id,
        leg_id=leg_id,
        turn_order=int(msg.get("turn_order", 0)),
        text=str(msg.get("transcript", "")),
        is_final=bool(msg.get("end_of_turn", False)),
        is_formatted=bool(msg.get("turn_is_formatted", False)),
        t_start=t_start,
        t_end=t_end,
        words=words,
        confidence=confidence,
        language=language,
    )


class AssemblyAIStream:
    def __init__(
        self,
        api_key: str,
        spec: StreamSpec,
        *,
        base_url: str = _DEFAULT_WS,
        send_queue_max: int = _SEND_QUEUE_MAX,
        reconnect_at_s: float = _RECONNECT_AT_S,
        stitch_context_s: float = _STITCH_CONTEXT_S,
        max_session_s: float = _MAX_SESSION_S,
    ) -> None:
        self._api_key = api_key
        self._spec = spec
        self._base_url = base_url
        self._reconnect_at_s = reconnect_at_s
        self._stitch_context_s = stitch_context_s
        self._max_session_s = max_session_s
        self._opened_at: float | None = None
        self.capped = False  # closed by the wall-clock backstop, not by the caller

        self._out: asyncio.Queue[bytes] = asyncio.Queue(maxsize=send_queue_max)
        self._turns: asyncio.Queue[Turn | None] = asyncio.Queue()
        self._closed = asyncio.Event()
        self._connected = asyncio.Event()

        self._ws: ClientConnection | None = None
        self._runner: asyncio.Task[None] | None = None
        self._session_id = spec.session_id
        self._dropped_frames = 0
        self._fatal: str | None = None
        # Coalesce sub-50 ms feeds into ~100 ms chunks the API will accept.
        self._send_buf = bytearray()
        self._min_chunk = int(_MIN_CHUNK_MS / 1000 * spec.sample_rate) * 2
        self._target_chunk = int(_TARGET_CHUNK_MS / 1000 * spec.sample_rate) * 2
        # Rolling acoustic context for session stitching (§5.3).
        self._recent: deque[bytes] = deque()
        self._recent_bytes = 0
        self._ctx_cap = int(self._stitch_context_s * spec.sample_rate * 2)  # PCM16

    # -- lifecycle ---------------------------------------------------------

    @property
    def url(self) -> str:
        q = f"?sample_rate={self._spec.sample_rate}"
        q += f"&format_turns={'true' if self._spec.format_turns else 'false'}"
        if self._spec.language:
            q += f"&language={self._spec.language}"
        return self._base_url + q

    async def start(self) -> None:
        self._runner = asyncio.create_task(self._run())
        done, _ = await asyncio.wait(
            {self._runner, asyncio.create_task(self._connected.wait())},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if self._runner in done:  # finished before it came up
            self._runner.result()  # re-raise a connect exception
        if self._fatal is not None and not self._connected.is_set():
            raise RuntimeError(f"AssemblyAI refused the stream: {self._fatal}")

    def _budget_left(self) -> float:
        if self._opened_at is None:
            return self._max_session_s
        return self._max_session_s - (time.monotonic() - self._opened_at)

    async def _run(self) -> None:
        self._opened_at = time.monotonic()
        try:
            while not self._closed.is_set():
                async with connect(
                    self.url, additional_headers={"Authorization": self._api_key}
                ) as ws:
                    self._ws = ws
                    self._connected.set()
                    await self._resend_context()
                    sender = asyncio.create_task(self._send_loop(ws))
                    receiver = asyncio.create_task(self._recv_loop(ws))
                    # whichever comes first: the stitch reconnect, or the
                    # wall-clock backstop that ends the billing outright
                    budget = self._budget_left()
                    timer = asyncio.create_task(asyncio.sleep(min(self._reconnect_at_s, budget)))
                    try:
                        await asyncio.wait(
                            {sender, receiver, timer, asyncio.create_task(self._closed.wait())},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                    finally:
                        for task in (sender, receiver, timer):
                            task.cancel()
                        with contextlib.suppress(Exception):
                            await asyncio.gather(sender, receiver, timer, return_exceptions=True)
                    if self._budget_left() <= 0:
                        self.capped = True
                        log.warning(
                            "ASR session hit the %.0fs wall-clock cap; closing to stop billing",
                            self._max_session_s,
                        )
                        break
                    if self._fatal is not None:
                        break  # auth / quota / protocol error — do not reconnect
                    if (
                        receiver.done()
                        and not receiver.cancelled()
                        and receiver.exception() is None
                    ):
                        break  # clean Termination from the server
                    # otherwise: reconnect timer fired or socket dropped — loop
        finally:
            self._ws = None
            self._closed.set()
            self._turns.put_nowait(None)

    async def _send_loop(self, ws: ClientConnection) -> None:
        try:
            while True:
                frame = await self._out.get()
                await ws.send(frame)
        except ConnectionClosed:
            return

    async def _recv_loop(self, ws: ClientConnection) -> None:
        try:
            async for raw in ws:
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "Begin":
                    self._session_id = str(msg.get("id", self._session_id))
                elif kind == "Turn":
                    turn = _turn_from_message(
                        msg,
                        session_id=self._session_id,
                        leg_id=self._spec.leg_id,
                        language=self._spec.language,
                    )
                    # v3 sends a Turn on every partial update of the current
                    # utterance; forward only the settled turn -- the
                    # formatted one when format_turns is on -- so downstream
                    # (role attribution, the transcript UI) sees each
                    # utterance once, not a growing ladder of prefixes.
                    if turn.is_final and (turn.is_formatted or not self._spec.format_turns):
                        self._turns.put_nowait(turn)
                elif kind == "Error":
                    self._fatal = str(msg.get("error", "unknown error"))
                    return
                elif kind == "Termination":
                    return
        except ConnectionClosed as exc:
            code = getattr(exc, "code", None)
            if code in _FATAL_CLOSE_CODES:
                self._fatal = self._fatal or f"connection closed {code}"
                return
            raise  # transient — let _run reconnect

    async def _resend_context(self) -> None:
        blob = b"".join(self._recent)
        for i in range(0, len(blob), self._target_chunk):
            with contextlib.suppress(asyncio.QueueFull):
                self._out.put_nowait(blob[i : i + self._target_chunk])

    # -- ASRStream -------------------------------------------------------

    def _enqueue(self, chunk: bytes) -> None:
        try:
            self._out.put_nowait(chunk)
        except asyncio.QueueFull:
            self._dropped_frames += 1  # drop, never block the capture path

    async def feed(self, pcm: bytes) -> None:
        self._recent.append(pcm)
        self._recent_bytes += len(pcm)
        while self._recent_bytes > self._ctx_cap and len(self._recent) > 1:
            self._recent_bytes -= len(self._recent.popleft())

        self._send_buf += pcm
        while len(self._send_buf) >= self._target_chunk:
            self._enqueue(bytes(self._send_buf[: self._target_chunk]))
            del self._send_buf[: self._target_chunk]

    async def turns(self) -> AsyncIterator[Turn]:
        while True:
            item = await self._turns.get()
            if item is None:
                return
            yield item

    async def close(self) -> None:
        if self._closed.is_set():
            return
        if len(self._send_buf) >= self._min_chunk:
            self._enqueue(bytes(self._send_buf))  # flush the tail
        self._send_buf.clear()
        ws = self._ws
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.send(json.dumps({"type": "Terminate"}))
        self._closed.set()
        if self._runner is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._runner, timeout=5.0)

    @property
    def dropped_frames(self) -> int:
        return self._dropped_frames

    @property
    def fatal(self) -> str | None:
        return self._fatal


class AssemblyAIStreaming:
    """An :class:`~packages.asr.provider.ASRProvider` over AssemblyAI v3."""

    name = "assemblyai"
    capabilities = ASRCapabilities(
        languages=("en", "fr", "es", "de", "it", "pt", "nl"),
        diarisation=False,
        keyterms=True,
        max_concurrency=None,
    )

    def __init__(self, api_key: str, *, base_url: str = _DEFAULT_WS) -> None:
        if not api_key:
            raise ValueError("AssemblyAI requires an API key")
        self._api_key = api_key
        self._base_url = base_url

    async def open(self, spec: StreamSpec) -> AssemblyAIStream:
        stream = AssemblyAIStream(self._api_key, spec, base_url=self._base_url)
        await stream.start()
        return stream
