"""Postgres-backed :class:`IdentityStore` (T-7.2a).

Implements the *same* synchronous interface as
:class:`~packages.identity.store.InMemoryIdentityStore` -- ``store.py`` and
the SaaS roadmap both promise nothing above the interface changes when
persistence lands, and it doesn't.  Underneath, ``asyncpg`` runs on a
private :class:`~packages.identity._loop.LoopThread`; every public method
is a thin blocking wrapper around one coroutine.

Validation is unchanged too: rows are built back into the Pydantic models
from :mod:`packages.identity.models`, so the same email-normalisation,
blank-name and role checks apply on the way in and out.

Enable with the ``db`` extra (``pip install -e ".[db]"``) and point
``RF_DATABASE_URL`` at a Postgres; otherwise the gateway keeps the
in-memory store.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from packages.identity._loop import LoopThread
from packages.identity.keys import hash_key, mint_api_key
from packages.identity.migrate import apply_schema
from packages.identity.models import ApiKey, Org, Role, User
from packages.identity.passwords import hash_password
from packages.identity.store import DuplicateEmail, IdentityError

_R = TypeVar("_R")
_M = TypeVar("_M")

_ORG_COLS = "id, name, tenant, created_at"
_USER_COLS = "id, org_id, email, password_hash, role, verified, created_at"
_KEY_COLS = "id, org_id, name, prefix, key_hash, created_at, last_used_at, revoked"


def _org(row: Any) -> Org:
    return Org(id=row["id"], name=row["name"], tenant=row["tenant"], created_at=row["created_at"])


def _user(row: Any) -> User:
    return User(
        id=row["id"],
        org_id=row["org_id"],
        email=row["email"],
        password_hash=row["password_hash"],
        role=row["role"],
        verified=row["verified"],
        created_at=row["created_at"],
    )


def _key(row: Any) -> ApiKey:
    return ApiKey(
        id=row["id"],
        org_id=row["org_id"],
        name=row["name"],
        prefix=row["prefix"],
        key_hash=row["key_hash"],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
        revoked=row["revoked"],
    )


class PgIdentityStore:
    """Drop-in for ``InMemoryIdentityStore`` backed by Postgres."""

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 8,
        op_timeout_s: float = 10.0,
    ) -> None:
        try:
            import asyncpg  # noqa: PLC0415 - optional 'db' extra
        except ModuleNotFoundError as exc:  # pragma: no cover - trivial guard
            raise RuntimeError(
                "PgIdentityStore needs the 'db' extra: pip install -e '.[db]'"
            ) from exc

        self._unique_violation = asyncpg.UniqueViolationError
        self._timeout_s = op_timeout_s
        self._closed = False
        self._loop = LoopThread()
        try:
            # ``create_pool()`` returns an awaitable Pool, not a coroutine, so
            # it can't go straight to ``run_coroutine_threadsafe`` -- wrap it.
            async def _open() -> Any:
                return await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size)

            self._pool: Any = self._loop.run(_open(), timeout_s=op_timeout_s)
            self._loop.run(self._apply_schema(), timeout_s=op_timeout_s)
        except BaseException:
            self._loop.close()
            raise

    # -- lifecycle ------------------------------------------------------

    def close(self) -> None:
        """Close the pool and stop the loop.  Safe to call more than once."""
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
            await apply_schema(conn)

    def _run(self, coro: Coroutine[object, object, _R]) -> _R:
        return self._loop.run(coro, timeout_s=self._timeout_s)

    # -- orgs / users -------------------------------------------------

    def create_org_with_admin(
        self, *, org_name: str, email: str, password: str
    ) -> tuple[Org, User]:
        return self._run(self._create_org_with_admin(org_name, email, password))

    async def _create_org_with_admin(
        self, org_name: str, email: str, password: str
    ) -> tuple[Org, User]:
        org = Org(name=org_name)
        org.tenant = org.id
        admin = User(
            org_id=org.id,
            email=email,
            password_hash=hash_password(password),
            role="admin",
            verified=True,
        )
        async with self._pool.acquire() as conn:
            try:
                async with conn.transaction():
                    await conn.execute(
                        f"INSERT INTO orgs ({_ORG_COLS}) VALUES ($1, $2, $3, $4)",
                        org.id,
                        org.name,
                        org.tenant,
                        org.created_at,
                    )
                    await self._insert_user(conn, admin)
            except self._unique_violation as exc:
                raise DuplicateEmail(admin.email) from exc
        return org, admin

    def add_user(self, *, org_id: str, email: str, password: str, role: Role) -> User:
        return self._run(self._add_user(org_id, email, password, role))

    async def _add_user(self, org_id: str, email: str, password: str, role: Role) -> User:
        user = User(
            org_id=org_id,
            email=email,
            password_hash=hash_password(password),
            role=role,
            verified=False,
        )
        async with self._pool.acquire() as conn:
            if await conn.fetchval("SELECT 1 FROM orgs WHERE id = $1", org_id) is None:
                raise IdentityError(f"no such org {org_id}")
            try:
                await self._insert_user(conn, user)
            except self._unique_violation as exc:
                raise DuplicateEmail(user.email) from exc
        return user

    @staticmethod
    async def _insert_user(conn: Any, user: User) -> None:
        await conn.execute(
            f"INSERT INTO users ({_USER_COLS}) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            user.id,
            user.org_id,
            user.email,
            user.password_hash,
            user.role,
            user.verified,
            user.created_at,
        )

    def get_user(self, user_id: str) -> User | None:
        return self._run(
            self._get_one(f"SELECT {_USER_COLS} FROM users WHERE id = $1", user_id, _user)
        )

    def get_user_by_email(self, email: str) -> User | None:
        return self._run(
            self._get_one(
                f"SELECT {_USER_COLS} FROM users WHERE email = $1", email.strip().lower(), _user
            )
        )

    def get_org(self, org_id: str) -> Org | None:
        return self._run(self._get_one(f"SELECT {_ORG_COLS} FROM orgs WHERE id = $1", org_id, _org))

    async def _get_one(self, sql: str, arg: str, build: Callable[[Any], _M]) -> _M | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, arg)
        return None if row is None else build(row)

    # -- api keys ---------------------------------------------------

    def issue_api_key(self, *, org_id: str, name: str) -> tuple[ApiKey, str]:
        return self._run(self._issue_api_key(org_id, name))

    async def _issue_api_key(self, org_id: str, name: str) -> tuple[ApiKey, str]:
        plaintext, prefix, key_hash = mint_api_key()
        key = ApiKey(org_id=org_id, name=name, prefix=prefix, key_hash=key_hash)
        async with self._pool.acquire() as conn:
            if await conn.fetchval("SELECT 1 FROM orgs WHERE id = $1", org_id) is None:
                raise IdentityError(f"no such org {org_id}")
            await conn.execute(
                f"INSERT INTO api_keys ({_KEY_COLS}) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                key.id,
                key.org_id,
                key.name,
                key.prefix,
                key.key_hash,
                key.created_at,
                key.last_used_at,
                key.revoked,
            )
        return key, plaintext

    def resolve_api_key(self, plaintext: str) -> ApiKey | None:
        return self._run(self._resolve_api_key(plaintext))

    async def _resolve_api_key(self, plaintext: str) -> ApiKey | None:
        # Stamp last_used_at on the hit so the DB row and the returned model
        # both carry it -- the caller cannot persist a mutation of a detached
        # model, unlike the in-memory store.
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"UPDATE api_keys SET last_used_at = extract(epoch from now()) "
                f"WHERE key_hash = $1 AND revoked = FALSE RETURNING {_KEY_COLS}",
                hash_key(plaintext),
            )
        return None if row is None else _key(row)

    def list_api_keys(self, org_id: str) -> list[ApiKey]:
        return self._run(self._list_api_keys(org_id))

    async def _list_api_keys(self, org_id: str) -> list[ApiKey]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_KEY_COLS} FROM api_keys WHERE org_id = $1 ORDER BY created_at",
                org_id,
            )
        return [_key(r) for r in rows]

    def revoke_api_key(self, *, org_id: str, key_id: str) -> ApiKey:
        return self._run(self._revoke_api_key(org_id, key_id))

    async def _revoke_api_key(self, org_id: str, key_id: str) -> ApiKey:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"UPDATE api_keys SET revoked = TRUE WHERE id = $1 AND org_id = $2 "
                f"RETURNING {_KEY_COLS}",
                key_id,
                org_id,
            )
        if row is None:
            raise IdentityError(f"no such key {key_id} for org {org_id}")
        return _key(row)
