"""wire.py: every protocol assumption, one test each.

These are written against AssemblyAI's published Voice Agent spec. If the
live API disagrees, the failure should point here and nowhere else.
"""

from __future__ import annotations

import base64
import json

import pytest

from packages.verify import wire


def test_session_update_declares_pcm16_24k_on_both_directions() -> None:
    msg = json.loads(wire.session_update(system_prompt="sp", greeting="hello"))
    assert msg["type"] == "session.update"
    s = msg["session"]
    assert s["input"]["format"]["encoding"] == "audio/pcm"
    assert s["output"]["format"]["encoding"] == "audio/pcm"
    assert s["system_prompt"] == "sp" and s["greeting"] == "hello"
    assert s["input"]["turn_detection"]["interrupt_response"] is True


def test_session_update_carries_the_report_tool() -> None:
    s = json.loads(wire.session_update(system_prompt="sp", greeting="g"))["session"]
    (tool,) = s["tools"]
    assert tool["type"] == "function" and tool["name"] == "report_verification"
    assert tool["parameters"]["required"] == ["verified", "reason"]


def test_input_audio_round_trips_the_bytes() -> None:
    pcm = bytes(range(256)) * 10
    msg = json.loads(wire.input_audio(pcm))
    assert msg["type"] == "input.audio"
    assert base64.b64decode(msg["audio"]) == pcm


def test_tool_result_is_a_json_string_not_an_object() -> None:
    """The spec wants a string. An object is silently wrong."""
    msg = json.loads(wire.tool_result("call_1", {"ok": True}))
    assert msg["type"] == "tool.result" and msg["call_id"] == "call_1"
    assert isinstance(msg["result"], str)
    assert json.loads(msg["result"]) == {"ok": True}


def test_session_end() -> None:
    assert json.loads(wire.session_end()) == {"type": "session.end"}


# -- server events -----------------------------------------------------------


def test_reply_audio_is_read_from_data_not_audio() -> None:
    pcm = b"\x01\x02\x03\x04"
    ev = wire.read_agent_event(
        json.dumps({"type": "reply.audio", "data": base64.b64encode(pcm).decode()})
    )
    assert ev is not None and ev.kind == "reply.audio" and ev.audio == pcm


def test_reply_audio_under_the_wrong_key_is_rejected() -> None:
    pcm = base64.b64encode(b"\x00\x01").decode()
    assert wire.read_agent_event(json.dumps({"type": "reply.audio", "audio": pcm})) is None


def test_undecodable_reply_audio_is_rejected_not_raised() -> None:
    assert wire.read_agent_event(json.dumps({"type": "reply.audio", "data": "!!not b64!!"})) is None


def test_tool_call_is_narrowed() -> None:
    ev = wire.read_agent_event(
        json.dumps(
            {
                "type": "tool.call",
                "call_id": "c9",
                "name": "report_verification",
                "arguments": {"verified": False, "reason": "no record"},
            }
        )
    )
    assert ev is not None and ev.kind == "tool.call"
    assert ev.call_id == "c9" and ev.name == "report_verification"
    assert ev.arguments == {"verified": False, "reason": "no record"}


@pytest.mark.parametrize("spelling", ["session.error", "error"])
def test_both_error_spellings_map_to_one_kind(spelling: str) -> None:
    ev = wire.read_agent_event(
        json.dumps({"type": spelling, "code": "UNAUTHORIZED", "message": "bad key"})
    )
    assert ev is not None and ev.kind == "session.error"
    assert ev.code == "UNAUTHORIZED" and ev.message == "bad key"


def test_transcripts_ready_and_done_carry_their_fields() -> None:
    user = wire.read_agent_event(json.dumps({"type": "transcript.user", "text": "no record"}))
    agent = wire.read_agent_event(
        json.dumps({"type": "transcript.agent", "text": "thanks", "interrupted": True})
    )
    ready = wire.read_agent_event(json.dumps({"type": "session.ready", "session_id": "sess_a"}))
    done = wire.read_agent_event(json.dumps({"type": "reply.done", "status": "interrupted"}))
    assert user is not None and user.text == "no record"
    assert agent is not None and agent.interrupted is True
    assert ready is not None and ready.session_id == "sess_a"
    assert done is not None and done.status == "interrupted"


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", '"a string"', "{}", '{"type": 7}'])
def test_malformed_frames_are_none_never_raise(raw: str) -> None:
    assert wire.read_agent_event(raw) is None


def test_an_unknown_event_type_is_preserved_not_dropped() -> None:
    ev = wire.read_agent_event(json.dumps({"type": "input.speech.started"}))
    assert ev is not None and ev.kind == "input.speech.started"


# -- the report --------------------------------------------------------------


def test_a_well_formed_report_becomes_an_outcome() -> None:
    out = wire.report_from_arguments(
        {"verified": False, "reason": "we have no record", "confidence": "high"}, duration_s=12.0
    )
    assert out is not None and out.verified is False and out.confidence == "high"
    assert out.duration_s == 12.0 and out.simulated is False


@pytest.mark.parametrize(
    "args",
    [
        {"verified": "no", "reason": "x"},  # a string is not a boolean answer
        {"verified": False},  # no reason
        {"verified": True, "reason": "   "},  # blank reason
        {},
    ],
)
def test_an_unusable_report_is_none(args: dict[str, object]) -> None:
    assert wire.report_from_arguments(args, duration_s=1.0) is None


def test_an_invalid_confidence_is_dropped_not_trusted() -> None:
    out = wire.report_from_arguments(
        {"verified": True, "reason": "yes we called", "confidence": "certain"}, duration_s=1.0
    )
    assert out is not None and out.confidence is None


def test_a_long_reason_is_bounded() -> None:
    out = wire.report_from_arguments(
        {"verified": False, "reason": " ".join(["word"] * 80)}, duration_s=1.0
    )
    assert out is not None and len(out.reason.split()) == 25
