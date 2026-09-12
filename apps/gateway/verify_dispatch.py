"""Live verification dispatch -- the decision to contact someone.

Watches the same ``rf.*.decision`` stream as ``guardian.py`` and
``intervene_dispatch.py``, but has to do one thing none of them do:
reconstruct **who the caller claimed to be**. That is not on the decision
event -- ``packages/pipeline/pipeline.py`` strips ``evidence`` before
publishing, so an INTERVENE says ``AUTH_CLAIM`` fired but not that the caller
said "Amazon". The name comes from the ``rf.*.turn`` stream instead: CALLER
turns first, unattributed (``UNKNOWN``) turns only as a fallback, and **never
CALLEE** -- see ``_resolve`` for why the fallback exists.

The never-CALLEE rule is invariant #1's idea applied to an outward action: a
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
from collections.abc import Mapping
from typing import Any, Protocol

from packages.contracts.audio import Mode
from packages.contracts.events import EventBus
from packages.contracts.verify import Progress, VerificationOutcome, VerificationStage
from packages.intervene.templates import select_template
from packages.risk.numeric import AMOUNT_RE
from packages.verify.budget import VerificationBudget
from packages.verify.directory import Directory, Institution, get_directory

log = logging.getLogger("ringfence.verify")

_REPLAY_TENANT = "replay"

# Enough recent CALLER speech to catch the institution's name, bounded so a
# long call cannot grow it without limit (invariant #6). The name is almost
# always said in the opening seconds, so this is generous.
_BUFFER_TURNS = 64


class Verifier(Protocol):
    """``packages/verify/simulated.py`` or ``packages/verify/live.py``."""

    async def verify(
        self,
        *,
        institution: Institution,
        amount: str | None,
        mode: Mode,
        progress: Progress | None = None,
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
        # session_id -> recent UNKNOWN-role text: a fallback source only.
        self._unattributed: OrderedDict[str, list[str]] = OrderedDict()

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
        if not self._enabled:
            return
        role = payload.get("role")
        if role == "CALLER":
            store = self._buffer
        elif role == "UNKNOWN":
            store = self._unattributed
        else:
            return  # CALLEE is never a source -- see _resolve
        session_id = str(payload.get("session_id", ""))
        text = str(payload.get("text", "")).strip()
        if not session_id or not text:
            return
        said = store.setdefault(session_id, [])
        store.move_to_end(session_id)
        said.append(text)
        del said[:-_BUFFER_TURNS]
        while len(store) > _BUFFER_TURNS:
            store.popitem(last=False)

    def _resolve(self, session_id: str) -> tuple[Institution | None, str | None]:
        """CALLER speech first, unattributed speech as a fallback, CALLEE never.

        Mixed (speakerphone) capture labels every turn UNKNOWN until the acoustic
        classifier calibrates, roughly eight seconds in -- which is exactly when
        a caller says who they claim to be. Found live: a genuine INTERVENE was
        skipped as ``no_institution`` because "Amazon Account Security" was only
        ever said inside that window. Carrier ingress has exact roles and never
        produces UNKNOWN, so this relaxes nothing where attribution is certain.

        CALLEE is still never a source: a frightened victim repeating the name
        back must not be what causes that institution to be contacted. The
        source is reported on every event, so an operator can see whether the
        name was attributed or inferred.
        """
        inst = self._directory.resolve(" ".join(self._buffer.get(session_id, [])))
        if inst is not None:
            return inst, "caller"
        inst = self._directory.resolve(" ".join(self._unattributed.get(session_id, [])))
        if inst is not None:
            return inst, "unattributed"
        return None, None

    def _amount(self, session_id: str) -> str | None:
        """The amount the caller stated, from CALLER speech only.

        Stricter than ``_resolve`` on purpose. A missing amount costs nothing --
        the prompt then says nothing about money -- but an amount taken from
        unattributed speech might be the victim's guess, and the agent would
        repeat it to the institution as fact.
        """
        m = AMOUNT_RE.search(" ".join(self._buffer.get(session_id, [])).lower())
        return m.group(0) if m else None

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

        institution, name_source = self._resolve(session_id)
        if institution is None:
            await self._publish_named(
                name_source,
                tenant,
                session_id,
                decision_id,
                t,
                stage="skipped",
                reason="no_institution",
            )
            return

        refusal = self._budget.refuse_reason(session_id=session_id, tenant=tenant)
        if refusal is not None:
            await self._publish_named(
                name_source,
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
            await self._publish_named(
                name_source,
                tenant,
                session_id,
                decision_id,
                t,
                stage="dialing",
                institution=institution,
            )

            async def progress(stage: VerificationStage, detail: Mapping[str, str]) -> None:
                await self._publish_named(
                    name_source,
                    tenant,
                    session_id,
                    decision_id,
                    t,
                    stage=stage,
                    institution=institution,
                    role=detail.get("role"),
                    text=detail.get("text"),
                )

            outcome = await self._verifier.verify(
                institution=institution,
                amount=self._amount(session_id),
                mode=_mode_of(payload),
                progress=progress,
            )
        except Exception:  # noqa: BLE001 - a verification must never break the bus loop
            log.exception("verification failed", extra={"session_id": session_id})
            outcome = None
        finally:
            self._budget.release()

        if outcome is None:
            # The guard refused, or it raised. Either way nothing was learnt.
            await self._publish_named(
                name_source,
                tenant,
                session_id,
                decision_id,
                t,
                stage="failed",
                institution=institution,
            )
            return

        await self._publish_named(
            name_source,
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

    async def _publish_named(self, name_source: str | None, *args: Any, **kwargs: Any) -> None:
        await self._publish(*args, name_source=name_source, **kwargs)

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
        name_source: str | None = None,
        role: str | None = None,
        text: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "name_source": name_source,
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
        if text is not None:
            # stage "transcript": one line of the verification conversation
            payload["role"] = role
            payload["text"] = text
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
