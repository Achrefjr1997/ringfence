"""Org management endpoints (T-7.1c): API key issuance, all admin-only.

The plaintext key is returned exactly once, from ``POST /orgs/keys``.
Everything after that works off the stored ``sha256`` (``identity.store``).
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from apps.gateway.auth import authenticate, read_json_body
from packages.identity.models import User
from packages.identity.store import IdentityError, IdentityStore


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

    return [
        Route("/orgs/keys", create_key, methods=["POST"]),
        Route("/orgs/keys", list_keys, methods=["GET"]),
        Route("/orgs/keys/{key_id}", delete_key, methods=["DELETE"]),
    ]
