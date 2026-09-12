"""AssemblyAI Voice Agent session -- Phase 0: the guard, and nothing else.

Opening a session here is not like calling the Tier-2 judge. It costs money
per minute *and* it starts a real conversation with a third party who did not
ask to be contacted. That is an external side effect in precisely the sense
invariant #4 means, so the guard is built and proved first; the conversation
logic lands in Phase 2 behind it.

Why the guard cannot reuse invariant #4's existing mechanism: that test spies
on ``httpx.AsyncClient.post``, and nothing on this path posts. The side effect
is the WebSocket connect, so the guard sits immediately before it and the
invariant spies the injected ``connect`` instead.
"""

from __future__ import annotations

from typing import Protocol

from packages.contracts.audio import Mode
from packages.contracts.verify import VerificationOutcome

AGENT_WS = "wss://agents.assemblyai.com/v1/ws"


class Connect(Protocol):
    """The WebSocket dialer, injected so tests (and the invariant) can watch
    it without a network."""

    async def __call__(self, url: str, **kw: object) -> object: ...


def may_open_session(*, dry_run: bool, mode: Mode) -> bool:
    """Invariant #4, as a predicate.

    Kept as a free function rather than folded into the session so the
    invariant can assert on the rule itself, not merely on one caller's
    observance of it.
    """
    return not (dry_run or mode is Mode.REPLAY)


class VoiceAgentSession:
    def __init__(
        self,
        *,
        api_key: str,
        connect: Connect,
        dry_run: bool = True,
    ) -> None:
        if not api_key:
            # A configuration error, not a guard decision. Raising keeps a
            # missing key from looking like the guard doing its job.
            raise ValueError("VoiceAgentSession needs an api_key")
        self._api_key = api_key
        self._connect = connect
        self._dry_run = dry_run

    async def start(self, *, mode: Mode) -> VerificationOutcome | None:
        """``None`` means the guard refused -- the session never opened.

        Distinct from a ``VerificationOutcome`` carrying ``verified=None``,
        which means it *did* open and came back without an answer.
        """
        if not may_open_session(dry_run=self._dry_run, mode=mode):
            return None
        await self._connect(
            AGENT_WS, additional_headers={"Authorization": f"Bearer {self._api_key}"}
        )
        # Phase 2 owns everything past here: session.update, the audio pumps,
        # the tool call, session.end in a finally.
        raise NotImplementedError("the verification conversation lands in Phase 2")
