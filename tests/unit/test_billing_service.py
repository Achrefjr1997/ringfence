"""T-7.3 -- BillingService: usage x plan -> cost + hard-cap."""

from __future__ import annotations

from packages.billing.meter import InMemoryBillingStore
from packages.billing.plans import load_plans
from packages.billing.service import BillingService


def _svc() -> tuple[BillingService, InMemoryBillingStore]:
    store = InMemoryBillingStore()
    return BillingService(load_plans(), store), store


def test_defaults_to_the_pilot_plan_and_hard_caps_it() -> None:
    svc, store = _svc()
    assert svc.plan_for("t1").id == "pilot"  # no assignment -> default
    assert svc.over_hard_cap("t1") is False

    store.record("t1", "call_minutes", 600)  # pilot allowance = 3 * 200
    assert svc.over_hard_cap("t1") is True
    snap = svc.snapshot("t1")
    assert snap.over_hard_cap is True and snap.overage_cents == 0  # hard cap, never billed


def test_metered_plan_accrues_overage_and_never_hard_caps() -> None:
    svc, store = _svc()
    store.set_plan("t1", "starter")  # 3000 included, 2.0c/min over, $49 base
    store.record("t1", "call_minutes", 3500)
    store.record("t1", "calls", 40)

    snap = svc.snapshot("t1")
    assert snap.plan == "starter"
    assert snap.overage_minutes == 500
    assert snap.overage_cents == 1000
    assert snap.month_cents == 4900 + 1000
    assert snap.over_hard_cap is False
    assert snap.calls == 40


def test_snapshot_serialises_for_the_endpoint() -> None:
    svc, store = _svc()
    store.set_plan("t1", "growth")
    store.record("t1", "call_minutes", 123.456)
    d = svc.snapshot("t1").as_dict()
    assert d["plan"] == "growth" and d["call_minutes"] == 123.46
    assert set(d) >= {"included_minutes", "overage_cents", "month_cents", "over_hard_cap", "period"}
