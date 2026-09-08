"""Usage metering + plan assignment (T-7.3).

``BillingStore`` is what the gateway and :class:`BillingService` depend on;
:class:`InMemoryBillingStore` is the default and
:class:`~packages.billing.pg_meter.PgBillingStore` is the Postgres
implementation -- nothing above this interface changes between them, same
as the identity and case stores.

Everything is keyed by ``tenant`` (which equals the org id) and a billing
``period`` -- a ``YYYY-MM`` string in UTC.
"""

from __future__ import annotations

import datetime as _dt
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal, Protocol

Metric = Literal["call_minutes", "calls", "guardian_notifications"]
_METRICS: tuple[Metric, ...] = ("call_minutes", "calls", "guardian_notifications")


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
        self, tenant: str, metric: Metric, quantity: float, *, ts: float | None = None
    ) -> None: ...

    def totals(self, tenant: str, period: str | None = None) -> UsageTotals: ...

    def plan_id(self, tenant: str) -> str | None: ...

    def set_plan(self, tenant: str, plan_id: str) -> None: ...


class InMemoryBillingStore:
    def __init__(self) -> None:
        self._counters: dict[tuple[str, str, str], float] = defaultdict(float)
        self._plans: dict[str, str] = {}

    def record(
        self, tenant: str, metric: Metric, quantity: float, *, ts: float | None = None
    ) -> None:
        if quantity:
            self._counters[(tenant, billing_period(ts), metric)] += quantity

    def totals(self, tenant: str, period: str | None = None) -> UsageTotals:
        p = period or billing_period()
        vals = {m: self._counters.get((tenant, p, m), 0.0) for m in _METRICS}
        return UsageTotals(
            tenant=tenant,
            period=p,
            call_minutes=vals["call_minutes"],
            calls=int(vals["calls"]),
            guardian_notifications=int(vals["guardian_notifications"]),
        )

    def plan_id(self, tenant: str) -> str | None:
        return self._plans.get(tenant)

    def set_plan(self, tenant: str, plan_id: str) -> None:
        self._plans[tenant] = plan_id
