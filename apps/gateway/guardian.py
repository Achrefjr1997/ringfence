"""Guardian webhook dispatch (T-4.5 wiring).

The pipeline publishes every decision to ``rf.<tenant>.decision``. This
watches that stream and, on an ``INTERVENE``, fires the tenant's
:class:`~packages.intervene.webhook.GuardianWebhook` -- signed, verdict
only (timestamp, signals, protective signals, a short rationale), never
transcript or audio.

Per tenant:
  * ``guardian_webhook_url`` -- from ``config/tenants.yaml``
  * HMAC secret -- from the env var named by ``guardian_webhook_secret_env``;
    if that is unset the webhook does not fire (no unsigned callbacks).

The dry-run / replay guard is inherited from ``GuardianWebhook``: with
``RF_DRY_RUN`` set nothing leaves the process, and ``rf.replay.*``
decisions are skipped outright.
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Any

import httpx

from packages.contracts.audio import Mode
from packages.contracts.risk import Contribution, Decision
from packages.policy.pack import PolicyPack
from packages.policy.tenants import TenantRegistry
from packages.intervene.webhook import GuardianWebhook

log = logging.getLogger("ringfence.guardian")

_REPLAY_TENANT = "replay"


def _decision_from_event(p: dict[str, Any]) -> Decision:
    return Decision(
        decision_id=str(p.get("decision_id", "")),
        session_id=str(p.get("session_id", "")),
        t=float(p.get("t", 0.0)),
        state=p.get("state", "CALM"),
        score=float(p.get("score", 0.0)),
        policy_pack="",
        contributions=tuple(
            Contribution(
                source=c["source"],
                id=c["id"],
                value=float(c.get("value", 0.0)),
                role=c.get("role"),
            )
            for c in p.get("contributions", [])
        ),
        counterfactual=p.get("counterfactual"),
    )


class GuardianDispatcher:
    def __init__(
        self,
        tenants: TenantRegistry,
        pack: PolicyPack,
        *,
        dry_run: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._tenants = tenants
        self._pack = pack
        self._dry_run = dry_run
        self._client = client or httpx.AsyncClient(timeout=2.0)
        self._owns_client = client is None
        self._hooks: dict[tuple[str, str], GuardianWebhook] = {}

    async def run(self, bus: Any) -> None:
        """Consume ``rf.*.decision`` until cancelled."""
        async with contextlib.aclosing(bus.subscribe("rf.*.decision")) as stream:
            async for subject, payload in stream:
                if payload.get("state") != "INTERVENE":
                    continue
                parts = subject.split(".")
                tenant = parts[1] if len(parts) > 2 else ""
                if tenant and tenant != _REPLAY_TENANT:
                    await self.dispatch(tenant, payload)

    async def dispatch(self, tenant: str, payload: dict[str, Any]) -> bool:
        cfg = self._tenants.get(tenant)
        if not cfg.guardian_webhook_url:
            return False
        secret = os.environ.get(cfg.guardian_webhook_secret_env or "", "")
        if not secret:
            log.warning(
                "guardian webhook configured but no secret",
                extra={"tenant": tenant, "secret_env": cfg.guardian_webhook_secret_env},
            )
            return False

        hook = self._hooks.get((cfg.guardian_webhook_url, secret))
        if hook is None:
            hook = GuardianWebhook(
                cfg.guardian_webhook_url,
                secret,
                self._pack,
                dry_run=self._dry_run,
                client=self._client,
            )
            self._hooks[(cfg.guardian_webhook_url, secret)] = hook

        try:
            return await hook.notify(_decision_from_event(payload), mode=Mode.SDK)
        except Exception:  # noqa: BLE001 - a guardian's endpoint must never break the gateway
            log.exception("guardian webhook failed", extra={"tenant": tenant})
            return False

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
