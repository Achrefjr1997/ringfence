"""Postgres-backed :class:`TranscriptStore` (oversight console P6).

``save`` replaces the whole transcript for a session in one transaction
(it fires once, on session close). On the shared
:class:`~packages.db.loop.LoopThread`.
"""

from __future__ import annotations

from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar

from packages.calls.transcripts import Turn

_R = TypeVar("_R")
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def schema_sql() -> str:
    return _SCHEMA_PATH.read_text(encoding="utf-8")


class PgTranscriptStore:
    def __init__(
        self, dsn: str, *, min_size: int = 1, max_size: int = 4, op_timeout_s: float = 10.0
    ) -> None:
        try:
            import asyncpg  # noqa: PLC0415 - optional 'db' extra
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise RuntimeError(
                "PgTranscriptStore needs the 'db' extra: pip install -e '.[db]'"
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

    def save(self, session_id: str, tenant: str, turns: list[Turn]) -> None:
        if not turns:
            return
        self._run(self._save(session_id, turns))

    async def _save(self, session_id: str, turns: list[Turn]) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            # only if the call exists -- avoids a dangling transcript
            if (
                await conn.fetchval("SELECT 1 FROM call_ledger WHERE session_id = $1", session_id)
                is None
            ):
                return
            await conn.execute("DELETE FROM call_transcript WHERE session_id = $1", session_id)
            await conn.executemany(
                "INSERT INTO call_transcript (session_id, seq, role, text, t) "
                "VALUES ($1, $2, $3, $4, $5)",
                [(session_id, i, r, txt, t) for i, (r, txt, t) in enumerate(turns)],
            )

    def get(self, session_id: str) -> list[Turn]:
        return self._run(self._get(session_id))

    async def _get(self, session_id: str) -> list[Turn]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT role, text, t FROM call_transcript WHERE session_id = $1 ORDER BY seq",
                session_id,
            )
        return [(r["role"], r["text"], r["t"]) for r in rows]
