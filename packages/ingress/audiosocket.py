"""Asterisk AudioSocket adapter (T-6.1).

Wire format: ``1 byte type | 2 byte big-endian length | payload``.

    0x00              hangup / end of stream
    0x01              16-byte call UUID (the session id)
    0x03              error
    0x10 .. 0x18      slin PCM16 mono LE at 8 / 12 / 16 / 24 / 32 / 44.1 / 48 /
                      96 / 192 kHz

**The sample rate is read from the type byte.**  Older Asterisk builds emit
8 kHz even when you asked for 16 kHz, so assuming 16 kHz silently halves
every timestamp.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from packages.media.normalise import AudioNormaliser

KIND_HANGUP = 0x00
KIND_UUID = 0x01
KIND_ERROR = 0x03

_RATE_BY_TYPE: dict[int, int] = {
    0x10: 8_000,
    0x11: 12_000,
    0x12: 16_000,
    0x13: 24_000,
    0x14: 32_000,
    0x15: 44_100,
    0x16: 48_000,
    0x17: 96_000,
    0x18: 192_000,
}


def rate_for_type(type_byte: int) -> int | None:
    return _RATE_BY_TYPE.get(type_byte)


def frame_message(type_byte: int, payload: bytes) -> bytes:
    if len(payload) > 0xFFFF:
        raise ValueError("AudioSocket payload exceeds 65535 bytes")
    return bytes([type_byte]) + len(payload).to_bytes(2, "big") + payload


@dataclass(frozen=True, slots=True)
class Message:
    type: int
    payload: bytes

    @property
    def is_audio(self) -> bool:
        return self.type in _RATE_BY_TYPE

    @property
    def rate(self) -> int | None:
        return _RATE_BY_TYPE.get(self.type)


class AudioSocketDecoder:
    """Streaming frame parser — feed it arbitrary chunks, get whole messages."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[Message]:
        self._buf += data
        out: list[Message] = []
        while len(self._buf) >= 3:
            length = int.from_bytes(self._buf[1:3], "big")
            if len(self._buf) < 3 + length:
                break
            out.append(Message(self._buf[0], bytes(self._buf[3 : 3 + length])))
            del self._buf[: 3 + length]
        return out

    @property
    def pending(self) -> int:
        return len(self._buf)


OnAudio = Callable[[str, bytes], Awaitable[None]]
OnHangup = Callable[[str], Awaitable[None]]


class AudioSocketServer:
    """TCP server: decode AudioSocket, resample each leg to 16 kHz / 40 ms
    using the rate from its type byte, and hand frames to ``on_audio``."""

    def __init__(self, *, on_audio: OnAudio, on_hangup: OnHangup | None = None) -> None:
        self._on_audio = on_audio
        self._on_hangup = on_hangup
        self._server: asyncio.Server | None = None
        self.last_source_rate: int | None = None  # for tests / observability

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> tuple[str, int]:
        self._server = await asyncio.start_server(self._handle, host, port)
        sock = self._server.sockets[0].getsockname()
        return sock[0], sock[1]

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        decoder = AudioSocketDecoder()
        normaliser: AudioNormaliser | None = None
        session_id = ""
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                for msg in decoder.feed(chunk):
                    if msg.type == KIND_UUID:
                        session_id = msg.payload.hex()
                    elif msg.type == KIND_HANGUP:
                        if self._on_hangup is not None:
                            await self._on_hangup(session_id)
                        return
                    elif msg.is_audio and msg.rate is not None:
                        if normaliser is None:
                            normaliser = AudioNormaliser(src_rate=msg.rate)
                            self.last_source_rate = msg.rate
                        for frame in normaliser.process(msg.payload):
                            await self._on_audio(session_id, frame)
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
