"""Postgres-backed :class:`BillingStore` (T-7.3).

Same synchronous interface as :class:`InMemoryBillingStore`, driven from
the shared :class:`~packages.db.loop.LoopThread`.  ``record()`` fires once
per session close, never per frame.
"""

from __future__ import annotations

from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar

from packages.billing.meter import Metric, UsageTotals, billing_period
from packages.db.loop import LoopThread

_R = TypeVar("_R")
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_METRICS: tuple[Metric, ...] = ("call_minutes", "calls", "guardian_notifications")


def schema_sql() -> str:
    return _SCHEMA_PATH.read_text(encoding="utf-8")


class PgBillingStore:
    def __init__(
        self, dsn: str, *, min_size: int = 1, max_size: int = 4, op_timeout_s: float = 10.0
    ) -> None:
        try:
            import asyncpg  # noqa: PLC0415 - optional 'db' extra
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise RuntimeError(
                "PgBillingStore needs the 'db' extra: pip install -e '.[db]'"
            ) from exc

        self._timeout_s = op_timeout_s
        self._closed = False
        self._loop = LoopThread()
        try:

            async def _open() -> Any:
                return await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size)

            self._pool: Any = self._loop.run(_open(), timeout_s=op_timeout_s)
            self._loop.run(self._apply_schema(), timeout_s=op_timeout_s)
        except BaseException:
            self._loop.close()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:

            async def _shut() -> None:
                await self._pool.close()

            self._loop.run(_shut(), timeout_s=self._timeout_s)
        finally:
            self._loop.close()

    async def _apply_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(schema_sql())

    def _run(self, coro: Coroutine[object, object, _R]) -> _R:
        return self._loop.run(coro, timeout_s=self._timeout_s)

    # -- usage ------------------------------------------------------

    def record(
        self, tenant: str, metric: Metric, quantity: float, *, ts: float | None = None
    ) -> None:
        if not quantity:
            return
        self._run(self._record(tenant, billing_period(ts), metric, float(quantity)))

    async def _record(self, tenant: str, period: str, metric: str, quantity: float) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO usage_counters (tenant, period, metric, quantity) "
                "VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (tenant, period, metric) "
                "DO UPDATE SET quantity = usage_counters.quantity + EXCLUDED.quantity",
                tenant,
                period,
                metric,
                quantity,
            )

    def totals(self, tenant: str, period: str | None = None) -> UsageTotals:
        return self._run(self._totals(tenant, period or billing_period()))

    async def _totals(self, tenant: str, period: str) -> UsageTotals:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT metric, quantity FROM usage_counters WHERE tenant = $1 AND period = $2",
                tenant,
                period,
            )
        by = {r["metric"]: r["quantity"] for r in rows}
        return UsageTotals(
            tenant=tenant,
            period=period,
            call_minutes=by.get("call_minutes", 0.0),
            calls=int(by.get("calls", 0.0)),
            guardian_notifications=int(by.get("guardian_notifications", 0.0)),
        )

    # -- plan assignment -----------------------------------------

    def plan_id(self, tenant: str) -> str | None:
        return self._run(self._plan_id(tenant))

    async def _plan_id(self, tenant: str) -> str | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchval("SELECT plan FROM org_billing WHERE tenant = $1", tenant)
        return str(row) if row is not None else None

    def set_plan(self, tenant: str, plan_id: str) -> None:
        self._run(self._set_plan(tenant, plan_id))

    async def _set_plan(self, tenant: str, plan_id: str) -> None:
        import time  # noqa: PLC0415

        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO org_billing (tenant, plan, updated_at) VALUES ($1, $2, $3) "
                "ON CONFLICT (tenant) DO UPDATE SET plan = EXCLUDED.plan, updated_at = EXCLUDED.updated_at",
                tenant,
                plan_id,
                time.time(),
            )
