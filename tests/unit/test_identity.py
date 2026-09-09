"""T-7.1a — identity records + in-memory store."""

import pytest
from pydantic import ValidationError

from packages.identity.keys import hash_key, mint_api_key
from packages.identity.models import Org, User
from packages.identity.passwords import hash_password, verify_password
from packages.identity.store import DuplicateEmail, IdentityError, InMemoryIdentityStore

# -- models ----------------------------------------------------------------


def test_user_email_is_normalised_and_validated() -> None:
    u = User(org_id="o", email="  Alice@Example.COM ", password_hash="x")
    assert u.email == "alice@example.com"
    assert u.role == "operator" and u.verified is False
    with pytest.raises(ValidationError):
        User(org_id="o", email="not-an-email", password_hash="x")


def test_user_role_must_be_known() -> None:
    with pytest.raises(ValidationError):
        User(org_id="o", email="a@b.co", password_hash="x", role="root")  # type: ignore[arg-type]


def test_org_name_must_not_be_blank() -> None:
    with pytest.raises(ValidationError):
        Org(name="   ")
    assert Org(name="  Acme  ").name == "Acme"


# -- passwords -----------------------------------------------------------


def test_password_round_trip() -> None:
    h = hash_password("correct horse battery staple")
    assert h.startswith("scrypt$")
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("wrong", h)


def test_password_hash_is_salted() -> None:
    assert hash_password("same") != hash_password("same")


def test_verify_rejects_a_garbage_hash() -> None:
    assert not verify_password("x", "not-a-hash")
    assert not verify_password("x", "bcrypt$1$2$3$4$5")


def test_empty_password_is_rejected() -> None:
    with pytest.raises(ValueError):
        hash_password("")


# -- api keys ---------------------------------------------------------


def test_mint_key_shape_and_hash() -> None:
    plain, prefix, kh = mint_api_key()
    assert plain.startswith("rf_") and plain.startswith(prefix)
    assert len(prefix) == len("rf_") + 6
    assert kh == hash_key(plain) and len(kh) == 64
    assert mint_api_key()[0] != mint_api_key()[0]


# -- store ------------------------------------------------------------


def _store_with_org() -> tuple[InMemoryIdentityStore, Org, User]:
    s = InMemoryIdentityStore()
    org, admin = s.create_org_with_admin(
        org_name="Acme", email="admin@acme.co", password="pw-12345678"
    )
    return s, org, admin


def test_create_org_with_admin() -> None:
    s, org, admin = _store_with_org()
    assert org.tenant == org.id
    assert admin.role == "admin" and admin.verified is True
    got = s.get_user_by_email("ADMIN@acme.co")
    assert got is not None and got.id == admin.id
    org_back = s.get_org(org.id)
    assert org_back is not None and org_back.name == "Acme"


def test_duplicate_email_is_rejected_across_the_store() -> None:
    s, org, _ = _store_with_org()
    with pytest.raises(DuplicateEmail):
        s.add_user(org_id=org.id, email="admin@acme.co", password="pw-23456789", role="operator")


def test_add_user_defaults_to_unverified() -> None:
    s, org, _ = _store_with_org()
    u = s.add_user(org_id=org.id, email="op@acme.co", password="pw-34567890", role="operator")
    assert u.verified is False and u.role == "operator"
    assert verify_password("pw-34567890", u.password_hash)


def test_add_user_to_a_missing_org_raises() -> None:
    s = InMemoryIdentityStore()
    with pytest.raises(IdentityError):
        s.add_user(org_id="nope", email="a@b.co", password="pw-45678901", role="operator")


def test_api_key_issue_resolve_list_revoke() -> None:
    s, org, _ = _store_with_org()
    key, plaintext = s.issue_api_key(org_id=org.id, name="prod")

    resolved = s.resolve_api_key(plaintext)
    assert resolved is not None and resolved.id == key.id
    assert [k.id for k in s.list_api_keys(org.id)] == [key.id]
    assert s.resolve_api_key("rf_not-a-real-key") is None

    revoked = s.revoke_api_key(org_id=org.id, key_id=key.id)
    assert revoked.revoked is True
    assert s.resolve_api_key(plaintext) is None  # a revoked key stops resolving


def test_cannot_revoke_another_orgs_key() -> None:
    s, org_a, _ = _store_with_org()
    org_b, _ = s.create_org_with_admin(
        org_name="Beta", email="admin@beta.co", password="pw-56789012"
    )
    key, _ = s.issue_api_key(org_id=org_a.id, name="k")
    with pytest.raises(IdentityError):
        s.revoke_api_key(org_id=org_b.id, key_id=key.id)


# -- mutations (T-7.1d) ---------------------------------------------


def test_set_password_and_set_verified() -> None:
    s, org, admin = _store_with_org()
    op = s.add_user(org_id=org.id, email="op@acme.co", password="pw-old-123456", role="operator")
    assert op.verified is False

    s.set_password(op.id, hash_password("pw-new-654321"))
    assert verify_password("pw-new-654321", s.get_user(op.id).password_hash)  # type: ignore[union-attr]

    got = s.set_verified(op.id)
    assert got.verified is True and s.get_user(op.id).verified is True  # type: ignore[union-attr]

    for call in (lambda: s.set_password("nope", "x"), lambda: s.set_verified("nope")):
        with pytest.raises(IdentityError):
            call()


def test_set_manager_and_user_ref() -> None:
    s, org, admin = _store_with_org()
    lead = s.add_user(org_id=org.id, email="lead@acme.co", password="pw-11112222", role="operator")
    rep = s.add_user(org_id=org.id, email="rep@acme.co", password="pw-33334444", role="operator")

    s.set_manager(rep.id, lead.id)
    s.set_user_ref(rep.id, "rep-007")
    got = s.get_user(rep.id)
    assert got is not None and got.manager_id == lead.id and got.user_ref == "rep-007"
    s.set_manager(rep.id, None)
    assert s.get_user(rep.id).manager_id is None  # type: ignore[union-attr]

    with pytest.raises(IdentityError):
        s.set_manager(rep.id, rep.id)  # self
    other_org, other_admin = s.create_org_with_admin(
        org_name="B", email="a@b.co", password="pw-55556666"
    )
    with pytest.raises(IdentityError):
        s.set_manager(rep.id, other_admin.id)  # cross-org
    with pytest.raises(IdentityError):
        s.set_manager("nope", lead.id)


def test_list_users_is_scoped_to_the_org_and_ordered() -> None:
    s, org_a, admin_a = _store_with_org()
    org_b, admin_b = s.create_org_with_admin(
        org_name="Beta", email="admin@beta.co", password="pw-56789012"
    )
    op = s.add_user(org_id=org_a.id, email="op@acme.co", password="pw-34567890", role="operator")
    assert [u.id for u in s.list_users(org_a.id)] == [admin_a.id, op.id]
    assert [u.id for u in s.list_users(org_b.id)] == [admin_b.id]
