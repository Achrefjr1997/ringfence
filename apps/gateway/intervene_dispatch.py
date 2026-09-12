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
"""

from __future__ import annotations

import contextlib
from typing import Any

from apps.gateway.decision_event import decision_from_event
from packages.contracts.audio import Mode
from packages.contracts.events import EventBus
from packages.intervene.service import InterventionService, Warning
from packages.policy.pack import PolicyPack

_REPLAY_TENANT = "replay"
_DELIVERED_CHANNEL = "app_banner"


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


class InterventionDispatcher:
    def __init__(self, pack: PolicyPack, bus: EventBus, *, dry_run: bool = True) -> None:
        self._pack = pack
        self._bus = bus
        self._dry_run = dry_run
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
        return await self._service_for(tenant).on_decision(
            decision, mode=Mode.SDK, language=language
        )
