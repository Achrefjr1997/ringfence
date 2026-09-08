"""Billing service (T-7.3).

Ties a tenant's recorded usage to its plan: current-period cost, projected
overage, and the one boolean the admission chain needs -- is this tenant
over a hard cap.
"""

from __future__ import annotations

from dataclasses import dataclass

from packages.billing.meter import BillingStore, UsageTotals
from packages.billing.plans import Plan, PlanBook


@dataclass(frozen=True, slots=True)
class BillingSnapshot:
    tenant: str
    period: str
    plan: str
    included_minutes: int
    call_minutes: float
    calls: int
    guardian_notifications: int
    overage_minutes: float
    overage_cents: int
    month_cents: int
    over_hard_cap: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "tenant": self.tenant,
            "period": self.period,
            "plan": self.plan,
            "included_minutes": self.included_minutes,
            "call_minutes": round(self.call_minutes, 2),
            "calls": self.calls,
            "guardian_notifications": self.guardian_notifications,
            "overage_minutes": round(self.overage_minutes, 2),
            "overage_cents": self.overage_cents,
            "month_cents": self.month_cents,
            "over_hard_cap": self.over_hard_cap,
        }


class BillingService:
    def __init__(self, plans: PlanBook, store: BillingStore) -> None:
        self._plans = plans
        self._store = store

    def plan_for(self, tenant: str) -> Plan:
        return self._plans.get(self._store.plan_id(tenant))

    def over_hard_cap(self, tenant: str) -> bool:
        plan = self.plan_for(tenant)
        if not plan.hard_cap:
            return False
        used = self._store.totals(tenant).call_minutes
        return used >= plan.included_minutes

    def snapshot(self, tenant: str, period: str | None = None) -> BillingSnapshot:
        plan = self.plan_for(tenant)
        t: UsageTotals = self._store.totals(tenant, period)
        overage_min = max(0.0, t.call_minutes - plan.included_minutes)
        return BillingSnapshot(
            tenant=tenant,
            period=t.period,
            plan=plan.id,
            included_minutes=plan.included_minutes,
            call_minutes=t.call_minutes,
            calls=t.calls,
            guardian_notifications=t.guardian_notifications,
            overage_minutes=overage_min,
            overage_cents=plan.overage_cents(t.call_minutes),
            month_cents=plan.month_cents(t.call_minutes),
            over_hard_cap=self.over_hard_cap(tenant),
        )
