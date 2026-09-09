"""T-7.3 -- in-memory usage meter + plan assignment."""

from __future__ import annotations

from packages.billing.meter import InMemoryBillingStore, billing_period


def test_billing_period_is_year_month_utc() -> None:
    # 2026-03-15T12:00:00Z
    assert billing_period(1_773_921_600.0) == "2026-03"


def test_record_accumulates_per_metric_and_period() -> None:
    s = InMemoryBillingStore()
    mar = 1_773_921_600.0  # 2026-03
    apr = 1_776_600_000.0  # 2026-04
    s.record("t1", "call_minutes", 12.5, ts=mar)
    s.record("t1", "call_minutes", 7.5, ts=mar)
    s.record("t1", "calls", 2, ts=mar)
    s.record("t1", "call_minutes", 99, ts=apr)

    m = s.totals("t1", "2026-03")
    assert m.call_minutes == 20.0 and m.calls == 2 and m.guardian_notifications == 0
    assert s.totals("t1", "2026-04").call_minutes == 99
    assert s.totals("other", "2026-03").call_minutes == 0.0


def test_zero_quantity_is_ignored() -> None:
    s = InMemoryBillingStore()
    s.record("t1", "call_minutes", 0)
    assert s.totals("t1").call_minutes == 0.0


def test_key_id_attributes_usage_without_disturbing_the_tenant_total() -> None:
    s = InMemoryBillingStore()
    mar = 1_773_921_600.0  # 2026-03
    s.record("t1", "call_minutes", 10.0, key_id="k1", ts=mar)
    s.record("t1", "call_minutes", 4.0, key_id="k2", ts=mar)
    s.record("t1", "call_minutes", 1.0, ts=mar)  # e.g. a dev-mode session, no key
    s.record("t1", "calls", 3, key_id="k1", ts=mar)

    # the tenant total is every session, keyed or not
    assert s.totals("t1", "2026-03").call_minutes == 15.0
    # per key
    k1 = s.key_totals("t1", "k1", "2026-03")
    assert k1.call_minutes == 10.0 and k1.calls == 3
    assert s.key_totals("t1", "k2", "2026-03").call_minutes == 4.0
    assert s.key_totals("t1", "k3", "2026-03").call_minutes == 0.0
    assert s.key_totals("t1", "k1", "2026-04").call_minutes == 0.0


def test_plan_assignment() -> None:
    s = InMemoryBillingStore()
    assert s.plan_id("t1") is None
    s.set_plan("t1", "growth")
    assert s.plan_id("t1") == "growth"
