"""Oversight console P1 -- PgCallLedger bits that need no database."""

from __future__ import annotations

from packages.calls.pg_ledger import schema_sql


def test_schema_declares_both_tables_and_is_idempotent() -> None:
    sql = schema_sql()
    for table in ("call_ledger", "call_scores"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert sql.count("CREATE TABLE ") == sql.count("CREATE TABLE IF NOT EXISTS ")
    assert sql.count("CREATE INDEX ") == sql.count("CREATE INDEX IF NOT EXISTS ")
    # the score series is wiped with its call
    assert "ON DELETE CASCADE" in sql
    # P2 columns, patched onto an already-migrated table idempotently
    assert "user_ref" in sql and "user_label" in sql
    assert sql.count("ALTER TABLE ") == sql.count("ADD COLUMN IF NOT EXISTS ")
