"""Offline coverage for the AssemblyAI provider's pure parts: message ->
``contracts.Turn`` mapping, URL shape, and the bounded send queue that
must drop rather than block the capture path.  The live socket is covered
by tests/integration/test_assemblyai.py (``-m needs_key``).
"""

from packages.asr.assemblyai import AssemblyAIStream, _turn_from_message
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


async def test_feed_drops_when_send_queue_is_full_and_never_blocks() -> None:
    stream = AssemblyAIStream("k", SPEC, send_queue_max=4)
    for _ in range(4):
        await stream.feed(b"\x00\x00" * 160)
    assert stream.dropped_frames == 0
    for _ in range(10):
        await stream.feed(b"\x00\x00" * 160)  # queue full: must drop, not hang
    assert stream.dropped_frames == 10
