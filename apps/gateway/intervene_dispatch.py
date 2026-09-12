"""Live intervention dispatch (T-4.1 wiring, closing a documented gap).

``InterventionService`` (packages/intervene/service.py) was fully built and
tested but had no caller outside tests -- the coaching it renders never
reached a real session; the gateway only ever fired the guardian webhook on
INTERVENE, nothing for the protected person themselves.

This watches ``rf.*.decision`` exactly the way ``guardian.py`` does for
guardian webhooks, and on INTERVENE renders the warning and republishes it
as ``rf.<tenant>.warning`` so a live viewer -- the console's Live tab, via
``/events/{session_id}`` -- sees it the same turn it fires.

Only the ``app_banner`` channel is actually delivered here:

* ``guardian_push`` already has its own dispatcher and signed webhook
  (``apps/gateway/guardian.py``) watching the same decision stream --
  delivering it again here would double-notify the guardian.
* ``in_ear`` needs push infrastructure that does not exist yet
  (``docs/ROADMAP.md`` Phase 4.1: no FCM/APNs, no device registration).

One ``InterventionService`` per tenant, kept for the process lifetime, so
its cooldown state is real -- constructing a fresh one per dispatch would
mean cooldown never actually cools anything down.

An optional :class:`~packages.intervene.coach.CoachGenerator` refines the
static template: the static warning above always publishes first and
instantly (the safety net), and if a coach is configured and answers in
time, a **second** ``rf.<tenant>.warning`` follows with a sentence that
reasons over every active signal at once rather than the single
highest-weight one. A live viewer just sees the message sharpen a moment
later -- there is nothing to double-notify, since this is a display
refinement on the bus, not an external side effect.
"""

from __future__ import annotations

import contextlib
from typing import Any

from apps.gateway.decision_event import decision_from_event
from packages.contracts.audio import Mode
from packages.contracts.events import EventBus
from packages.contracts.risk import Decision
from packages.intervene.coach import CoachGenerator, CoachRequest
from packages.intervene.service import InterventionService, Warning
from packages.intervene.templates import select_template
from packages.policy.pack import PolicyPack

_REPLAY_TENANT = "replay"
_DELIVERED_CHANNEL = "app_banner"
_COACH_TEMPLATE_ID = "LLM_COACH"
_SIGNAL_LOOKBACK_S = 60.0  # matches InterventionService._top_caller_signal


class _BannerTransport:
    """The dispatcher's only real side effect: publish onto the bus for a
    live viewer. Every other configured channel is a deliberate no-op --
    see the module docstring for why."""

    def __init__(self, bus: EventBus, tenant: str) -> None:
        self._bus = bus
        self._tenant = tenant

    async def deliver(self, warning: Warning, channel: str) -> None:
        if channel != _DELIVERED_CHANNEL:
            return
        await self._bus.publish(
            f"rf.{self._tenant}.warning",
            {
                "session_id": warning.session_id,
                "decision_id": warning.decision_id,
                "t": warning.t,
                "template_id": warning.template_id,
                "language": warning.language,
                "text": warning.text,
            },
        )


def _active_caller_signals(
    decision: Decision, pack: PolicyPack, language: str
) -> tuple[tuple[str, str], ...]:
    """Every CALLER signal presently counting toward the score, each paired
    with its own template copy for grounding -- the same filter
    ``InterventionService._top_caller_signal`` uses, kept here rather than
    the single best one it picks."""
    window_start = decision.t - _SIGNAL_LOOKBACK_S
    seen: dict[str, float] = {}
    for c in decision.contributions:
        if c.source != "signal" or c.role not in ("CALLER", None):
            continue
        if c.t is not None and c.t < window_start:
            continue
        weight = pack.signals[c.id].weight if c.id in pack.signals else 0.0
        if weight <= 0:
            continue
        seen[c.id] = max(seen.get(c.id, 0.0), weight)
    ordered = sorted(seen, key=lambda sid: seen[sid], reverse=True)
    return tuple((sid, select_template(sid, language)[1]) for sid in ordered)


class InterventionDispatcher:
    def __init__(
        self,
        pack: PolicyPack,
        bus: EventBus,
        *,
        dry_run: bool = True,
        coach: CoachGenerator | None = None,
    ) -> None:
        self._pack = pack
        self._bus = bus
        self._dry_run = dry_run
        self._coach = coach
        self._services: dict[str, InterventionService] = {}

    def _service_for(self, tenant: str) -> InterventionService:
        svc = self._services.get(tenant)
        if svc is None:
            svc = InterventionService(
                self._pack, transport=_BannerTransport(self._bus, tenant), dry_run=self._dry_run
            )
            self._services[tenant] = svc
        return svc

    async def run(self) -> None:
        """Consume ``rf.*.decision`` until cancelled."""
        async with contextlib.aclosing(self._bus.subscribe("rf.*.decision")) as stream:
            async for subject, payload in stream:
                if payload.get("state") != "INTERVENE":
                    continue
                parts = subject.split(".")
                tenant = parts[1] if len(parts) > 2 else ""
                if tenant and tenant != _REPLAY_TENANT:
                    await self.dispatch(tenant, payload)

    async def dispatch(self, tenant: str, payload: dict[str, Any]) -> Warning | None:
        decision = decision_from_event(payload)
        language = str(payload.get("language") or "en")
        # replay is filtered before dispatch is ever reached (run(), above);
        # any non-REPLAY Mode satisfies InterventionService's suppress guard
        # identically -- the same simplification apps/gateway/guardian.py
        # makes for the same reason.
        warning = await self._service_for(tenant).on_decision(
            decision, mode=Mode.SDK, language=language
        )
        if warning is not None and warning.delivered and self._coach is not None:
            await self._refine(tenant, decision, warning, language)
        return warning

    async def _refine(
        self, tenant: str, decision: Decision, warning: Warning, language: str
    ) -> None:
        assert self._coach is not None
        signals = _active_caller_signals(decision, self._pack, language)
        if not signals:
            return
        text = await self._coach.suggest(
            CoachRequest(
                session_id=decision.session_id,
                score=decision.score,
                signals=signals,
                language=language,
            )
        )
        if not text:
            return  # coach missed (timeout/error/malformed) -- static text stands
        await self._bus.publish(
            f"rf.{tenant}.warning",
            {
                "session_id": warning.session_id,
                "decision_id": warning.decision_id,
                "t": warning.t,
                "template_id": _COACH_TEMPLATE_ID,
                "language": language,
                "text": text,
            },
        )
