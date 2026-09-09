"""Postgres-backed :class:`CommentStore` (oversight console P3).

Same synchronous interface as :class:`InMemoryCommentStore`, on the shared
:class:`~packages.db.loop.LoopThread`. Comment volume is human-paced, so
every call here does its own round trip -- no batching needed.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar

from packages.calls.comments import Comment, CommentError, Visibility, _VIS

_R = TypeVar("_R")
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def schema_sql() -> str:
    return _SCHEMA_PATH.read_text(encoding="utf-8")


class PgCommentStore:
    def __init__(
        self, dsn: str, *, min_size: int = 1, max_size: int = 4, op_timeout_s: float = 10.0
    ) -> None:
        try:
            import asyncpg  # noqa: PLC0415 - optional 'db' extra
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise RuntimeError(
                "PgCommentStore needs the 'db' extra: pip install -e '.[db]'"
            ) from exc

        from packages.db.loop import LoopThread  # noqa: PLC0415

        self._timeout_s = op_timeout_s
        self._closed = False
        self._loop = LoopThread()
        try:

            async def _open() -> Any:
                return await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size)

            self._pool: Any = self._loop.run(_open(), timeout_s=op_timeout_s)
            self._loop.run(self._apply_schema(), timeout_s=op_timeout_s)
        except BaseException:
            self._loop.close()
            raise

    def close_pool(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:

            async def _shut() -> None:
                await self._pool.close()

            self._loop.run(_shut(), timeout_s=self._timeout_s)
        finally:
            self._loop.close()

    async def _apply_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(schema_sql())

    def _run(self, coro: Coroutine[object, object, _R]) -> _R:
        return self._loop.run(coro, timeout_s=self._timeout_s)

    # -- writes -------------------------------------------------

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
            id=uuid.uuid4().hex,
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
        self._run(self._add(c))
        return c

    async def _add(self, c: Comment) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO call_comments "
                "(id, session_id, tenant, author_id, author_email, body, visibility, "
                " t_seconds, parent_id, created_at) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                c.id,
                c.session_id,
                c.tenant,
                c.author_id,
                c.author_email,
                c.body,
                c.visibility,
                c.t_seconds,
                c.parent_id,
                c.created_at,
            )
            if c.mentions:
                await conn.executemany(
                    "INSERT INTO call_comment_mentions (comment_id, mentioned_user_id) "
                    "VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    [(c.id, m) for m in c.mentions],
                )

    def edit(self, comment_id: str, *, actor_id: str, is_admin: bool, body: str) -> Comment:
        if not body.strip():
            raise CommentError("empty comment")
        return self._run(self._edit(comment_id, actor_id, is_admin, body.strip()))

    async def _edit(self, comment_id: str, actor_id: str, is_admin: bool, body: str) -> Comment:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT author_id FROM call_comments WHERE id = $1", comment_id
            )
            if row is None:
                raise CommentError("no such comment")
            if not (is_admin or row["author_id"] == actor_id):
                raise CommentError("not the author")
            await conn.execute(
                "UPDATE call_comments SET body = $2, edited_at = $3 WHERE id = $1",
                comment_id,
                body,
                time.time(),
            )
        return await self._get(comment_id)

    def delete(self, comment_id: str, *, actor_id: str, is_admin: bool) -> None:
        self._run(self._delete(comment_id, actor_id, is_admin))

    async def _delete(self, comment_id: str, actor_id: str, is_admin: bool) -> None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT author_id FROM call_comments WHERE id = $1", comment_id
            )
            if row is None:
                raise CommentError("no such comment")
            if not (is_admin or row["author_id"] == actor_id):
                raise CommentError("not the author")
            await conn.execute("DELETE FROM call_comments WHERE id = $1", comment_id)

    def set_resolved(self, comment_id: str, *, actor_id: str, resolved: bool) -> Comment:
        return self._run(self._set_resolved(comment_id, actor_id, resolved))

    async def _set_resolved(self, comment_id: str, actor_id: str, resolved: bool) -> Comment:
        async with self._pool.acquire() as conn:
            n = await conn.execute(
                "UPDATE call_comments SET resolved_at = $2, resolved_by = $3 WHERE id = $1",
                comment_id,
                time.time() if resolved else None,
                actor_id if resolved else None,
            )
        if n.endswith("0"):
            raise CommentError("no such comment")
        return await self._get(comment_id)

    # -- reads --------------------------------------------------

    def list_for_call(self, session_id: str) -> list[Comment]:
        return self._run(self._list_for_call(session_id))

    async def _list_for_call(self, session_id: str) -> list[Comment]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM call_comments WHERE session_id = $1 ORDER BY created_at",
                session_id,
            )
            ments = await conn.fetch(
                "SELECT comment_id, mentioned_user_id FROM call_comment_mentions m "
                "JOIN call_comments c ON c.id = m.comment_id WHERE c.session_id = $1",
                session_id,
            )
        by_id: dict[str, list[str]] = {}
        for m in ments:
            by_id.setdefault(m["comment_id"], []).append(m["mentioned_user_id"])
        return [_row(r, tuple(by_id.get(r["id"], ()))) for r in rows]

    async def _get(self, comment_id: str) -> Comment:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM call_comments WHERE id = $1", comment_id)
            if row is None:
                raise CommentError("no such comment")
            ments = await conn.fetch(
                "SELECT mentioned_user_id FROM call_comment_mentions WHERE comment_id = $1",
                comment_id,
            )
        return _row(row, tuple(m["mentioned_user_id"] for m in ments))


def _row(r: Any, mentions: tuple[str, ...]) -> Comment:
    return Comment(
        id=r["id"],
        session_id=r["session_id"],
        tenant=r["tenant"],
        author_id=r["author_id"],
        author_email=r["author_email"],
        body=r["body"],
        visibility=r["visibility"],
        t_seconds=r["t_seconds"],
        parent_id=r["parent_id"],
        mentions=mentions,
        created_at=r["created_at"],
        edited_at=r["edited_at"],
        resolved_at=r["resolved_at"],
        resolved_by=r["resolved_by"],
    )
