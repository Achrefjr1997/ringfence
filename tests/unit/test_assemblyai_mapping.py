"""Offline coverage for the AssemblyAI provider's pure parts: message ->
``contracts.Turn`` mapping, URL shape, and the bounded send queue that
must drop rather than block the capture path.  The live socket is covered
by tests/integration/test_assemblyai.py (``-m needs_key``).
"""

import json

from packages.asr.assemblyai import (
    _FATAL_CLOSE_CODES,
    AssemblyAIStream,
    _turn_from_message,
)
from packages.asr.provider import StreamSpec
from packages.contracts.transcript import Turn

SPEC = StreamSpec(session_id="s1", leg_id="far", language="en")


def test_turn_from_message_maps_every_field() -> None:
    msg = {
        "type": "Turn",
        "turn_order": 3,
        "transcript": "read me the code",
        "end_of_turn": True,
        "turn_is_formatted": True,
        "end_of_turn_confidence": 0.91,
        "words": [
            {"text": "read", "start": 1000, "end": 1200, "confidence": 0.98},
            {"text": "me", "start": 1200, "end": 1300, "confidence": 0.97},
            {"text": "the", "start": 1300, "end": 1450, "confidence": 0.99},
            {"text": "code", "start": 1450, "end": 1800, "confidence": 0.95},
        ],
    }
    turn = _turn_from_message(msg, session_id="abc", leg_id="far", language="en")
    assert isinstance(turn, Turn)
    assert turn.session_id == "abc"
    assert turn.leg_id == "far"
    assert turn.turn_order == 3
    assert turn.text == "read me the code"
    assert turn.is_final is True
    assert turn.is_formatted is True
    assert turn.language == "en"
    assert turn.confidence == 0.91
    assert [w.text for w in turn.words] == ["read", "me", "the", "code"]
    assert turn.words[0].start == 1.0  # ms -> s
    assert turn.words[-1].end == 1.8
    assert turn.t_start == 1.0
    assert turn.t_end == 1.8


def test_turn_from_message_without_words() -> None:
    msg = {"type": "Turn", "turn_order": 0, "transcript": "hi", "end_of_turn": False}
    turn = _turn_from_message(msg, session_id="s", leg_id="far", language=None)
    assert turn.words == ()
    assert turn.t_start == 0.0 and turn.t_end == 0.0
    assert turn.confidence == 1.0
    assert turn.is_final is False
    assert turn.is_formatted is False


def test_url_has_sample_rate_and_format_turns() -> None:
    stream = AssemblyAIStream("k", SPEC)
    assert stream.url.startswith("wss://streaming.assemblyai.com/v3/ws?")
    assert "sample_rate=16000" in stream.url
    assert "format_turns=true" in stream.url
    assert "language=en" in stream.url


async def test_feed_coalesces_to_100ms_chunks() -> None:
    stream = AssemblyAIStream("k", SPEC)
    # ten 40 ms browser frames -> four 100 ms chunks queued, 0 dropped
    for _ in range(10):
        await stream.feed(b"\x00\x00" * 640)
    assert stream._out.qsize() == 4 and stream.dropped_frames == 0
    assert all(len(stream._out.get_nowait()) == 3200 for _ in range(4))  # 100 ms @ 16 kHz


async def test_feed_drops_when_send_queue_is_full_and_never_blocks() -> None:
    stream = AssemblyAIStream("k", SPEC, send_queue_max=4)
    chunk = b"\x00\x00" * 1600  # exactly one 100 ms chunk
    for _ in range(4):
        await stream.feed(chunk)
    assert stream.dropped_frames == 0
    for _ in range(10):
        await stream.feed(chunk)  # queue full: must drop, not hang
    assert stream.dropped_frames == 10


async def test_close_flushes_a_partial_tail_above_the_minimum() -> None:
    stream = AssemblyAIStream("k", SPEC)
    await stream.feed(b"\x00\x00" * 640)  # 40 ms -> buffered, nothing queued yet
    assert stream._out.qsize() == 0
    await stream.close()  # 40 ms < 50 ms minimum -> dropped, not sent
    assert stream._out.qsize() == 0

    s2 = AssemblyAIStream("k", SPEC)
    await s2.feed(b"\x00\x00" * 960)  # 60 ms -> above the 50 ms minimum
    await s2.close()
    assert s2._out.qsize() == 1 and len(s2._out.get_nowait()) == 1920


async def test_error_message_is_fatal_and_stops_the_recv_loop() -> None:
    stream = AssemblyAIStream("k", SPEC)

    class FakeWS:
        def __aiter__(self):  # noqa: ANN204
            return self

        async def __anext__(self) -> str:
            if not getattr(self, "_sent", False):
                self._sent = True
                return json.dumps({"type": "Error", "error": "Input Duration Violation: 40.0 ms"})
            raise StopAsyncIteration

    await stream._recv_loop(FakeWS())  # type: ignore[arg-type]
    assert stream.fatal is not None and "40.0 ms" in stream.fatal
    assert 3007 in _FATAL_CLOSE_CODES and 1008 in _FATAL_CLOSE_CODES
