"""Threaded review comments on a call (oversight console P3).

A comment is anchored to a call and optionally to a moment in it
(``t_seconds``). Comments thread via ``parent_id``. Visibility is one of:

* ``org``      -- every reviewer in the tenant who can see the call
* ``private``  -- only the author
* ``mentions`` -- the author plus the users named in ``@mentions``

A mention also *grants* the mentioned user access to the call once
per-call access control lands (P4); until then every reviewer already
sees every call, so the grant is recorded but inert.

``CommentStore`` is a sync Protocol -- in-memory default,
:class:`~packages.calls.pg_comments.PgCommentStore` for Postgres.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, replace
from typing import Literal, Protocol

Visibility = Literal["org", "private", "mentions"]
_VIS: frozenset[str] = frozenset({"org", "private", "mentions"})


@dataclass(frozen=True, slots=True)
class Comment:
    id: str
    session_id: str
    tenant: str
    author_id: str
    author_email: str
    body: str
    visibility: Visibility = "org"
    t_seconds: float | None = None
    parent_id: str | None = None
    mentions: tuple[str, ...] = ()  # user ids
    created_at: float = 0.0
    edited_at: float | None = None
    resolved_at: float | None = None
    resolved_by: str | None = None

    def visible_to(self, user_id: str | None, *, is_admin: bool) -> bool:
        if is_admin or user_id is None:  # dev_mode / admin see everything
            return True
        if self.visibility == "org":
            return True
        if user_id == self.author_id:
            return True
        return self.visibility == "mentions" and user_id in self.mentions


class CommentError(Exception):
    """No such comment, or the caller may not touch it."""


class CommentStore(Protocol):
    def add(
        self,
        *,
        session_id: str,
        tenant: str,
        author_id: str,
        author_email: str,
        body: str,
        visibility: Visibility = "org",
        t_seconds: float | None = None,
        parent_id: str | None = None,
        mentions: tuple[str, ...] = (),
    ) -> Comment: ...

    def list_for_call(self, session_id: str) -> list[Comment]: ...

    def edit(self, comment_id: str, *, actor_id: str, is_admin: bool, body: str) -> Comment: ...

    def delete(self, comment_id: str, *, actor_id: str, is_admin: bool) -> None: ...

    def set_resolved(self, comment_id: str, *, actor_id: str, resolved: bool) -> Comment: ...


def _new_id() -> str:
    return uuid.uuid4().hex


class InMemoryCommentStore:
    def __init__(self) -> None:
        self._by_call: dict[str, list[Comment]] = {}
        self._index: dict[str, str] = {}  # comment id -> session id

    def add(
        self,
        *,
        session_id: str,
        tenant: str,
        author_id: str,
        author_email: str,
        body: str,
        visibility: Visibility = "org",
        t_seconds: float | None = None,
        parent_id: str | None = None,
        mentions: tuple[str, ...] = (),
    ) -> Comment:
        if not body.strip():
            raise CommentError("empty comment")
        if visibility not in _VIS:
            raise CommentError(f"visibility must be one of {sorted(_VIS)}")
        c = Comment(
            id=_new_id(),
            session_id=session_id,
            tenant=tenant,
            author_id=author_id,
            author_email=author_email,
            body=body.strip(),
            visibility=visibility,
            t_seconds=t_seconds,
            parent_id=parent_id,
            mentions=tuple(dict.fromkeys(mentions)),
            created_at=time.time(),
        )
        self._by_call.setdefault(session_id, []).append(c)
        self._index[c.id] = session_id
        return c

    def list_for_call(self, session_id: str) -> list[Comment]:
        return sorted(self._by_call.get(session_id, []), key=lambda c: c.created_at)

    def _find(self, comment_id: str) -> tuple[list[Comment], int]:
        sid = self._index.get(comment_id)
        if sid is None:
            raise CommentError("no such comment")
        lst = self._by_call[sid]
        for i, c in enumerate(lst):
            if c.id == comment_id:
                return lst, i
        raise CommentError("no such comment")

    def edit(self, comment_id: str, *, actor_id: str, is_admin: bool, body: str) -> Comment:
        lst, i = self._find(comment_id)
        if not (is_admin or lst[i].author_id == actor_id):
            raise CommentError("not the author")
        if not body.strip():
            raise CommentError("empty comment")
        lst[i] = replace(lst[i], body=body.strip(), edited_at=time.time())
        return lst[i]

    def delete(self, comment_id: str, *, actor_id: str, is_admin: bool) -> None:
        lst, i = self._find(comment_id)
        if not (is_admin or lst[i].author_id == actor_id):
            raise CommentError("not the author")
        del lst[i]
        self._index.pop(comment_id, None)

    def set_resolved(self, comment_id: str, *, actor_id: str, resolved: bool) -> Comment:
        lst, i = self._find(comment_id)
        lst[i] = replace(
            lst[i],
            resolved_at=time.time() if resolved else None,
            resolved_by=actor_id if resolved else None,
        )
        return lst[i]
