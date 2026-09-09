"""Oversight console P3 -- in-memory threaded review comments."""

from __future__ import annotations

import pytest

from packages.calls.comments import CommentError, InMemoryCommentStore


def _store() -> InMemoryCommentStore:
    return InMemoryCommentStore()


def _add(s: InMemoryCommentStore, **kw: object) -> object:
    base: dict[str, object] = dict(
        session_id="s1", tenant="acme", author_id="u1", author_email="a@acme.co", body="hi"
    )
    base.update(kw)
    return s.add(**base)  # type: ignore[arg-type]


def test_add_list_thread_and_anchor() -> None:
    s = _store()
    top = _add(s, body="root", t_seconds=12.5)
    _add(s, body="reply", parent_id=top.id)  # type: ignore[attr-defined]
    got = s.list_for_call("s1")
    assert [c.body for c in got] == ["root", "reply"]
    assert got[0].t_seconds == 12.5 and got[1].parent_id == top.id  # type: ignore[attr-defined]
    assert s.list_for_call("other") == []


def test_empty_body_and_bad_visibility_are_rejected() -> None:
    s = _store()
    with pytest.raises(CommentError):
        _add(s, body="   ")
    with pytest.raises(CommentError):
        _add(s, visibility="secret")


def test_visibility_rules() -> None:
    s = _store()
    org = _add(s, visibility="org")
    priv = _add(s, visibility="private")
    men = _add(s, visibility="mentions", mentions=("u2",))

    # author sees all three
    assert all(c.visible_to("u1", is_admin=False) for c in (org, priv, men))  # type: ignore[attr-defined]
    # a bystander sees only org
    assert org.visible_to("u9", is_admin=False)  # type: ignore[attr-defined]
    assert not priv.visible_to("u9", is_admin=False)  # type: ignore[attr-defined]
    assert not men.visible_to("u9", is_admin=False)  # type: ignore[attr-defined]
    # the mentioned user sees the mentions comment
    assert men.visible_to("u2", is_admin=False)  # type: ignore[attr-defined]
    # admin sees everything
    assert priv.visible_to("u9", is_admin=True)  # type: ignore[attr-defined]


def test_edit_delete_are_author_or_admin_only() -> None:
    s = _store()
    c = _add(s, body="mine")
    with pytest.raises(CommentError):
        s.edit(c.id, actor_id="stranger", is_admin=False, body="x")  # type: ignore[attr-defined]
    edited = s.edit(c.id, actor_id="stranger", is_admin=True, body="fixed")  # type: ignore[attr-defined]
    assert edited.body == "fixed" and edited.edited_at is not None
    updated = s.edit(c.id, actor_id="u1", is_admin=False, body="mine v2")  # type: ignore[attr-defined]
    assert updated.body == "mine v2"

    with pytest.raises(CommentError):
        s.delete(c.id, actor_id="stranger", is_admin=False)  # type: ignore[attr-defined]
    s.delete(c.id, actor_id="u1", is_admin=False)  # type: ignore[attr-defined]
    assert s.list_for_call("s1") == []


def test_resolve_and_reopen() -> None:
    s = _store()
    c = _add(s)
    r = s.set_resolved(c.id, actor_id="u2", resolved=True)  # type: ignore[attr-defined]
    assert r.resolved_at is not None and r.resolved_by == "u2"
    r2 = s.set_resolved(c.id, actor_id="u2", resolved=False)  # type: ignore[attr-defined]
    assert r2.resolved_at is None and r2.resolved_by is None


def test_touching_a_missing_comment_raises() -> None:
    s = _store()
    for op in (
        lambda: s.edit("nope", actor_id="u1", is_admin=True, body="x"),
        lambda: s.delete("nope", actor_id="u1", is_admin=True),
        lambda: s.set_resolved("nope", actor_id="u1", resolved=True),
    ):
        with pytest.raises(CommentError):
            op()
