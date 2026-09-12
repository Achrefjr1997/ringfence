"""Phase 0 of the verification agent: the guard, and nothing else.

The agent opens a paid, real-time conversation with a third party. That is an
external side effect in exactly the sense invariant #4 means, so the guard
ships *before* the thing it guards -- there is deliberately no conversation
logic in the agent yet, only the decision about whether it may run at all.

Note this guard cannot be caught by invariant #4's existing spy on
``httpx.AsyncClient.post``: there is no POST anywhere on this path. The side
effect is the WebSocket connect, so that is what both the guard and the
invariant have to sit on.
"""

from __future__ import annotations

import pytest

from packages.contracts.audio import Mode
from packages.verify.agent import VoiceAgentSession, may_open_session


class _RecordingConnect:
    """Stands in for ``websockets.connect``. Records every attempt so a test
    can assert on attempts that should never have happened."""

    def __init__(self) -> None:
        self.opened: list[str] = []

    async def __call__(self, url: str, **kw: object) -> object:
        self.opened.append(url)
        raise AssertionError("Phase 0 has no conversation; connect must not be awaited past this")


# -- the predicate, on its own ----------------------------------------------


@pytest.mark.parametrize("mode", [Mode.CARRIER, Mode.SDK, Mode.ENTERPRISE])
def test_a_live_session_in_a_live_mode_may_open(mode: Mode) -> None:
    assert may_open_session(dry_run=False, mode=mode) is True


@pytest.mark.parametrize("mode", [Mode.CARRIER, Mode.SDK, Mode.ENTERPRISE, Mode.REPLAY])
def test_dry_run_never_opens_whatever_the_mode(mode: Mode) -> None:
    assert may_open_session(dry_run=True, mode=mode) is False


def test_replay_never_opens_even_when_not_dry_run() -> None:
    """A fixture replayed into a real tenant is still a replay. This is the
    case the bus could not even see before `mode` was added to the decision
    event -- see packages/pipeline/pipeline.py."""
    assert may_open_session(dry_run=False, mode=Mode.REPLAY) is False


# -- the session honours it --------------------------------------------------


async def test_a_guarded_session_never_touches_the_socket() -> None:
    connect = _RecordingConnect()
    session = VoiceAgentSession(api_key="k", connect=connect, dry_run=True)
    assert await session.start(mode=Mode.CARRIER) is None
    assert connect.opened == []


async def test_a_replay_session_never_touches_the_socket() -> None:
    connect = _RecordingConnect()
    session = VoiceAgentSession(api_key="k", connect=connect, dry_run=False)
    assert await session.start(mode=Mode.REPLAY) is None
    assert connect.opened == []


async def test_a_live_session_does_reach_the_socket() -> None:
    """Without this the two tests above prove nothing -- a session that never
    connects under any circumstance would pass them both."""
    connect = _RecordingConnect()
    session = VoiceAgentSession(api_key="k", connect=connect, dry_run=False)
    with pytest.raises(AssertionError):  # _RecordingConnect stops us here, by design
        await session.start(mode=Mode.CARRIER)
    assert connect.opened == ["wss://agents.assemblyai.com/v1/ws"]


async def test_an_absent_api_key_is_refused_before_the_guard_is_even_consulted() -> None:
    """A missing key is a configuration error, not a dry-run. Failing loudly
    here stops a silent no-op being mistaken for the guard working."""
    with pytest.raises(ValueError, match="api_key"):
        VoiceAgentSession(api_key="", connect=_RecordingConnect(), dry_run=False)
