"""Oversight console P3 -- PgCommentStore against a real Postgres."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

pytestmark = pytest.mark.needs_pg

_DSN = os.environ.get("RF_TEST_DATABASE_URL")
if not _DSN:
    pytest.skip("RF_TEST_DATABASE_URL not set", allow_module_level=True)
pytest.importorskip("asyncpg")

from packages.calls.comments import CommentError  # noqa: E402
from packages.calls.pg_comments import PgCommentStore  # noqa: E402
from packages.calls.pg_ledger import PgCallLedger  # noqa: E402


@pytest.fixture()
def stores() -> Iterator[tuple[PgCallLedger, PgCommentStore]]:
    lg = PgCallLedger(_DSN or "")
    cm = PgCommentStore(_DSN or "")
    lg._run(_truncate(lg))
    lg.open("s1", tenant="acme", api_key_id="k1", started_at=1000.0)
    try:
        yield lg, cm
    finally:
        cm.close_pool()
        lg.close_pool()


async def _truncate(lg: PgCallLedger) -> None:
    async with lg._pool.acquire() as conn:
        await conn.execute("TRUNCATE call_ledger, call_scores, call_comments CASCADE")


def test_thread_persists_with_mentions(stores: tuple[PgCallLedger, PgCommentStore]) -> None:
    _lg, cm = stores
    top = cm.add(
        session_id="s1",
        tenant="acme",
        author_id="u1",
        author_email="a@acme.co",
        body="root",
        t_seconds=9.0,
        visibility="mentions",
        mentions=("u2", "u3"),
    )
    cm.add(
        session_id="s1",
        tenant="acme",
        author_id="u2",
        author_email="b@acme.co",
        body="reply",
        parent_id=top.id,
    )
    got = cm.list_for_call("s1")
    assert [c.body for c in got] == ["root", "reply"]
    assert set(got[0].mentions) == {"u2", "u3"} and got[0].t_seconds == 9.0
    assert got[1].parent_id == top.id


def test_edit_delete_resolve_round_trip(stores: tuple[PgCallLedger, PgCommentStore]) -> None:
    _lg, cm = stores
    c = cm.add(session_id="s1", tenant="acme", author_id="u1", author_email="a@acme.co", body="v1")
    assert cm.edit(c.id, actor_id="u1", is_admin=False, body="v2").body == "v2"
    with pytest.raises(CommentError):
        cm.edit(c.id, actor_id="stranger", is_admin=False, body="x")
    r = cm.set_resolved(c.id, actor_id="u9", resolved=True)
    assert r.resolved_at is not None and r.resolved_by == "u9"
    cm.delete(c.id, actor_id="u1", is_admin=False)
    assert cm.list_for_call("s1") == []


def test_deleting_the_call_removes_its_comments(
    stores: tuple[PgCallLedger, PgCommentStore],
) -> None:
    lg, cm = stores
    cm.add(session_id="s1", tenant="acme", author_id="u1", author_email="a@acme.co", body="x")

    async def _drop() -> None:
        async with lg._pool.acquire() as conn:
            await conn.execute("DELETE FROM call_ledger WHERE session_id = 's1'")

    lg._run(_drop())
    assert cm.list_for_call("s1") == []
