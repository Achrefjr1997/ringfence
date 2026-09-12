"""Live verification dispatch -- the decision to contact someone.

Watches the same ``rf.*.decision`` stream as ``guardian.py`` and
``intervene_dispatch.py``, but has to do one thing none of them do:
reconstruct **who the caller claimed to be**. That is not on the decision
event -- ``packages/pipeline/pipeline.py`` strips ``evidence`` before
publishing, so an INTERVENE says ``AUTH_CLAIM`` fired but not that the caller
said "Amazon". The name comes from the ``rf.*.turn`` stream instead, and only
ever from **CALLER** turns.

The CALLER-only rule is invariant #1's idea applied to an outward action: a
frightened person repeating "Amazon" back at the scammer is exactly what
happens on these calls, and it must never be what causes Amazon to be
contacted.

Unlike ``InterventionDispatcher`` -- whose only side effect is a bus publish
for a live viewer, and which is therefore deliberately *not* gated by
``RF_DRY_RUN`` -- this one reaches outside. The guard that proves it is in
``packages/verify/agent.py::may_open_session``; this module adds independent
refusals (replay mode, the replay tenant, the master switch) as defence in
depth, not as the load-bearing one.
"""

from __future__ import annotations

import contextlib
import logging
from collections import OrderedDict
from typing import Any, Protocol

from packages.contracts.audio import Mode
from packages.contracts.events import EventBus
from packages.contracts.verify import VerificationOutcome
from packages.intervene.templates import select_template
from packages.verify.budget import VerificationBudget
from packages.verify.directory import Directory, Institution, get_directory

log = logging.getLogger("ringfence.verify")

_REPLAY_TENANT = "replay"

# Enough recent CALLER speech to catch the institution's name, bounded so a
# long call cannot grow it without limit (invariant #6). The name is almost
# always said in the opening seconds, so this is generous.
_BUFFER_TURNS = 64


class Verifier(Protocol):
    """The seam Phase 2 fills with the real Voice Agent session."""

    async def verify(
        self, *, institution: Institution, amount: str | None, mode: Mode
    ) -> VerificationOutcome | None: ...


def _template_for(outcome: VerificationOutcome) -> str:
    if outcome.verified is True:
        return "VERIFY_CONFIRMED"
    if outcome.verified is False:
        return "VERIFY_UNCONFIRMED"
    # None: the session ran but nobody answered, or it timed out. Not a denial.
    return "VERIFY_FAILED"


class VerificationDispatcher:
    def __init__(
        self,
        bus: EventBus,
        *,
        verifier: Verifier,
        directory: Directory | None = None,
        budget: VerificationBudget | None = None,
        enabled: bool = False,
    ) -> None:
        self._bus = bus
        self._verifier = verifier
        self._directory = directory or get_directory()
        self._budget = budget or VerificationBudget()
        self._enabled = enabled
        # session_id -> recent CALLER text, oldest evicted first.
        self._buffer: OrderedDict[str, list[str]] = OrderedDict()

    async def run(self) -> None:
        """Consume turns and decisions until cancelled."""
        if not self._enabled:
            log.info("verification disabled (RF_VERIFY_ENABLED unset); dispatcher idle")
            return
        async with contextlib.aclosing(self._bus.subscribe("rf.*")) as stream:
            async for subject, payload in stream:
                if subject.endswith(".turn"):
                    await self.on_turn(subject, payload)
                elif subject.endswith(".decision"):
                    await self.on_decision(subject, payload)

    # -- the caller's own words -------------------------------------------

    async def on_turn(self, subject: str, payload: dict[str, Any]) -> None:
        if not self._enabled or payload.get("role") != "CALLER":
            return
        session_id = str(payload.get("session_id", ""))
        text = str(payload.get("text", "")).strip()
        if not session_id or not text:
            return
        said = self._buffer.setdefault(session_id, [])
        self._buffer.move_to_end(session_id)
        said.append(text)
        del said[:-_BUFFER_TURNS]
        while len(self._buffer) > _BUFFER_TURNS:
            self._buffer.popitem(last=False)

    # -- the decision to act ----------------------------------------------

    async def on_decision(self, subject: str, payload: dict[str, Any]) -> None:
        if not self._enabled or payload.get("state") != "INTERVENE":
            return
        tenant = _tenant_of(subject)
        if not tenant or tenant == _REPLAY_TENANT:
            return
        # A fixture replayed into a *real* tenant. The tenant string cannot
        # see this, which is why `mode` is on the event at all.
        if str(payload.get("mode", "")) == Mode.REPLAY.value:
            return

        session_id = str(payload.get("session_id", ""))
        decision_id = str(payload.get("decision_id", ""))
        t = float(payload.get("t", 0.0))
        language = str(payload.get("language") or "en")

        institution = self._directory.resolve(" ".join(self._buffer.get(session_id, [])))
        if institution is None:
            await self._publish(
                tenant, session_id, decision_id, t, stage="skipped", reason="no_institution"
            )
            return

        refusal = self._budget.refuse_reason(session_id=session_id, tenant=tenant)
        if refusal is not None:
            await self._publish(
                tenant,
                session_id,
                decision_id,
                t,
                stage="skipped",
                reason=refusal,
                institution=institution,
            )
            return

        self._budget.claim(session_id=session_id, tenant=tenant)
        try:
            await self._publish(
                tenant, session_id, decision_id, t, stage="dialing", institution=institution
            )
            outcome = await self._verifier.verify(
                institution=institution, amount=None, mode=_mode_of(payload)
            )
        except Exception:  # noqa: BLE001 - a verification must never break the bus loop
            log.exception("verification failed", extra={"session_id": session_id})
            outcome = None
        finally:
            self._budget.release()

        if outcome is None:
            # The guard refused, or it raised. Either way nothing was learnt.
            await self._publish(
                tenant, session_id, decision_id, t, stage="failed", institution=institution
            )
            return

        await self._publish(
            tenant,
            session_id,
            decision_id,
            t,
            stage="result",
            institution=institution,
            outcome=outcome,
        )
        # Also on the existing warning subject, so the console's coach banner
        # renders it in every language templates.yaml already carries -- no
        # front-end change needed for the headline moment.
        template_id = _template_for(outcome)
        _, text = select_template(template_id, language)
        await self._bus.publish(
            f"rf.{tenant}.warning",
            {
                "session_id": session_id,
                "decision_id": decision_id,
                "t": t,
                "template_id": template_id,
                "language": language,
                "text": text.replace("{institution}", institution.display_name),
                "simulated": outcome.simulated,
            },
        )

    async def _publish(
        self,
        tenant: str,
        session_id: str,
        decision_id: str,
        t: float,
        *,
        stage: str,
        reason: str | None = None,
        institution: Institution | None = None,
        outcome: VerificationOutcome | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "session_id": session_id,
            "decision_id": decision_id,
            "t": t,
            "stage": stage,
            "reason": reason,
            "institution": institution.id if institution else None,
            "institution_display": institution.display_name if institution else None,
            "verified": outcome.verified if outcome else None,
            "confidence": outcome.confidence if outcome else None,
            "detail": outcome.reason if outcome else None,
            "simulated": outcome.simulated if outcome else False,
        }
        await self._bus.publish(f"rf.{tenant}.verification", payload)


def _tenant_of(subject: str) -> str:
    parts = subject.split(".")
    return parts[1] if len(parts) > 2 else ""


def _mode_of(payload: dict[str, Any]) -> Mode:
    raw = str(payload.get("mode", ""))
    for mode in Mode:
        if mode.value == raw:
            return mode
    return Mode.SDK
