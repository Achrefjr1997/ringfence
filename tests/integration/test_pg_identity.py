"""T-7.2a -- PgIdentityStore against a real Postgres.

Runs only when ``RF_TEST_DATABASE_URL`` points at a reachable database
(CI provides one; locally, ``docker compose up -d postgres`` and
``export RF_TEST_DATABASE_URL=postgres://postgres:dev@localhost:5432/ringfence``).

The assertions deliberately mirror ``tests/unit/test_identity.py`` so the
Postgres store is held to the same contract as the in-memory one.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

pytestmark = pytest.mark.needs_pg

_DSN = os.environ.get("RF_TEST_DATABASE_URL")
if not _DSN:
    pytest.skip("RF_TEST_DATABASE_URL not set", allow_module_level=True)
pytest.importorskip("asyncpg")

from packages.identity.pg_store import PgIdentityStore  # noqa: E402
from packages.identity.store import DuplicateEmail, IdentityError  # noqa: E402


@pytest.fixture()
def store() -> Iterator[PgIdentityStore]:
    s = PgIdentityStore(_DSN or "")
    s._run(_truncate(s))  # start from empty tables
    try:
        yield s
    finally:
        s.close()


async def _truncate(s: PgIdentityStore) -> None:
    async with s._pool.acquire() as conn:
        await conn.execute("TRUNCATE api_keys, users, orgs RESTART IDENTITY CASCADE")


def _org(s: PgIdentityStore):  # noqa: ANN202 - test helper
    return s.create_org_with_admin(org_name="Acme", email="admin@acme.co", password="pw-12345678")


# -- orgs / users ------------------------------------------------------


def test_create_org_with_admin_round_trips(store: PgIdentityStore) -> None:
    org, admin = _org(store)
    assert org.tenant == org.id
    assert admin.role == "admin" and admin.verified is True

    assert store.get_org(org.id) is not None
    got = store.get_user_by_email("ADMIN@acme.co")  # case-insensitive
    assert got is not None and got.id == admin.id
    assert store.get_user(admin.id) is not None
    assert store.get_user("missing") is None
    assert store.get_org("missing") is None


def test_duplicate_email_is_rejected_and_leaves_no_orphan_org(store: PgIdentityStore) -> None:
    _org(store)
    with pytest.raises(DuplicateEmail):
        store.create_org_with_admin(org_name="Other", email="admin@acme.co", password="pw-23456789")
    with pytest.raises(DuplicateEmail):
        store.add_user(
            org_id=_org(store)[0].id, email="admin@acme.co", password="pw-2", role="operator"
        )


def test_add_user_defaults_to_unverified(store: PgIdentityStore) -> None:
    org, _ = _org(store)
    u = store.add_user(org_id=org.id, email="op@acme.co", password="pw-34567890", role="operator")
    assert u.verified is False and u.role == "operator"
    assert store.get_user_by_email("op@acme.co") is not None


def test_add_user_to_a_missing_org_raises(store: PgIdentityStore) -> None:
    with pytest.raises(IdentityError):
        store.add_user(org_id="nope", email="a@b.co", password="pw-45678901", role="operator")


# -- api keys --------------------------------------------------------


def test_api_key_issue_resolve_list_revoke(store: PgIdentityStore) -> None:
    org, _ = _org(store)
    key, plaintext = store.issue_api_key(org_id=org.id, name="prod")

    resolved = store.resolve_api_key(plaintext)
    assert resolved is not None and resolved.id == key.id
    assert resolved.last_used_at is not None  # stamped on resolve
    assert [k.id for k in store.list_api_keys(org.id)] == [key.id]
    assert store.resolve_api_key("rf_not-a-real-key") is None

    revoked = store.revoke_api_key(org_id=org.id, key_id=key.id)
    assert revoked.revoked is True
    assert store.resolve_api_key(plaintext) is None


def test_issue_key_for_a_missing_org_raises(store: PgIdentityStore) -> None:
    with pytest.raises(IdentityError):
        store.issue_api_key(org_id="nope", name="k")


def test_cannot_revoke_another_orgs_key(store: PgIdentityStore) -> None:
    org_a, _ = _org(store)
    org_b, _ = store.create_org_with_admin(
        org_name="Beta", email="admin@beta.co", password="pw-56789012"
    )
    key, _ = store.issue_api_key(org_id=org_a.id, name="k")
    with pytest.raises(IdentityError):
        store.revoke_api_key(org_id=org_b.id, key_id=key.id)


def test_a_second_store_sees_the_first_ones_writes(store: PgIdentityStore) -> None:
    org, _ = _org(store)
    key, plaintext = store.issue_api_key(org_id=org.id, name="prod")

    other = PgIdentityStore(_DSN or "")
    try:
        assert other.get_org(org.id) is not None
        assert other.resolve_api_key(plaintext) is not None
    finally:
        other.close()


# -- mutations (T-7.1d) -------------------------------------------


def test_set_password_set_verified_and_list_users(store: PgIdentityStore) -> None:
    from packages.identity.passwords import hash_password, verify_password

    org, admin = _org(store)
    op = store.add_user(
        org_id=org.id, email="op@acme.co", password="pw-old-123456", role="operator"
    )
    assert op.verified is False

    store.set_password(op.id, hash_password("pw-new-654321"))
    fetched = store.get_user(op.id)
    assert fetched is not None and verify_password("pw-new-654321", fetched.password_hash)

    assert store.set_verified(op.id).verified is True

    assert [u.id for u in store.list_users(org.id)] == [admin.id, op.id]

    with pytest.raises(IdentityError):
        store.set_password("nope", "x")
    with pytest.raises(IdentityError):
        store.set_verified("nope")


def test_set_manager_and_user_ref_round_trip(store: PgIdentityStore) -> None:
    org, admin = _org(store)
    lead = store.add_user(
        org_id=org.id, email="lead@acme.co", password="pw-11112222", role="operator"
    )
    rep = store.add_user(
        org_id=org.id, email="rep@acme.co", password="pw-33334444", role="operator"
    )

    store.set_manager(rep.id, lead.id)
    store.set_user_ref(rep.id, "rep-42")
    got = store.get_user(rep.id)
    assert got is not None and got.manager_id == lead.id and got.user_ref == "rep-42"

    store.set_manager(rep.id, None)
    assert store.get_user(rep.id).manager_id is None  # type: ignore[union-attr]

    with pytest.raises(IdentityError):
        store.set_manager(rep.id, rep.id)
