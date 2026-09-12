"""Phase 1 verifier: no conversation, and honest about it.

Stands in for the Voice Agent session so the whole path -- the caller names an
institution, the call reaches INTERVENE, "Verifying with...", an outcome on the
banner -- runs end to end with no API key and no cost. It never touches a
network, so it needs no guard of its own; the load-bearing guard for the real
session is ``packages/verify/agent.py::may_open_session``.

Every outcome it returns carries ``simulated=True``, and the console labels the
result accordingly. A banner reading "We called Amazon directly" must never
appear unmarked when nobody called anyone -- that would be the product lying
to the person it exists to protect.
"""

from __future__ import annotations

import asyncio

from packages.contracts.audio import Mode
from packages.contracts.verify import Progress, VerificationOutcome
from packages.verify.directory import Institution


class SimulatedVerifier:
    def __init__(self, *, delay_s: float = 2.0) -> None:
        self._delay_s = delay_s

    async def verify(
        self,
        *,
        institution: Institution,
        amount: str | None,
        mode: Mode,
        progress: Progress | None = None,
    ) -> VerificationOutcome | None:
        # A real verification takes seconds; an instant one would make the
        # demo misrepresent what the live feature feels like.
        await asyncio.sleep(self._delay_s)
        return VerificationOutcome(
            verified=False,
            reason=f"simulated: {institution.display_name} has no record of this call",
            confidence="high",
            duration_s=self._delay_s,
            simulated=True,
        )
