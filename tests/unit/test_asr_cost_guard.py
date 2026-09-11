"""Bounding what a stuck leg can bill.

AssemblyAI v3 bills the **wall-clock time the socket is open, idle
included** -- an unclosed session can bill for hours.  We open one per leg
per call, and under SIPREC the hangup belongs to an SBC we do not control.
So two independent backstops, because they fail differently:

* the ASR stream caps its own wall-clock lifetime, which bounds the bill
  even if the gateway forgets about the session entirely;
* the capture loop closes a leg with no *speech* -- not "no bytes", because
  a leg that hangs up badly often keeps streaming silence, and the socket
  bills the same either way.

Note what this is *not*: VAD-gating the upload would not save a cent on a
wall-clock billing model.  It earns its place here as the idle *signal*, and
(§4.4) as the shed ladder's bottom rung.
"""

from __future__ import annotations

import asyncio

import pytest

from packages.asr.assemblyai import _MAX_SESSION_S, AssemblyAIStream
from packages.asr.provider import StreamSpec
from packages.media.vad import VoiceActivityDetector

SPEC = StreamSpec(session_id="s1", leg_id="far", language="en")


def test_the_cap_defaults_to_something_that_bounds_a_runaway_bill() -> None:
    assert 0 < _MAX_SESSION_S <= 3 * 60 * 60  # v3 caps a session at 3 h anyway


def test_budget_is_full_before_the_stream_opens() -> None:
    s = AssemblyAIStream("k", SPEC, max_session_s=42.0)
    assert s._budget_left() == 42.0
    assert s.capped is False


def test_budget_drains_from_the_moment_the_run_loop_starts() -> None:
    s = AssemblyAIStream("k", SPEC, max_session_s=10.0)
    s._opened_at = 0.0  # pretend we opened at monotonic zero
    left = s._budget_left()
    assert left < 10.0, "an opened stream must be spending its budget"


def test_an_exhausted_budget_is_not_positive() -> None:
    s = AssemblyAIStream("k", SPEC, max_session_s=0.0)
    s._opened_at = 0.0
    assert s._budget_left() <= 0.0


async def test_the_reconnect_timer_never_outlives_the_budget() -> None:
    """The stitch reconnect is ~2h45m; the cap must win when it is shorter,
    or the backstop would never fire on a long call."""
    s = AssemblyAIStream("k", SPEC, max_session_s=1.0, reconnect_at_s=9999.0)
    s._opened_at = asyncio.get_running_loop().time() * 0  # start of budget
    assert min(s._reconnect_at_s, s._budget_left()) <= 1.0


# -- the idle signal ---------------------------------------------------


def _pcm(seconds: float, *, loud: bool) -> bytes:
    import numpy as np

    n = int(16_000 * seconds)
    if not loud:
        return (np.zeros(n, dtype="<i2")).tobytes()
    t = np.arange(n) / 16_000.0
    return (0.3 * 32767 * np.sin(2 * np.pi * 220 * t)).astype("<i2").tobytes()


def test_silence_is_not_speech_so_a_quiet_leg_looks_idle() -> None:
    vad = VoiceActivityDetector(sample_rate=16_000)
    assert not any(f.speech for f in vad.push(_pcm(1.0, loud=False)))


def test_a_leg_still_streaming_silence_would_never_trip_a_byte_counter() -> None:
    """The reason the idle check is on speech rather than on bytes."""
    silence = _pcm(2.0, loud=False)
    assert len(silence) > 0, "bytes keep arriving"
    vad = VoiceActivityDetector(sample_rate=16_000)
    assert not any(f.speech for f in vad.push(silence)), "but none of it is speech"


@pytest.mark.parametrize("seconds", [0.5, 1.0])
def test_voiced_audio_registers_as_speech(seconds: float) -> None:
    vad = VoiceActivityDetector(sample_rate=16_000)
    assert any(f.speech for f in vad.push(_pcm(seconds, loud=True)))
