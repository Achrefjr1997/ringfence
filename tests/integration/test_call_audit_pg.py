"""Oversight console P5 -- PgAuditLog against a real Postgres."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

pytestmark = pytest.mark.needs_pg

_DSN = os.environ.get("RF_TEST_DATABASE_URL")
if not _DSN:
    pytest.skip("RF_TEST_DATABASE_URL not set", allow_module_level=True)
pytest.importorskip("asyncpg")

from packages.calls.pg_audit import PgAuditLog  # noqa: E402


@pytest.fixture()
def log() -> Iterator[PgAuditLog]:
    lg = PgAuditLog(_DSN or "")
    lg._run(_truncate(lg))
    try:
        yield lg
    finally:
        lg.close_pool()


async def _truncate(lg: PgAuditLog) -> None:
    async with lg._pool.acquire() as conn:
        await conn.execute("TRUNCATE call_access_log")


def _rec(lg: PgAuditLog, **kw: object) -> None:
    base: dict[str, object] = dict(
        session_id="s1",
        tenant="acme",
        actor_id="u1",
        actor_email="u1@acme.co",
        action="view",
    )
    base.update(kw)
    lg.record(**base)  # type: ignore[arg-type]


def test_append_and_read_back(log: PgAuditLog) -> None:
    _rec(log, action="view", ip="10.0.0.1")
    _rec(log, action="comment")
    _rec(log, session_id="s2", action="view")

    rows = log.for_call("s1")
    assert [r.action for r in rows] == ["comment", "view"]  # newest first
    assert rows[1].ip == "10.0.0.1"
    assert [r.session_id for r in log.for_call("s2")] == ["s2"]


def test_query_filters(log: PgAuditLog) -> None:
    _rec(log, actor_id="u1", action="view")
    _rec(log, actor_id="u2", action="share")
    _rec(log, tenant="other", action="view")

    assert len(log.query("acme")) == 2
    assert [r.action for r in log.query("acme", actor_id="u2")] == ["share"]
    assert [r.actor_id for r in log.query("acme", action="view")] == ["u1"]


def test_audit_survives_when_the_call_is_gone(log: PgAuditLog) -> None:
    # no FK to call_ledger, so a row for a session that never existed is fine
    _rec(log, session_id="never-a-real-call", action="view")
    assert len(log.for_call("never-a-real-call")) == 1
