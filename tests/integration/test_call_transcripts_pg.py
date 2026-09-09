"""Oversight console P6 -- PgTranscriptStore against a real Postgres."""

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
from packages.calls.pg_transcripts import PgTranscriptStore  # noqa: E402


@pytest.fixture()
def stores() -> Iterator[tuple[PgCallLedger, PgTranscriptStore]]:
    lg = PgCallLedger(_DSN or "")
    ts = PgTranscriptStore(_DSN or "")
    lg._run(_truncate(lg))
    lg.open("s1", tenant="acme", api_key_id="k1", started_at=1000.0)
    try:
        yield lg, ts
    finally:
        ts.close_pool()
        lg.close_pool()


async def _truncate(lg: PgCallLedger) -> None:
    async with lg._pool.acquire() as conn:
        await conn.execute("TRUNCATE call_ledger, call_scores, call_transcript CASCADE")


def test_save_replace_and_get(stores: tuple[PgCallLedger, PgTranscriptStore]) -> None:
    _lg, ts = stores
    ts.save("s1", "acme", [("CALLER", "one", 1.0)])
    ts.save("s1", "acme", [("CALLER", "two", 1.0), ("CALLEE", "ok", 2.0)])
    assert ts.get("s1") == [("CALLER", "two", 1.0), ("CALLEE", "ok", 2.0)]


def test_save_for_an_unknown_call_is_dropped(
    stores: tuple[PgCallLedger, PgTranscriptStore],
) -> None:
    _lg, ts = stores
    ts.save("ghost", "acme", [("CALLER", "x", 1.0)])
    assert ts.get("ghost") == []


def test_transcript_dies_with_the_call(
    stores: tuple[PgCallLedger, PgTranscriptStore],
) -> None:
    lg, ts = stores
    ts.save("s1", "acme", [("CALLER", "x", 1.0)])

    async def _drop() -> None:
        async with lg._pool.acquire() as conn:
            await conn.execute("DELETE FROM call_ledger WHERE session_id = 's1'")

    lg._run(_drop())
    assert ts.get("s1") == []
