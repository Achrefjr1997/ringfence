"""The real verifier: ring the desk, then let the Voice Agent ask.

Implements ``apps/gateway/verify_dispatch.py::Verifier``, replacing
``simulated.py`` when ``RF_VERIFY_AGENT=voice_agent``. It owns the order of
events, and the order is the point:

1. **the guard first** -- a session that could never open must not even ring
   the desk, or a dry-run would still page a human;
2. offer the desk a single-use ticket and wait for it to answer (unanswered ->
   ``verified=None``, never ``False``);
3. only then open the paid Voice Agent session, streaming its transcript back
   as ``transcript`` progress so the protected person watches it happen.

Its outcomes are never ``simulated``: a conversation genuinely took place.
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping

from packages.contracts.audio import Mode
from packages.contracts.verify import Progress, VerificationOutcome, VerificationStage
from packages.verify.agent import VoiceAgentSession
from packages.verify.desk import DeskExchange
from packages.verify.directory import Institution


async def _emit(
    progress: Progress | None, stage: VerificationStage, detail: Mapping[str, str]
) -> None:
    if progress is None:
        return
    with contextlib.suppress(Exception):  # a viewer's trouble never stops a verification
        await progress(stage, detail)


def _verdict(outcome: VerificationOutcome | None) -> str:
    if outcome is None or outcome.verified is None:
        return "unknown"
    return "true" if outcome.verified else "false"


class VoiceAgentVerifier:
    def __init__(self, *, session: VoiceAgentSession, exchange: DeskExchange) -> None:
        self._session = session
        self._exchange = exchange

    async def verify(
        self,
        *,
        institution: Institution,
        amount: str | None,
        mode: Mode,
        progress: Progress | None = None,
    ) -> VerificationOutcome | None:
        if not self._session.may_open(mode):
            return None

        offer = self._exchange.offer(institution)
        if offer is None:
            return VerificationOutcome(
                verified=None, reason="the verification desk is busy", error="desk_busy"
            )
        await _emit(progress, "ringing", {})
        line = await self._exchange.wait_answer(offer)
        if line is None:
            return VerificationOutcome(
                verified=None,
                reason=f"the {institution.display_name} desk did not answer",
                error="no_answer",
            )

        await _emit(progress, "connected", {})

        async def on_transcript(role: str, text: str) -> None:
            await _emit(progress, "transcript", {"role": role, "text": text})

        outcome: VerificationOutcome | None = None
        try:
            outcome = await self._session.start(
                mode=mode,
                institution=institution,
                amount=amount,
                desk=line,
                on_transcript=on_transcript,
            )
            return outcome
        finally:
            line.close({"type": "ended", "verified": _verdict(outcome)})
