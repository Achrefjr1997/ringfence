"""Oversight console P1 -- PgCallLedger against a real Postgres."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

pytestmark = pytest.mark.needs_pg

_DSN = os.environ.get("RF_TEST_DATABASE_URL")
if not _DSN:
    pytest.skip("RF_TEST_DATABASE_URL not set", allow_module_level=True)
pytest.importorskip("asyncpg")

from packages.calls.pg_ledger import PgCallLedger  # noqa: E402


@pytest.fixture()
def ledger() -> Iterator[PgCallLedger]:
    lg = PgCallLedger(_DSN or "")
    lg._run(_truncate(lg))
    try:
        yield lg
    finally:
        lg.close_pool()


async def _truncate(lg: PgCallLedger) -> None:
    async with lg._pool.acquire() as conn:
        await conn.execute("TRUNCATE call_ledger, call_scores")


def test_open_score_close_round_trip(ledger: PgCallLedger) -> None:
    ledger.open("s1", tenant="acme", api_key_id="k1", started_at=1000.0)
    ledger.record_score("s1", t=5.0, score=20.0, state="WATCH")
    ledger.record_score("s1", t=12.0, score=70.0, state="ALERT")
    ledger.record_score("s1", t=20.0, score=40.0, state="WATCH")
    ledger.close("s1", ended_at=1090.0, leg_count=2)

    rec = ledger.get("s1")
    assert rec is not None
    assert rec.api_key_id == "k1" and rec.duration_s == 90.0 and rec.leg_count == 2
    assert rec.peak_state == "ALERT" and rec.peak_score == 70.0
    assert [p.state for p in rec.scores] == ["WATCH", "ALERT", "WATCH"]


def test_list_filters_and_scopes(ledger: PgCallLedger) -> None:
    ledger.open("a", tenant="acme", api_key_id="k1", started_at=100.0)
    ledger.open("b", tenant="acme", api_key_id="k2", started_at=200.0)
    ledger.open("c", tenant="other", api_key_id="k1", started_at=300.0)
    ledger.record_score("b", t=1.0, score=90.0, state="INTERVENE")

    assert {r.session_id for r in ledger.list("acme")} == {"a", "b"}
    assert [r.session_id for r in ledger.list("acme", api_key_id="k1")] == ["a"]
    assert [r.session_id for r in ledger.list("acme", min_state="ALERT")] == ["b"]
    assert {r.session_id for r in ledger.list("acme", since=150.0)} == {"b"}


def test_scores_are_wiped_with_the_call(ledger: PgCallLedger) -> None:
    ledger.open("s1", tenant="acme", api_key_id=None, started_at=0.0)
    ledger.record_score("s1", t=1.0, score=10.0, state="CALM")
    ledger._run(_delete_call(ledger, "s1"))
    async_rows = ledger._run(_count_scores(ledger, "s1"))
    assert async_rows == 0


async def _delete_call(lg: PgCallLedger, sid: str) -> None:
    async with lg._pool.acquire() as conn:
        await conn.execute("DELETE FROM call_ledger WHERE session_id = $1", sid)


async def _count_scores(lg: PgCallLedger, sid: str) -> int:
    async with lg._pool.acquire() as conn:
        return int(
            await conn.fetchval("SELECT count(*) FROM call_scores WHERE session_id = $1", sid)
        )
