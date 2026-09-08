"""Ingress rate limiting (T-7.4).

A token bucket per client IP, refilling at ``rate`` tokens per minute with
burst ``= rate``.  Two buckets per client: a tight one for the auth surface
(``/auth/*``, ``/orgs/*``) where brute force lives, and a loose one for
everything else.  ``/health`` and ``/metrics`` are never limited.

In-memory and per-process: fine for a single node, which is the T-7.2c
topology.  A multi-node deployment needs a shared store (Redis) -- noted,
not built here.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_AUTH_PREFIXES = ("/auth/", "/orgs/")
_EXEMPT = frozenset({"/health", "/metrics"})
_SWEEP_EVERY_S = 300.0


@dataclass
class _Bucket:
    tokens: float
    updated: float

    def take(self, *, rate_per_min: float, now: float) -> float:
        """Consume one token if available.  Returns 0 on success, else the
        number of seconds until the next token."""
        refill = rate_per_min / 60.0
        self.tokens = min(rate_per_min, self.tokens + (now - self.updated) * refill)
        self.updated = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return 0.0
        return (1.0 - self.tokens) / refill


@dataclass
class RateLimiter:
    per_min: int = 120
    auth_per_min: int = 20
    now: Callable[[], float] = time.monotonic
    _buckets: dict[tuple[str, str], _Bucket] = field(default_factory=dict)
    _last_sweep: float = 0.0

    def retry_after(self, client: str, path: str) -> float:
        """0.0 if the request is allowed, else seconds until it would be."""
        lane = "auth" if path.startswith(_AUTH_PREFIXES) else "default"
        rate = self.auth_per_min if lane == "auth" else self.per_min
        now = self.now()
        self._maybe_sweep(now)
        key = (client, lane)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = self._buckets[key] = _Bucket(tokens=float(rate), updated=now)
        return bucket.take(rate_per_min=float(rate), now=now)

    def _maybe_sweep(self, now: float) -> None:
        if now - self._last_sweep < _SWEEP_EVERY_S:
            return
        self._last_sweep = now
        # drop buckets that have had time to refill fully — they carry no state
        stale = [
            k
            for k, b in self._buckets.items()
            if now - b.updated > 120.0 and b.tokens >= min(self.per_min, self.auth_per_min)
        ]
        for k in stale:
            del self._buckets[k]


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, limiter: RateLimiter) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._limiter = limiter

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path in _EXEMPT:
            return await call_next(request)
        wait = self._limiter.retry_after(client_ip(request), path)
        if wait > 0.0:
            retry = max(1, round(wait))
            return JSONResponse(
                {"error": "rate limited", "retry_after": retry},
                status_code=429,
                headers={"Retry-After": str(retry)},
            )
        return await call_next(request)
