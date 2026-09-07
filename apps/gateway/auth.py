"""Auth endpoints (T-7.1b): signup / login / whoami / logout.

Plain Starlette handlers over an :class:`IdentityStore`.  Session state
lives in the bearer token, not the server — the only store touched is
``identity.store``.
"""

from __future__ import annotations

import secrets

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from apps.gateway.tokens import issue_token, read_token
from packages.identity.models import User
from packages.identity.passwords import hash_password, verify_password
from packages.identity.store import DuplicateEmail, IdentityError, IdentityStore

# Verify against this when the email is unknown, so login latency does not
# leak which addresses are registered.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def _account_body(user: User, token: str) -> dict[str, object]:
    return {
        "token": token,
        "user_id": user.id,
        "org_id": user.org_id,
        "email": user.email,
        "role": user.role,
        "verified": user.verified,
    }


def _token_for(user: User, secret: str) -> str:
    return issue_token(user_id=user.id, org_id=user.org_id, role=user.role, secret=secret)


async def _json_body(request: Request) -> dict[str, object]:
    try:
        data = await request.json()
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def authenticate(request: Request, store: IdentityStore, secret: str) -> User | None:
    """Resolve ``Authorization: Bearer <token>`` to a live :class:`User`."""
    header = request.headers.get("authorization", "")
    if header[:7].lower() != "bearer ":
        return None
    claims = read_token(header[7:].strip(), secret=secret)
    if claims is None:
        return None
    user = store.get_user(claims.user_id)
    if user is None or user.org_id != claims.org_id or user.role != claims.role:
        return None  # token outlived a role/org change
    return user


def build_auth_routes(store: IdentityStore, secret: str) -> list[Route]:
    async def signup(request: Request) -> JSONResponse:
        body = await _json_body(request)
        try:
            _, admin = store.create_org_with_admin(
                org_name=str(body.get("org_name", "")),
                email=str(body.get("email", "")),
                password=str(body.get("password", "")),
            )
        except DuplicateEmail as exc:
            return JSONResponse({"error": f"email already registered: {exc}"}, status_code=409)
        except (ValidationError, ValueError, IdentityError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(_account_body(admin, _token_for(admin, secret)), status_code=201)

    async def login(request: Request) -> JSONResponse:
        body = await _json_body(request)
        email = str(body.get("email", ""))
        password = str(body.get("password", ""))
        user = store.get_user_by_email(email) if email else None
        if user is None:
            verify_password(password, _DUMMY_HASH)  # equalise timing
            return JSONResponse({"error": "invalid credentials"}, status_code=401)
        if not verify_password(password, user.password_hash):
            return JSONResponse({"error": "invalid credentials"}, status_code=401)
        return JSONResponse(_account_body(user, _token_for(user, secret)))

    async def whoami(request: Request) -> JSONResponse:
        user = authenticate(request, store, secret)
        if user is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        return JSONResponse(
            {
                "user_id": user.id,
                "org_id": user.org_id,
                "email": user.email,
                "role": user.role,
                "verified": user.verified,
            }
        )

    async def logout(_: Request) -> Response:
        return Response(status_code=204)  # stateless token — advisory

    return [
        Route("/auth/signup", signup, methods=["POST"]),
        Route("/auth/login", login, methods=["POST"]),
        Route("/auth/whoami", whoami),
        Route("/auth/logout", logout, methods=["POST"]),
    ]
