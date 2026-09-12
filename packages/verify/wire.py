"""AssemblyAI Voice Agent API frames -- pure codecs, no I/O.

Every assumption this feature makes about the wire lives in this one file, so
a protocol surprise is a one-function fix with one test, not an archaeology
exercise across the session and the bridge.

Confirmed against AssemblyAI's published API spec and their raw-WebSocket and
Python guides (not guessed):

* endpoint ``wss://agents.assemblyai.com/v1/ws``, ``Authorization: Bearer``;
* audio is ``audio/pcm`` -- PCM16 little-endian mono at 24 kHz, base64 -- in
  both directions, declared explicitly on ``input`` and ``output``;
* agent audio arrives in ``reply.audio`` under the key **``data``**, not
  ``audio`` (the client-sent ``input.audio`` does use ``audio``);
* ``tool.result.result`` is a JSON-encoded **string**, not an object;
* errors arrive as ``session.error`` *or* plain ``error``, each with ``code``
  and ``message``.

Nothing typed ``Any`` leaves this module: raw frames are narrowed through
``_as_dict`` and exposed as a frozen :class:`AgentEvent`.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from packages.contracts.verify import VerificationOutcome

SAMPLE_RATE = 24_000
ENCODING = "audio/pcm"
REPORT_TOOL = "report_verification"

# Agent-side reasons are quoted from what a stranger said. Bound them before
# they reach the bus or a banner.
_MAX_REASON_WORDS = 25

REPORT_TOOL_SCHEMA: dict[str, object] = {
    "type": "function",
    "name": REPORT_TOOL,
    "description": (
        "Call this exactly once, as soon as the person you are speaking to gives "
        "a clear answer about whether their organisation placed the call, or tells "
        "you they cannot answer."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "verified": {
                "type": "boolean",
                "description": "true only if they confirmed their organisation placed the call",
            },
            "confidence": {"type": "string", "enum": ["high", "low"]},
            "reason": {
                "type": "string",
                "description": "at most 20 words, quoting what they actually said",
            },
        },
        "required": ["verified", "reason"],
    },
}


# -- client -> server --------------------------------------------------------


def session_update(*, system_prompt: str, greeting: str, voice: str = "ivy") -> str:
    return json.dumps(
        {
            "type": "session.update",
            "session": {
                "system_prompt": system_prompt,
                "greeting": greeting,
                "input": {
                    "format": {"encoding": ENCODING},
                    # Barge-in on: if the desk interrupts, the agent stops
                    # talking rather than reading over them.
                    "turn_detection": {"interrupt_response": True},
                },
                "output": {"voice": voice, "format": {"encoding": ENCODING}},
                "tools": [REPORT_TOOL_SCHEMA],
            },
        }
    )


def input_audio(pcm16: bytes) -> str:
    return json.dumps({"type": "input.audio", "audio": base64.b64encode(pcm16).decode("ascii")})


def tool_result(call_id: str, result: Mapping[str, object]) -> str:
    # The spec requires `result` to be a JSON *string*. An object here is
    # silently wrong in a way no type checker would catch.
    return json.dumps({"type": "tool.result", "call_id": call_id, "result": json.dumps(result)})


def session_end() -> str:
    """Stops billing immediately. Closing the socket alone does not."""
    return json.dumps({"type": "session.end"})


# -- server -> client --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentEvent:
    kind: str
    text: str | None = None
    audio: bytes | None = None
    call_id: str | None = None
    name: str | None = None
    arguments: Mapping[str, object] = field(default_factory=dict)
    status: str | None = None
    code: str | None = None
    message: str | None = None
    session_id: str | None = None
    interrupted: bool = False


def _as_dict(raw: str | bytes) -> dict[str, object] | None:
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return {str(k): v for k, v in parsed.items()}


def _str(d: Mapping[str, object], key: str) -> str | None:
    v = d.get(key)
    return v if isinstance(v, str) else None


def read_agent_event(raw: str | bytes) -> AgentEvent | None:
    """One server frame, narrowed. ``None`` for anything malformed -- never
    raises, because a bad frame must not tear down a live conversation."""
    d = _as_dict(raw)
    if d is None:
        return None
    kind = _str(d, "type")
    if kind is None:
        return None
    if kind == "error":
        kind = "session.error"  # the spec allows both spellings for one event

    if kind == "reply.audio":
        data = _str(d, "data")
        if data is None:
            return None
        try:
            audio = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            return None
        return AgentEvent(kind=kind, audio=audio)

    if kind == "tool.call":
        args = d.get("arguments")
        arguments = {str(k): v for k, v in args.items()} if isinstance(args, dict) else {}
        return AgentEvent(
            kind=kind, call_id=_str(d, "call_id"), name=_str(d, "name"), arguments=arguments
        )

    return AgentEvent(
        kind=kind,
        text=_str(d, "text"),
        status=_str(d, "status"),
        code=_str(d, "code"),
        message=_str(d, "message"),
        session_id=_str(d, "session_id"),
        interrupted=d.get("interrupted") is True,
    )


def report_from_arguments(
    arguments: Mapping[str, object], *, duration_s: float
) -> VerificationOutcome | None:
    """A ``report_verification`` call, validated. ``None`` if the model sent
    something that is not a usable answer -- the caller then treats the
    verification as unanswered rather than guessing what was meant."""
    verified = arguments.get("verified")
    reason = arguments.get("reason")
    if not isinstance(verified, bool) or not isinstance(reason, str) or not reason.strip():
        return None
    raw_conf = arguments.get("confidence")
    confidence: Literal["high", "low"] | None = (
        "high" if raw_conf == "high" else "low" if raw_conf == "low" else None
    )
    return VerificationOutcome(
        verified=verified,
        reason=" ".join(reason.split()[:_MAX_REASON_WORDS]),
        confidence=confidence,
        duration_s=duration_s,
    )
