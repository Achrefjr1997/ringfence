"""Intervention service and the dry-run / replay guard (T-4.1, §8.1–8.5).

The guard is the point of this module: **before any external side effect**,
if ``dry_run`` is set or the session is ``Mode.REPLAY``, log and return.
A replay session or a shadow policy evaluation can never send a real
notification (invariant #4).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from packages.contracts.audio import Mode
from packages.contracts.risk import Decision
from packages.intervene.templates import select_template
from packages.policy.pack import PolicyPack

log = logging.getLogger("ringfence.intervene")

# guardians get the verdict only — timestamp, signals, rationale — never text/audio
_VERDICT_ONLY_CHANNELS = frozenset({"guardian_push"})


@dataclass(frozen=True, slots=True)
class Warning:
    session_id: str
    decision_id: str
    t: float
    template_id: str
    language: str
    text: str
    channels: tuple[str, ...]
    idempotency_key: str
    delivered: bool


class Transport(Protocol):
    async def deliver(self, warning: Warning, channel: str) -> None: ...


@dataclass
class LoggingTransport:
    sent: list[tuple[str, str]] = field(default_factory=list)

    async def deliver(self, warning: Warning, channel: str) -> None:
        self.sent.append((channel, warning.idempotency_key))
        log.info("intervene channel=%s key=%s", channel, warning.idempotency_key)


class InterventionService:
    def __init__(
        self,
        pack: PolicyPack,
        *,
        transport: Transport,
        dry_run: bool = True,
        cooldown_s: float | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._pack = pack
        self._transport = transport
        self._dry_run = dry_run
        self._cooldown_s = pack.interventions.cooldown_s if cooldown_s is None else cooldown_s
        self._now = now
        self._last_fired: dict[str, float] = {}

    async def on_decision(
        self, decision: Decision, *, mode: Mode, language: str = "en"
    ) -> Warning | None:
        if decision.state != "INTERVENE":
            return None

        last = self._last_fired.get(decision.session_id)
        if last is not None and self._now() - last < self._cooldown_s:
            return None

        signal_id = self._top_caller_signal(decision)
        template_id, base_text = select_template(signal_id, language)
        channels = tuple(self._pack.interventions.channels) or ("app_banner",)
        key = f"{decision.session_id}:{decision.decision_id}"

        suppress = self._dry_run or mode is Mode.REPLAY
        warning = Warning(
            session_id=decision.session_id,
            decision_id=decision.decision_id,
            t=decision.t,
            template_id=template_id,
            language=language,
            text=base_text,
            channels=channels,
            idempotency_key=key,
            delivered=not suppress,
        )

        if suppress:
            log.info(
                "SUPPRESSED intervention (dry_run=%s mode=%s) key=%s",
                self._dry_run,
                mode.value,
                key,
            )
            return warning

        self._last_fired[decision.session_id] = self._now()
        for channel in channels:
            payload = warning
            if channel in _VERDICT_ONLY_CHANNELS:
                payload = _strip_content(warning)
            await self._transport.deliver(payload, channel)
        return warning

    def _top_caller_signal(self, decision: Decision) -> str | None:
        weights = self._pack.signals
        window_start = decision.t - 60.0
        best: tuple[float, str] | None = None
        for c in decision.contributions:
            if c.source != "signal" or c.role not in ("CALLER", None):
                continue
            if c.t is not None and c.t < window_start:
                continue
            w = weights[c.id].weight if c.id in weights else 0.0
            if w <= 0:
                continue
            if best is None or w > best[0]:
                best = (w, c.id)
        return best[1] if best else None


def _strip_content(w: Warning) -> Warning:
    return Warning(
        session_id=w.session_id,
        decision_id=w.decision_id,
        t=w.t,
        template_id=w.template_id,
        language=w.language,
        text="",  # guardians never receive the message text
        channels=w.channels,
        idempotency_key=w.idempotency_key,
        delivered=w.delivered,
    )
