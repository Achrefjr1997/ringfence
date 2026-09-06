import asyncio

import numpy as np
import pytest

from packages.ingress.audiosocket import (
    KIND_HANGUP,
    KIND_UUID,
    AudioSocketDecoder,
    AudioSocketServer,
    Message,
    frame_message,
    rate_for_type,
)


@pytest.mark.parametrize(
    "type_byte, rate",
    [(0x10, 8_000), (0x12, 16_000), (0x15, 44_100), (0x16, 48_000), (0x18, 192_000)],
)
def test_rate_is_read_from_the_type_byte(type_byte: int, rate: int) -> None:
    assert rate_for_type(type_byte) == rate
    assert Message(type_byte, b"").rate == rate and Message(type_byte, b"").is_audio
    assert rate_for_type(KIND_UUID) is None


def test_decoder_reassembles_frames_split_across_chunks() -> None:
    wire = frame_message(KIND_UUID, b"\x01" * 16) + frame_message(0x10, b"ab" * 160)
    dec = AudioSocketDecoder()
    assert dec.feed(wire[:5]) == []  # header + a bit, not a whole frame
    assert dec.feed(wire[5:20]) == [Message(KIND_UUID, b"\x01" * 16)]
    msgs = dec.feed(wire[20:])
    assert msgs == [Message(0x10, b"ab" * 160)]
    assert dec.pending == 0


def test_decoder_yields_multiple_frames_from_one_chunk() -> None:
    wire = frame_message(0x12, b"\x00\x00" * 100) + frame_message(KIND_HANGUP, b"")
    msgs = AudioSocketDecoder().feed(wire)
    assert [m.type for m in msgs] == [0x12, KIND_HANGUP]


async def test_server_normalises_using_the_type_byte_rate_not_16k() -> None:
    frames: list[tuple[str, bytes]] = []
    hung: list[str] = []

    async def on_audio(sid: str, frame: bytes) -> None:
        frames.append((sid, frame))

    async def on_hangup(sid: str) -> None:
        hung.append(sid)

    server = AudioSocketServer(on_audio=on_audio, on_hangup=on_hangup)
    host, port = await server.start()
    try:
        reader, writer = await asyncio.open_connection(host, port)
        uuid = bytes(range(16))
        writer.write(frame_message(KIND_UUID, uuid))
        # 1 s of 8 kHz audio (type 0x10) -> after resample, ~1 s of 16 kHz / 40 ms frames
        pcm8k = (np.zeros(8_000, dtype="<i2")).tobytes()
        for i in range(0, len(pcm8k), 640):
            writer.write(frame_message(0x10, pcm8k[i : i + 640]))
        writer.write(frame_message(KIND_HANGUP, b""))
        await writer.drain()
        await asyncio.sleep(0.2)
        writer.close()
    finally:
        await server.close()

    assert server.last_source_rate == 8_000  # NOT assumed to be 16k
    assert frames, "no normalised frames delivered"
    assert all(len(f) == 640 * 2 for _, f in frames)  # 16 kHz / 40 ms
    assert frames[0][0] == uuid.hex()
    assert hung == [uuid.hex()]
    # 1 s of 8 kHz in -> ~1 s of 16 kHz / 40 ms frames out (~25), within a couple
    assert abs(len(frames) - 25) <= 3


def test_frame_message_roundtrips_and_rejects_oversize() -> None:
    assert AudioSocketDecoder().feed(frame_message(0x14, b"xyz")) == [Message(0x14, b"xyz")]
    with pytest.raises(ValueError):
        frame_message(0x10, b"x" * 70_000)
