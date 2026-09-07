"""Identity store (T-7.1a).

``IdentityStore`` is the interface the gateway will depend on; the
in-memory implementation here matches the ``SessionStore`` / ``CaseStore``
idiom used elsewhere in the codebase.  A Postgres-backed implementation
lands in a later T-7.x task — nothing above this interface changes then.
"""

from __future__ import annotations

from typing import Protocol

from packages.identity.keys import hash_key, mint_api_key
from packages.identity.models import ApiKey, Org, Role, User
from packages.identity.passwords import hash_password


class IdentityError(RuntimeError):
    pass


class DuplicateEmail(IdentityError):
    pass


class IdentityStore(Protocol):
    def create_org_with_admin(
        self, *, org_name: str, email: str, password: str
    ) -> tuple[Org, User]: ...

    def add_user(self, *, org_id: str, email: str, password: str, role: Role) -> User: ...

    def get_user(self, user_id: str) -> User | None: ...

    def get_user_by_email(self, email: str) -> User | None: ...

    def get_org(self, org_id: str) -> Org | None: ...

    def issue_api_key(self, *, org_id: str, name: str) -> tuple[ApiKey, str]: ...

    def resolve_api_key(self, plaintext: str) -> ApiKey | None: ...

    def list_api_keys(self, org_id: str) -> list[ApiKey]: ...

    def revoke_api_key(self, *, org_id: str, key_id: str) -> ApiKey: ...


class InMemoryIdentityStore:
    def __init__(self) -> None:
        self._orgs: dict[str, Org] = {}
        self._users: dict[str, User] = {}
        self._user_id_by_email: dict[str, str] = {}
        self._keys: dict[str, ApiKey] = {}
        self._key_id_by_hash: dict[str, str] = {}

    # -- orgs / users --------------------------------------------------

    def create_org_with_admin(
        self, *, org_name: str, email: str, password: str
    ) -> tuple[Org, User]:
        if email.strip().lower() in self._user_id_by_email:
            raise DuplicateEmail(email.strip().lower())
        org = Org(name=org_name)
        org.tenant = org.id
        self._orgs[org.id] = org
        admin = self._add(
            org_id=org.id, email=email, password=password, role="admin", verified=True
        )
        return org, admin

    def add_user(self, *, org_id: str, email: str, password: str, role: Role) -> User:
        if org_id not in self._orgs:
            raise IdentityError(f"no such org {org_id}")
        return self._add(org_id=org_id, email=email, password=password, role=role, verified=False)

    def _add(self, *, org_id: str, email: str, password: str, role: Role, verified: bool) -> User:
        user = User(
            org_id=org_id,
            email=email,
            password_hash=hash_password(password),
            role=role,
            verified=verified,
        )
        if user.email in self._user_id_by_email:
            raise DuplicateEmail(user.email)
        self._users[user.id] = user
        self._user_id_by_email[user.email] = user.id
        return user

    def get_user(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    def get_user_by_email(self, email: str) -> User | None:
        uid = self._user_id_by_email.get(email.strip().lower())
        return self._users.get(uid) if uid is not None else None

    def get_org(self, org_id: str) -> Org | None:
        return self._orgs.get(org_id)

    # -- api keys ---------------------------------------------------

    def issue_api_key(self, *, org_id: str, name: str) -> tuple[ApiKey, str]:
        if org_id not in self._orgs:
            raise IdentityError(f"no such org {org_id}")
        plaintext, prefix, key_hash = mint_api_key()
        key = ApiKey(org_id=org_id, name=name, prefix=prefix, key_hash=key_hash)
        self._keys[key.id] = key
        self._key_id_by_hash[key_hash] = key.id
        return key, plaintext

    def resolve_api_key(self, plaintext: str) -> ApiKey | None:
        key_id = self._key_id_by_hash.get(hash_key(plaintext))
        if key_id is None:
            return None
        key = self._keys.get(key_id)
        return None if key is None or key.revoked else key

    def list_api_keys(self, org_id: str) -> list[ApiKey]:
        return sorted(
            (k for k in self._keys.values() if k.org_id == org_id),
            key=lambda k: k.created_at,
        )

    def revoke_api_key(self, *, org_id: str, key_id: str) -> ApiKey:
        key = self._keys.get(key_id)
        if key is None or key.org_id != org_id:
            raise IdentityError(f"no such key {key_id} for org {org_id}")
        key.revoked = True
        return key
