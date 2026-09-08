"""T-7.3 -- PgBillingStore bits that need no database."""

from __future__ import annotations

from packages.billing.pg_meter import schema_sql
from packages.db.migrate import _SCHEMAS


def test_schema_is_idempotent_and_declares_both_tables() -> None:
    sql = schema_sql()
    for table in ("org_billing", "usage_counters"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert sql.count("CREATE TABLE ") == sql.count("CREATE TABLE IF NOT EXISTS ")
    assert sql.count("CREATE INDEX ") == sql.count("CREATE INDEX IF NOT EXISTS ")


def test_migrate_runner_applies_the_billing_schema_too() -> None:
    names = [name for name, _ in _SCHEMAS]
    assert names == ["identity", "cases", "billing"]
