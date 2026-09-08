"""T-7.2a -- PgIdentityStore pieces that need no live database."""

from __future__ import annotations

import threading

import pytest

from packages.identity._loop import LoopThread
from packages.identity.migrate import main, schema_sql

# -- schema.sql ----------------------------------------------------------


def test_schema_declares_the_three_tables_idempotently() -> None:
    sql = schema_sql()
    for table in ("orgs", "users", "api_keys"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    # every CREATE is guarded, so re-applying is a no-op
    assert sql.count("CREATE TABLE ") == sql.count("CREATE TABLE IF NOT EXISTS ")
    assert sql.count("CREATE INDEX ") == sql.count("CREATE INDEX IF NOT EXISTS ")


def test_migrate_main_without_a_dsn_is_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RF_DATABASE_URL", raising=False)
    assert main([]) == 2


# -- LoopThread --------------------------------------------------------


def test_loop_thread_runs_a_coroutine_and_propagates_the_result() -> None:
    loop = LoopThread()
    try:

        async def add(a: int, b: int) -> int:
            return a + b

        assert loop.run(add(2, 3)) == 5

        thread_names: list[str] = []

        async def where_am_i() -> None:
            thread_names.append(threading.current_thread().name)

        loop.run(where_am_i())
        assert thread_names == ["rf-identity-loop"]
    finally:
        loop.close()


def test_loop_thread_propagates_exceptions() -> None:
    loop = LoopThread()
    try:

        async def boom() -> None:
            raise ValueError("nope")

        with pytest.raises(ValueError, match="nope"):
            loop.run(boom())
    finally:
        loop.close()


def test_loop_thread_close_is_idempotent() -> None:
    loop = LoopThread()
    loop.close()
    loop.close()  # must not raise


# -- PgIdentityStore construction failure -----------------------------


def test_pg_store_cleans_up_the_loop_when_the_pool_cannot_connect() -> None:
    pytest.importorskip("asyncpg")
    from packages.identity.pg_store import PgIdentityStore

    before = threading.active_count()
    with pytest.raises(Exception):  # noqa: B017,PT011 - OSError family from asyncpg
        PgIdentityStore("postgres://user:pw@127.0.0.1:1/db", op_timeout_s=5.0)
    # the daemon loop thread from the failed construction is not leaked
    assert threading.active_count() <= before
