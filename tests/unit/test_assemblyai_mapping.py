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
    assert turn.speaker_label is None


def test_url_has_sample_rate_and_format_turns() -> None:
    stream = AssemblyAIStream("k", SPEC)
    assert stream.url.startswith("wss://streaming.assemblyai.com/v3/ws?")
    assert "sample_rate=16000" in stream.url
    assert "format_turns=true" in stream.url
    assert "language=en" in stream.url
    assert "speaker_labels" not in stream.url  # opt-in only, not default cost/risk


# -- diarization plumbing (public beta upstream -- a cross-check signal, ------
# -- never something detection depends on) ------------------------------


def test_diarize_off_by_default_is_silent_on_the_wire() -> None:
    """SPEC does not set diarize, so nothing about it should appear -- a
    provider that ignores an unknown param silently is a worse failure mode
    than one that never sends it."""
    assert AssemblyAIStream("k", SPEC).url.count("speaker") == 0


def test_diarize_true_adds_speaker_labels_to_the_url() -> None:
    spec = StreamSpec(session_id="s1", leg_id="far", language="en", diarize=True)
    assert "speaker_labels=true" in AssemblyAIStream("k", spec).url


def test_max_speakers_only_appears_when_diarize_is_on() -> None:
    """A stray max_speakers with diarize=False would be a silent no-op on
    AssemblyAI's side -- catch the mistake in our own URL instead."""
    spec = StreamSpec(session_id="s1", leg_id="far", diarize=False, max_speakers=2)
    assert "max_speakers" not in AssemblyAIStream("k", spec).url

    spec2 = StreamSpec(session_id="s1", leg_id="far", diarize=True, max_speakers=2)
    assert "max_speakers=2" in AssemblyAIStream("k", spec2).url


def test_turn_from_message_carries_the_speaker_label_when_present() -> None:
    msg = {
        "type": "Turn",
        "turn_order": 1,
        "transcript": "your account is locked",
        "end_of_turn": True,
        "speaker_label": "B",
        "words": [
            {"text": "your", "start": 0, "end": 200, "confidence": 0.9, "speaker": "B"},
            {"text": "account", "start": 200, "end": 600, "confidence": 0.9, "speaker": "B"},
        ],
    }
    turn = _turn_from_message(msg, session_id="s", leg_id="mixed", language=None)
    assert turn.speaker_label == "B"
    assert [w.speaker_label for w in turn.words] == ["B", "B"]


def test_turn_from_message_speaker_label_absent_when_diarize_was_off() -> None:
    """The ordinary shape -- no speaker_label key at all, not an empty one --
    since diarize=False means AssemblyAI never sends the field."""
    msg = {
        "type": "Turn",
        "turn_order": 0,
        "transcript": "hello",
        "end_of_turn": True,
        "words": [{"text": "hello", "start": 0, "end": 300, "confidence": 0.9}],
    }
    turn = _turn_from_message(msg, session_id="s", leg_id="far", language=None)
    assert turn.speaker_label is None
    assert turn.words[0].speaker_label is None


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


class _ScriptedWS:
    """Yields a fixed list of message dicts as JSON, then stops."""

    def __init__(self, msgs: list[dict]) -> None:
        self._msgs = list(msgs)

    def __aiter__(self):  # noqa: ANN204
        return self

    async def __anext__(self) -> str:
        if not self._msgs:
            raise StopAsyncIteration
        return json.dumps(self._msgs.pop(0))


def _turn_msg(text: str, *, final: bool, formatted: bool) -> dict:
    return {
        "type": "Turn",
        "turn_order": 0,
        "transcript": text,
        "end_of_turn": final,
        "turn_is_formatted": formatted,
        "words": [{"text": text, "start": 0, "end": 900, "confidence": 0.9}],
    }


async def test_recv_loop_forwards_only_the_formatted_final_turn() -> None:
    stream = AssemblyAIStream("k", SPEC)  # SPEC has format_turns default True
    await stream._recv_loop(  # type: ignore[arg-type]
        _ScriptedWS(
            [
                _turn_msg("can you", final=False, formatted=False),
                _turn_msg("can you confirm the name", final=False, formatted=False),
                _turn_msg("can you confirm the name on the account", final=True, formatted=False),
                _turn_msg("Can you confirm the name on the account?", final=True, formatted=True),
            ]
        )
    )
    out = []
    while not stream._turns.empty():
        out.append(stream._turns.get_nowait())
    assert [t.text for t in out] == ["Can you confirm the name on the account?"]


async def test_recv_loop_forwards_the_unformatted_final_when_formatting_is_off() -> None:
    spec = StreamSpec(session_id="s1", leg_id="far", language="en", format_turns=False)
    stream = AssemblyAIStream("k", spec)
    await stream._recv_loop(  # type: ignore[arg-type]
        _ScriptedWS(
            [
                _turn_msg("hello", final=False, formatted=False),
                _turn_msg("hello there", final=True, formatted=False),
            ]
        )
    )
    out = []
    while not stream._turns.empty():
        out.append(stream._turns.get_nowait())
    assert [t.text for t in out] == ["hello there"]


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
