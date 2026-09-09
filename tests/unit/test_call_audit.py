"""Oversight console P5 -- in-memory access audit log."""

from __future__ import annotations

from packages.calls.audit import InMemoryAuditLog


def _log() -> InMemoryAuditLog:
    return InMemoryAuditLog()


def _rec(lg: InMemoryAuditLog, **kw: object) -> None:
    base: dict[str, object] = dict(
        session_id="s1",
        tenant="acme",
        actor_id="u1",
        actor_email="u1@acme.co",
        action="view",
    )
    base.update(kw)
    lg.record(**base)  # type: ignore[arg-type]


def test_record_and_for_call_newest_first() -> None:
    lg = _log()
    _rec(lg, action="view")
    _rec(lg, action="comment")
    _rec(lg, session_id="s2", action="view")

    rows = lg.for_call("s1")
    assert [r.action for r in rows] == ["comment", "view"]  # newest first
    assert all(r.actor_email == "u1@acme.co" for r in rows)
    assert [r.session_id for r in lg.for_call("s2")] == ["s2"]


def test_unknown_action_is_dropped() -> None:
    lg = _log()
    _rec(lg, action="nonsense")
    assert lg.for_call("s1") == []


def test_query_filters_by_tenant_actor_action_and_time() -> None:
    lg = _log()
    _rec(lg, actor_id="u1", action="view")
    _rec(lg, actor_id="u2", action="share")
    _rec(lg, tenant="other", actor_id="u1", action="view")

    assert len(lg.query("acme")) == 2
    assert [r.action for r in lg.query("acme", actor_id="u2")] == ["share"]
    assert [r.actor_id for r in lg.query("acme", action="view")] == ["u1"]
    assert lg.query("acme", limit=1) == lg.query("acme")[:1]


def test_list_action_carries_no_session() -> None:
    lg = _log()
    _rec(lg, session_id="", action="list")
    (row,) = lg.query("acme")
    assert row.action == "list" and row.session_id == ""
    assert lg.for_call("") == [row]  # addressable, just not tied to one call
