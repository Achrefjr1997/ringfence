"""T-7.3 -- PgBillingStore against a real Postgres."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

pytestmark = pytest.mark.needs_pg

_DSN = os.environ.get("RF_TEST_DATABASE_URL")
if not _DSN:
    pytest.skip("RF_TEST_DATABASE_URL not set", allow_module_level=True)
pytest.importorskip("asyncpg")

from packages.billing.pg_meter import PgBillingStore  # noqa: E402


@pytest.fixture()
def store() -> Iterator[PgBillingStore]:
    s = PgBillingStore(_DSN or "")
    s._run(_truncate(s))
    try:
        yield s
    finally:
        s.close()


async def _truncate(s: PgBillingStore) -> None:
    async with s._pool.acquire() as conn:
        await conn.execute("TRUNCATE usage_counters, org_billing")


def test_record_upserts_additively(store: PgBillingStore) -> None:
    ts = 1_773_921_600.0  # 2026-03
    store.record("t1", "call_minutes", 10.0, ts=ts)
    store.record("t1", "call_minutes", 5.5, ts=ts)
    store.record("t1", "calls", 3, ts=ts)

    m = store.totals("t1", "2026-03")
    assert m.call_minutes == 15.5 and m.calls == 3
    assert store.totals("t1", "2026-04").call_minutes == 0.0


def test_plan_assignment_round_trips(store: PgBillingStore) -> None:
    assert store.plan_id("t1") is None
    store.set_plan("t1", "scale")
    assert store.plan_id("t1") == "scale"
    store.set_plan("t1", "growth")  # upsert
    assert store.plan_id("t1") == "growth"


def test_a_second_store_sees_the_writes(store: PgBillingStore) -> None:
    store.record("t1", "call_minutes", 7.0)
    store.set_plan("t1", "starter")
    other = PgBillingStore(_DSN or "")
    try:
        assert other.totals("t1").call_minutes == 7.0
        assert other.plan_id("t1") == "starter"
    finally:
        other.close()
