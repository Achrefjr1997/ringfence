"""What stops the agent contacting someone twenty times.

The bill is not the concern -- a 90-second session is about eleven cents.
The concern is that anyone who can push a line to INTERVENE, by reading scam
phrases at a microphone, can make RingFence open a conversation with a real
person. Repeatedly. That is a harassment amplifier, and the mitigation is
throttling, not metering.

Sliding-window shape lifted from ``packages/intervene/webhook.py`` --
``GuardianWebhook`` solves the same problem for the same reason, and two
callers do not justify a shared abstraction.
"""

from __future__ import annotations

import time
from collections import OrderedDict, deque
from collections.abc import Callable

_HOUR = 3600.0

# Bounded so a long-lived process cannot accumulate session ids forever --
# invariant #6 (no unbounded queue). Oldest evicted first; an evicted session
# could in principle verify twice, which is the right trade against a leak.
_SEEN_CAP = 500


class VerificationBudget:
    def __init__(
        self,
        *,
        per_tenant_hourly: int = 3,
        max_concurrent: int = 1,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._per_tenant_hourly = per_tenant_hourly
        self._max_concurrent = max_concurrent
        self._now = now
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._by_tenant: dict[str, deque[float]] = {}
        self._live = 0

    def refuse_reason(self, *, session_id: str, tenant: str) -> str | None:
        """``None`` means go ahead. Otherwise a short reason, published as
        ``stage:"skipped"`` so an operator can see *why* nothing happened
        rather than watching silence."""
        if session_id in self._seen:
            return "already_verified"
        if self._live >= self._max_concurrent:
            return "concurrent"
        if len(self._recent(tenant)) >= self._per_tenant_hourly:
            return "tenant_hourly"
        return None

    def claim(self, *, session_id: str, tenant: str) -> None:
        """Record a verification as started. Call only after
        :meth:`refuse_reason` returned ``None``."""
        self._seen[session_id] = None
        self._seen.move_to_end(session_id)
        while len(self._seen) > _SEEN_CAP:
            self._seen.popitem(last=False)
        self._recent(tenant).append(self._now())
        self._live += 1

    def release(self) -> None:
        """A verification finished, however it finished. Always call this --
        a leaked slot silently disables the feature for the process."""
        self._live = max(0, self._live - 1)

    def _recent(self, tenant: str) -> deque[float]:
        window = self._by_tenant.setdefault(tenant, deque())
        cutoff = self._now() - _HOUR
        while window and window[0] < cutoff:
            window.popleft()
        return window
