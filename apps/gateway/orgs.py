"""Org management endpoints, all admin-only:

* API key issuance (T-7.1c) -- the plaintext key is returned once, from
  ``POST /orgs/keys``; everything after works off the stored ``sha256``.
* Operator / guardian invites (T-7.1d) -- ``POST /orgs/users`` creates the
  account with a random password and returns a one-time ``invite_token``
  the invitee redeems at ``POST /auth/accept``.
"""

from __future__ import annotations

import secrets
from typing import cast

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from apps.gateway.auth import authenticate, read_json_body
from apps.gateway.purpose_tokens import bind_for, issue_purpose_token
from packages.identity.models import Role, User
from packages.identity.store import DuplicateEmail, IdentityError, IdentityStore

_INVITE_ROLES = ("operator", "guardian")


def build_org_routes(store: IdentityStore, secret: str) -> list[Route]:
    def _require_admin(request: Request) -> User | JSONResponse:
        user = authenticate(request, store, secret)
        if user is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        if user.role != "admin":
            return JSONResponse({"error": "admin role required"}, status_code=403)
        return user

    async def create_key(request: Request) -> Response:
        user = _require_admin(request)
        if isinstance(user, JSONResponse):
            return user
        body = await read_json_body(request)
        name = str(body.get("name", "")).strip() or "unnamed"
        key, plaintext = store.issue_api_key(org_id=user.org_id, name=name)
        return JSONResponse(
            {
                "id": key.id,
                "name": key.name,
                "prefix": key.prefix,
                "key": plaintext,  # shown once
                "created_at": key.created_at,
            },
            status_code=201,
        )

    async def list_keys(request: Request) -> Response:
        user = _require_admin(request)
        if isinstance(user, JSONResponse):
            return user
        return JSONResponse(
            [
                {
                    "id": k.id,
                    "name": k.name,
                    "prefix": k.prefix,
                    "created_at": k.created_at,
                    "last_used_at": k.last_used_at,
                    "revoked": k.revoked,
                }
                for k in store.list_api_keys(user.org_id)
            ]
        )

    async def delete_key(request: Request) -> Response:
        user = _require_admin(request)
        if isinstance(user, JSONResponse):
            return user
        try:
            store.revoke_api_key(org_id=user.org_id, key_id=request.path_params["key_id"])
        except IdentityError:
            return JSONResponse({"error": "no such key"}, status_code=404)
        return Response(status_code=204)

    async def create_user(request: Request) -> Response:
        admin = _require_admin(request)
        if isinstance(admin, JSONResponse):
            return admin
        body = await read_json_body(request)
        role = str(body.get("role", "operator"))
        if role not in _INVITE_ROLES:
            return JSONResponse(
                {"error": f"role must be one of {list(_INVITE_ROLES)}"}, status_code=400
            )
        try:
            invited = store.add_user(
                org_id=admin.org_id,
                email=str(body.get("email", "")),
                password=secrets.token_urlsafe(32),  # placeholder; set at /auth/accept
                role=cast(Role, role),  # checked against _INVITE_ROLES above
            )
        except DuplicateEmail as exc:
            return JSONResponse({"error": f"email already registered: {exc}"}, status_code=409)
        except (ValidationError, ValueError, IdentityError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        token = issue_purpose_token(
            purpose="invite",
            subject=invited.id,
            secret=secret,
            bind=bind_for(invited.password_hash),
        )
        return JSONResponse(
            {
                "user_id": invited.id,
                "email": invited.email,
                "role": invited.role,
                "invite_token": token,
            },
            status_code=201,
        )

    async def list_org_users(request: Request) -> Response:
        admin = _require_admin(request)
        if isinstance(admin, JSONResponse):
            return admin
        return JSONResponse(
            [
                {
                    "user_id": u.id,
                    "email": u.email,
                    "role": u.role,
                    "verified": u.verified,
                    "created_at": u.created_at,
                }
                for u in store.list_users(admin.org_id)
            ]
        )

    return [
        Route("/orgs/keys", create_key, methods=["POST"]),
        Route("/orgs/keys", list_keys, methods=["GET"]),
        Route("/orgs/keys/{key_id}", delete_key, methods=["DELETE"]),
        Route("/orgs/users", create_user, methods=["POST"]),
        Route("/orgs/users", list_org_users, methods=["GET"]),
    ]
