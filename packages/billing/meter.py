"""Usage metering + plan assignment (T-7.3).

``BillingStore`` is what the gateway and :class:`BillingService` depend on;
:class:`InMemoryBillingStore` is the default and
:class:`~packages.billing.pg_meter.PgBillingStore` is the Postgres
implementation -- nothing above this interface changes between them, same
as the identity and case stores.

Tenant-level counters drive billing.  When a session was admitted with an
API key, the same usage is also recorded against that key so an admin can
see which integration is spending minutes (``key_totals``).  Everything is
keyed by a billing ``period`` -- a ``YYYY-MM`` string in UTC.
"""

from __future__ import annotations

import datetime as _dt
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

Metric = Literal["call_minutes", "calls", "guardian_notifications"]


def billing_period(ts: float | None = None) -> str:
    d = _dt.datetime.fromtimestamp(time.time() if ts is None else ts, tz=_dt.UTC)
    return f"{d.year:04d}-{d.month:02d}"


@dataclass(frozen=True, slots=True)
class UsageTotals:
    tenant: str
    period: str
    call_minutes: float
    calls: int
    guardian_notifications: int


class BillingStore(Protocol):
    def record(
        self,
        tenant: str,
        metric: Metric,
        quantity: float,
        *,
        key_id: str | None = None,
        ts: float | None = None,
    ) -> None: ...

    def totals(self, tenant: str, period: str | None = None) -> UsageTotals: ...

    def key_totals(self, tenant: str, key_id: str, period: str | None = None) -> UsageTotals: ...

    def plan_id(self, tenant: str) -> str | None: ...

    def set_plan(self, tenant: str, plan_id: str) -> None: ...


class InMemoryBillingStore:
    def __init__(self) -> None:
        self._counters: dict[tuple[str, str, str], float] = defaultdict(float)
        self._by_key: dict[tuple[str, str, str, str], float] = defaultdict(float)
        self._plans: dict[str, str] = {}

    def record(
        self,
        tenant: str,
        metric: Metric,
        quantity: float,
        *,
        key_id: str | None = None,
        ts: float | None = None,
    ) -> None:
        if not quantity:
            return
        p = billing_period(ts)
        self._counters[(tenant, p, metric)] += quantity
        if key_id:
            self._by_key[(tenant, key_id, p, metric)] += quantity

    def _totals(
        self,
        get: Callable[[Metric, str], float],
        tenant: str,
        period: str | None,
    ) -> UsageTotals:
        p = period or billing_period()
        return UsageTotals(
            tenant=tenant,
            period=p,
            call_minutes=get("call_minutes", p),
            calls=int(get("calls", p)),
            guardian_notifications=int(get("guardian_notifications", p)),
        )

    def totals(self, tenant: str, period: str | None = None) -> UsageTotals:
        return self._totals(lambda m, p: self._counters.get((tenant, p, m), 0.0), tenant, period)

    def key_totals(self, tenant: str, key_id: str, period: str | None = None) -> UsageTotals:
        return self._totals(
            lambda m, p: self._by_key.get((tenant, key_id, p, m), 0.0), tenant, period
        )

    def plan_id(self, tenant: str) -> str | None:
        return self._plans.get(tenant)

    def set_plan(self, tenant: str, plan_id: str) -> None:
        self._plans[tenant] = plan_id
