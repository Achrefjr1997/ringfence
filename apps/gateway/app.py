"""Ingest gateway (T-3.3).

    WS  /ws/capture?session=&leg=&tenant=[&consent=]   PCM16 LE binary frames in
    GET /health                                        readiness + admission counters
    GET /events/{session_id}                           SSE stream of decisions

Admission runs in the §3.6 order — tenant, consent, quota, capacity — and
**every rejection is explicit and counted**.  A silent rejection is an
unprotected subscriber.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import mimetypes
import os
import secrets
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from apps.gateway.auth import authenticate, build_auth_routes, read_json_body
from apps.gateway.call_recorder import CallLedgerRecorder
from apps.gateway.guardian import GuardianDispatcher
from apps.gateway.orgs import build_org_routes
from apps.gateway.ratelimit import RateLimiter, RateLimitMiddleware
from packages.asr.null import NullASR
from packages.asr.provider import ASRProvider, StreamSpec
from packages.asr.router import ASRRouter
from packages.billing.meter import BillingStore, InMemoryBillingStore
from packages.billing.plans import get_plans
from packages.billing.provider import BillingProvider, NullBilling
from packages.billing.service import BillingService
from packages.calls.comments import (
    Comment,
    CommentError,
    CommentStore,
    InMemoryCommentStore,
)
from packages.calls.ledger import CallLedger, CallRecord, InMemoryCallLedger
from packages.contracts.audio import Frame, LegSpec, Mode, RoleHint, SessionDescriptor
from packages.contracts.events import EventBus, InProcessBus
from packages.contracts.risk import State
from packages.contracts.settings import get_settings
from packages.identity.models import User
from packages.identity.store import IdentityStore, InMemoryIdentityStore
from packages.intervene.cases import Case, CaseStore, InMemoryCaseStore
from packages.obs.metrics import MetricsSnapshot, prometheus_text
from packages.pipeline.pipeline import Pipeline
from packages.policy.pack import PolicyPack, load_pack
from packages.policy.tenants import TenantRegistry, load_tenants, tenant_pattern
from packages.risk.judge import Judge
from packages.session.manager import SessionManager

log = logging.getLogger("ringfence.gateway")

_RATE = 16_000  # rejection reasons, in check order: AUTH, TENANT, CONSENT, BILLING, QUOTA, CAPACITY


class _AccessLog(BaseHTTPMiddleware):
    """One structured line per HTTP request.  Health and metrics scrapes
    log at DEBUG so a 15 s Prometheus poll does not drown the stream."""

    _QUIET = frozenset({"/health", "/metrics"})

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception(
                "request failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "dur_ms": round((time.perf_counter() - start) * 1000, 1),
                },
            )
            raise
        log.log(
            logging.DEBUG if request.url.path in self._QUIET else logging.INFO,
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "dur_ms": round((time.perf_counter() - start) * 1000, 1),
            },
        )
        return response


# Windows' registry maps .js -> text/plain, which browsers refuse to run as a
# module or an AudioWorklet. Force the correct type before StaticFiles reads it.
mimetypes.add_type("text/javascript", ".js")

ProviderFactory = Callable[[StreamSpec], ASRProvider]
JudgeFactory = Callable[[PolicyPack], Judge | None]


def _read_env_key(name: str) -> str | None:
    """Env var, else a matching line in the repo-root ``.env``."""
    key = os.environ.get(name)
    if key:
        return key
    env = Path(__file__).resolve().parents[2] / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip() or None
    return None


def _default_judge_factory() -> JudgeFactory:
    """Build the Tier-2 LLM judge from the pack + ``OLLAMA_API_KEY``.

    Returns ``None`` (judge disabled, no exception) when the pack turns the
    judge off, the key is absent, or the ``judge`` extra is not installed —
    the pipeline then runs rules-only, exactly as it did before T-2.6b.
    """

    def factory(pack: PolicyPack) -> Judge | None:
        if not pack.judge.enabled:
            return None
        key = _read_env_key("OLLAMA_API_KEY")
        if not key:
            log.warning("judge enabled in pack but OLLAMA_API_KEY not set - running rules-only")
            return None
        from packages.risk.judge import BoundedJudge
        from packages.risk.kb import BM25KnowledgeBase, KnowledgeBase
        from packages.risk.ollama_judge import OllamaCaller

        try:
            caller = OllamaCaller(api_key=key)
        except RuntimeError as exc:  # 'judge' extra missing
            log.warning("judge enabled but unavailable (%s) - running rules-only", exc)
            return None
        try:
            kb: KnowledgeBase | None = BM25KnowledgeBase.load()
        except (OSError, ValueError) as exc:  # a bad KB must not disable the judge
            log.warning("scam knowledge base unavailable (%s) - judge runs without it", exc)
            kb = None
        timeout_s = float(os.environ.get("RF_JUDGE_TIMEOUT_S", "0.8"))
        return BoundedJudge(caller, model=pack.judge.model, timeout_s=timeout_s, kb=kb)

    return factory


def _bearer(header: str | None) -> str | None:
    if header and header[:7].lower() == "bearer ":
        return header[7:].strip() or None
    return None


@dataclass
class GatewayMetrics:
    admitted: int = 0
    rejected: Counter[str] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, object]:
        return {"admitted": self.admitted, "rejected": dict(self.rejected)}


@dataclass
class _Live:
    pipeline: Pipeline
    tenant: str
    started_at: float = 0.0
    legs: set[str] = field(default_factory=set)
    seen_legs: set[str] = field(default_factory=set)  # every leg id ever attached
    api_key_id: str | None = None  # the key this session was admitted with, if any
    user_ref: str | None = None  # the employee the integration attributed this call to


def _serialise_case(case: Case) -> dict[str, object]:
    return {
        "session_id": case.session_id,
        "opened_at": case.opened_at,
        "peak_state": case.peak_state,
        "peak_score": round(case.peak_score, 1),
        "feedback": case.feedback,
        "feedback_note": case.feedback_note,
        "transcript": [{"role": r, "text": t, "t": ts} for (r, t, ts) in case.transcript],
        "decisions": [
            {
                "decision_id": d.decision_id,
                "t": d.t,
                "state": d.state,
                "score": round(d.score, 2),
                "counterfactual": d.counterfactual,
                "contributions": [
                    {"source": c.source, "id": c.id, "value": round(c.value, 2), "role": c.role}
                    for c in d.contributions
                ],
            }
            for d in case.decisions
        ],
    }


def _serialise_verdict(case: Case) -> dict[str, object]:
    """Guardian view — the full decision chain, no transcript text."""
    data = _serialise_case(case)
    data.pop("transcript", None)
    return data


def _serialise_call(rec: CallRecord, *, with_scores: bool = False) -> dict[str, object]:
    out: dict[str, object] = {
        "session_id": rec.session_id,
        "api_key_id": rec.api_key_id,
        "user_ref": rec.user_ref,
        "user_label": rec.user_label,
        "started_at": rec.started_at,
        "ended_at": rec.ended_at,
        "duration_s": round(rec.duration_s, 1),
        "peak_state": rec.peak_state,
        "peak_score": round(rec.peak_score, 1),
        "leg_count": rec.leg_count,
        "live": rec.ended_at is None,
    }
    if with_scores:
        out["scores"] = [
            {"t": round(p.t, 2), "score": round(p.score, 2), "state": p.state} for p in rec.scores
        ]
    return out


def _serialise_comment(c: Comment) -> dict[str, object]:
    return {
        "id": c.id,
        "author_email": c.author_email,
        "body": c.body,
        "visibility": c.visibility,
        "t_seconds": c.t_seconds,
        "parent_id": c.parent_id,
        "mentions": list(c.mentions),
        "created_at": c.created_at,
        "edited_at": c.edited_at,
        "resolved_at": c.resolved_at,
        "resolved_by": c.resolved_by,
    }


_CASE_ROLES = frozenset({"admin", "operator", "guardian"})
_STATE_NAMES = frozenset({"CALM", "WATCH", "ALERT", "INTERVENE", "RESOLVED"})


def _default_asr() -> ASRProvider:
    """The one real ASR provider, built once and shared behind the router.

    ``NullASR`` when no key is configured -- sessions still run, with no
    transcript.
    """
    key = _read_env_key("ASSEMBLYAI_API_KEY")
    if not key:
        return NullASR([])
    from packages.asr.assemblyai import AssemblyAIStreaming

    return AssemblyAIStreaming(key)


def create_app(
    *,
    provider_factory: ProviderFactory | None = None,
    judge_factory: JudgeFactory | None = None,
    pack: PolicyPack | None = None,
    bus: EventBus | None = None,
    case_store: CaseStore | None = None,
    identity: IdentityStore | None = None,
    billing_store: BillingStore | None = None,
    call_ledger: CallLedger | None = None,
    comment_store: CommentStore | None = None,
    billing_provider: BillingProvider | None = None,
    asr: ASRProvider | None = None,
    guardian_dispatcher: GuardianDispatcher | None = None,
    session_secret: str | None = None,
    tenants: TenantRegistry | None = None,
    quota_per_tenant: int | None = None,
    capacity: int = 500,
    dev_consent: bool = True,
    dev_mode: bool = False,
) -> Starlette:
    the_pack = pack or load_pack("config/policy/default.yaml")
    the_bus = bus or InProcessBus()
    the_tenants = tenants or load_tenants()
    on_shutdown: list[Callable[[], object]] = []
    _dsn = get_settings().database_url

    if case_store is not None:
        the_cases: CaseStore = case_store
    elif _dsn:
        from packages.intervene.pg_cases import PgCaseStore

        pg_cases = PgCaseStore(_dsn)
        the_cases = pg_cases
        on_shutdown.append(pg_cases.close)
    else:
        the_cases = InMemoryCaseStore()

    if identity is not None:
        the_identity: IdentityStore = identity
    elif _dsn:
        from packages.identity.pg_store import PgIdentityStore

        pg = PgIdentityStore(_dsn)
        the_identity = pg
        on_shutdown.append(pg.close)
    else:
        the_identity = InMemoryIdentityStore()

    if billing_store is not None:
        the_billing_store: BillingStore = billing_store
    elif _dsn:
        from packages.billing.pg_meter import PgBillingStore

        pg_billing = PgBillingStore(_dsn)
        the_billing_store = pg_billing
        on_shutdown.append(pg_billing.close)
    else:
        the_billing_store = InMemoryBillingStore()
    billing = BillingService(get_plans(), the_billing_store)
    the_billing_provider = billing_provider or NullBilling()

    if call_ledger is not None:
        the_ledger: CallLedger = call_ledger
    elif _dsn:
        from packages.calls.pg_ledger import PgCallLedger

        pg_ledger = PgCallLedger(_dsn)
        the_ledger = pg_ledger
        on_shutdown.append(pg_ledger.close_pool)
    else:
        the_ledger = InMemoryCallLedger()

    if comment_store is not None:
        the_comments_store: CommentStore = comment_store
    elif _dsn:
        from packages.calls.pg_comments import PgCommentStore

        pg_comments = PgCommentStore(_dsn)
        the_comments_store = pg_comments
        on_shutdown.append(pg_comments.close_pool)
    else:
        the_comments_store = InMemoryCommentStore()

    the_secret = session_secret or get_settings().session_secret
    if not the_secret:
        if not dev_mode:
            raise RuntimeError(
                "RF_SESSION_SECRET must be set outside dev_mode - it signs every auth, "
                "verification, reset and invite token; an ephemeral one silently "
                "invalidates them all on restart"
            )
        the_secret = secrets.token_urlsafe(32)
        log.warning("RF_SESSION_SECRET unset - dev_mode: using an ephemeral signing key")
    # A test passing provider_factory keeps the direct path (no router). The
    # real path -- and any injected `asr` -- runs behind an ASRRouter so a
    # flapping provider trips its circuit breaker and the session degrades to
    # NullASR ("rules on signalling only") instead of dropping the call.
    the_router: ASRRouter | None = None
    if provider_factory is not None:
        make_provider = provider_factory
    else:
        base_asr = asr or _default_asr()
        the_router = (
            base_asr
            if isinstance(base_asr, ASRRouter)
            else ASRRouter([base_asr], fallback=NullASR([]), bus=the_bus)
        )

        def make_provider(_spec: StreamSpec) -> ASRProvider:
            assert the_router is not None
            return the_router

    the_judge = (judge_factory or _default_judge_factory())(the_pack)
    metrics = GatewayMetrics()
    sessions: dict[str, _Live] = {}
    sm = SessionManager(bus=the_bus)

    def admit(
        *, api_key: str | None, tenant_hint: str, consent: str | None
    ) -> tuple[str | None, str | None, str | None]:
        """Return ``(tenant, reject_reason, api_key_id)``.

        Exactly one of ``tenant`` / ``reject_reason`` is ``None``.  ``api_key_id``
        is the id of the key the session was admitted with, for per-key usage
        attribution -- ``None`` in ``dev_mode`` (no key required).

        §3.6 order, with AUTH first: outside ``dev_mode`` the tenant comes
        from a valid API key, never a query param.
        """
        key_id: str | None = None
        if dev_mode:
            tenant = tenant_hint
            if not tenant.strip():
                return None, "TENANT", None
            cfg = the_tenants.get(tenant)
            if (cfg.consent_required or not dev_consent) and not consent:
                return None, "CONSENT", None
        else:
            if not api_key:
                return None, "AUTH", None
            key = the_identity.resolve_api_key(api_key)
            if key is None:
                return None, "AUTH", None
            org = the_identity.get_org(key.org_id)
            if org is None:
                return None, "AUTH", None
            key.last_used_at = time.time()
            key_id = key.id
            tenant = org.tenant
            cfg = the_tenants.get(tenant)
            # a hard-capped plan (free/pilot) stops admitting once its
            # monthly minute allowance is spent; metered plans just accrue
            if billing.over_hard_cap(tenant):
                return None, "BILLING", None

        quota = quota_per_tenant if quota_per_tenant is not None else cfg.quota
        active_for_tenant = sum(1 for s in sessions.values() if s.tenant == tenant)
        if active_for_tenant >= quota:
            return None, "QUOTA", None
        if len(sessions) >= capacity:
            return None, "CAPACITY", None
        return tenant, None, key_id

    def read_tenant(request: Request) -> tuple[str | None, str | None]:
        """Resolve the tenant scope for a read endpoint (``/events``).

        In ``dev_mode`` an optional ``?tenant=`` narrows the subscription;
        otherwise a valid API key is required.
        """
        if dev_mode:
            return request.query_params.get("tenant"), None
        api_key = _bearer(request.headers.get("authorization")) or request.query_params.get("key")
        key = the_identity.resolve_api_key(api_key) if api_key else None
        if key is None:
            return None, "AUTH"
        org = the_identity.get_org(key.org_id)
        if org is None:
            return None, "AUTH"
        return org.tenant, None

    async def health(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "sessions": len(sessions),
                "capacity": capacity,
                "metrics": metrics.as_dict(),
            }
        )

    async def metrics_endpoint(_: Request) -> PlainTextResponse:
        by_tenant: Counter[str] = Counter(s.tenant for s in sessions.values())
        body = prometheus_text(
            MetricsSnapshot(
                active=len(sessions),
                capacity=capacity,
                admitted=metrics.admitted,
                rejected=dict(metrics.rejected),
                active_by_tenant=dict(by_tenant),
                asr_breakers=the_router.breaker_states() if the_router is not None else {},
            )
        )
        return PlainTextResponse(body, media_type="text/plain; version=0.0.4")

    async def replay_fixture(request: Request) -> JSONResponse:
        """Dev helper for the console demo: play a fixture's transcript onto
        the shared bus so a browser watching ``session`` sees it live.  Runs
        to completion before responding — use a high ``speed``."""
        fixture_id = request.path_params["fixture_id"]
        session_id = request.query_params.get("session", f"replay-{fixture_id}")
        tenant_id = request.query_params.get("tenant", "replay")
        speed = float(request.query_params.get("speed", "8"))
        from packages.ingress.replay import replay as _replay

        decisions = await _replay(
            fixture_id=fixture_id,
            speed=speed,
            session_id=session_id,
            tenant_id=tenant_id,
            bus=the_bus,
            case_store=the_cases,
        )
        return JSONResponse(
            {
                "replayed": fixture_id,
                "session": session_id,
                "speed": speed,
                "decisions": len(decisions),
            }
        )

    async def events(request: Request) -> Response:
        session_id = request.path_params["session_id"]
        tenant, reason = read_tenant(request)
        if reason is not None:
            return JSONResponse({"error": reason.lower()}, status_code=401)
        pattern = tenant_pattern(tenant) if tenant else "rf.*"

        async def stream() -> AsyncIterator[dict[str, object]]:
            async for subject, payload in the_bus.subscribe(pattern):
                if payload.get("session_id") != session_id:
                    continue
                if subject.endswith(".decision"):
                    yield {"event": "decision", "data": json.dumps(payload)}
                elif subject.endswith(".turn"):
                    yield {"event": "turn", "data": json.dumps(payload)}
                elif subject.endswith(".session.closed"):
                    yield {"event": "end", "data": json.dumps(payload)}
                    return
                if await request.is_disconnected():
                    return

        return EventSourceResponse(stream())

    def case_access(request: Request) -> tuple[User | None, JSONResponse | None]:
        """``(user, error)``.  In ``dev_mode`` returns ``(None, None)`` — no
        auth, every case visible (the current local/demo behaviour)."""
        if dev_mode:
            return None, None
        user = authenticate(request, the_identity, the_secret)
        if user is None:
            return None, JSONResponse({"error": "unauthenticated"}, status_code=401)
        if user.role not in _CASE_ROLES:
            return None, JSONResponse({"error": "forbidden"}, status_code=403)
        return user, None

    async def list_cases(request: Request) -> JSONResponse:
        user, err = case_access(request)
        if err is not None:
            return err
        cases = the_cases.list()
        if user is not None:
            cases = [c for c in cases if c.tenant == user.org_id]
        return JSONResponse(
            [
                {
                    "session_id": c.session_id,
                    "opened_at": c.opened_at,
                    "peak_state": c.peak_state,
                    "peak_score": round(c.peak_score, 1),
                    "feedback": c.feedback,
                }
                for c in cases
            ]
        )

    async def get_case(request: Request) -> JSONResponse:
        user, err = case_access(request)
        if err is not None:
            return err
        case = the_cases.get(request.path_params["session_id"])
        if case is None or (user is not None and case.tenant != user.org_id):
            return JSONResponse({"error": "no such case"}, status_code=404)
        if user is not None and user.role == "guardian":
            return JSONResponse(_serialise_verdict(case))
        return JSONResponse(_serialise_case(case))

    async def post_feedback(request: Request) -> JSONResponse:
        user, err = case_access(request)
        if err is not None:
            return err
        if user is not None and user.role == "guardian":
            return JSONResponse({"error": "forbidden"}, status_code=403)
        session_id = request.path_params["session_id"]
        if user is not None:
            existing = the_cases.get(session_id)
            if existing is None or existing.tenant != user.org_id:
                return JSONResponse({"error": "no such case"}, status_code=404)
        body = await request.json()
        try:
            case = the_cases.set_feedback(
                session_id, str(body.get("label", "")), str(body.get("note", ""))
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except KeyError:
            return JSONResponse({"error": "no such case"}, status_code=404)
        return JSONResponse(_serialise_case(case))

    def _calls_tenant(request: Request, user: User | None) -> str | None:
        if user is not None:
            return user.org_id
        return request.query_params.get("tenant") or None

    async def list_calls(request: Request) -> JSONResponse:
        user, err = case_access(request)
        if err is not None:
            return err
        tenant = _calls_tenant(request, user)
        if not tenant:
            return JSONResponse({"error": "tenant unresolved"}, status_code=400)
        q = request.query_params
        state = q.get("state") or None
        if state is not None and state not in _STATE_NAMES:
            return JSONResponse({"error": "bad state"}, status_code=400)
        try:
            rows = the_ledger.list(
                tenant,
                api_key_id=q.get("key_id") or None,
                user_ref=q.get("user") or None,
                since=float(q["from"]) if q.get("from") else None,
                until=float(q["to"]) if q.get("to") else None,
                min_state=cast(State, state) if state else None,
                limit=min(500, int(q.get("limit", "100"))),
            )
        except ValueError:
            return JSONResponse({"error": "bad from/to/limit"}, status_code=400)
        return JSONResponse([_serialise_call(r) for r in rows])

    async def list_call_users(request: Request) -> JSONResponse:
        user, err = case_access(request)
        if err is not None:
            return err
        tenant = _calls_tenant(request, user)
        if not tenant:
            return JSONResponse({"error": "tenant unresolved"}, status_code=400)
        try:
            since = (
                float(request.query_params["from"]) if request.query_params.get("from") else None
            )
        except ValueError:
            return JSONResponse({"error": "bad from"}, status_code=400)
        return JSONResponse(
            [
                {
                    "user_ref": s.user_ref,
                    "user_label": s.user_label,
                    "calls": s.calls,
                    "alerts": s.alerts,
                    "interventions": s.interventions,
                    "last_at": s.last_at,
                    "peak_state": s.peak_state,
                }
                for s in the_ledger.user_summaries(tenant, since=since)
            ]
        )

    async def get_call(request: Request) -> JSONResponse:
        user, err = case_access(request)
        if err is not None:
            return err
        rec = the_ledger.get(request.path_params["session_id"])
        tenant = _calls_tenant(request, user)
        if rec is None or (tenant is not None and rec.tenant != tenant):
            return JSONResponse({"error": "no such call"}, status_code=404)
        body = _serialise_call(rec, with_scores=True)
        case = the_cases.get(rec.session_id)
        if case is not None and (user is None or case.tenant == rec.tenant):
            body["transcript"] = [{"role": r, "text": t, "t": ts} for (r, t, ts) in case.transcript]
            body["has_case"] = True
        else:
            body["transcript"] = []
            body["has_case"] = False
        return JSONResponse(body)

    # -- threaded review comments (P3) ------------------------------

    def _actor(user: User | None) -> tuple[str, str, bool]:
        """(id, email, is_admin) — a synthetic 'dev' actor when unauthenticated."""
        if user is None:
            return "dev", "dev@local", True
        return user.id, user.email, user.role == "admin"

    def _call_for_comment(request: Request, user: User | None) -> CallRecord | None:
        rec = the_ledger.get(request.path_params["session_id"])
        tenant = _calls_tenant(request, user)
        if rec is None or (tenant is not None and rec.tenant != tenant):
            return None
        return rec

    async def list_comments(request: Request) -> JSONResponse:
        user, err = case_access(request)
        if err is not None:
            return err
        rec = _call_for_comment(request, user)
        if rec is None:
            return JSONResponse({"error": "no such call"}, status_code=404)
        uid, _email, is_admin = _actor(user)
        cs = [
            c
            for c in the_comments_store.list_for_call(rec.session_id)
            if c.visible_to(None if user is None else uid, is_admin=is_admin)
        ]
        return JSONResponse([_serialise_comment(c) for c in cs])

    async def add_comment(request: Request) -> JSONResponse:
        user, err = case_access(request)
        if err is not None:
            return err
        if user is not None and user.role == "guardian":
            return JSONResponse({"error": "forbidden"}, status_code=403)
        rec = _call_for_comment(request, user)
        if rec is None:
            return JSONResponse({"error": "no such call"}, status_code=404)
        uid, email, _is_admin = _actor(user)
        payload = await read_json_body(request)
        t_raw = payload.get("t_seconds")
        mentions = _resolve_mentions(payload.get("mentions", []), rec.tenant)
        try:
            t_seconds = (
                float(t_raw) if isinstance(t_raw, (int, float, str)) and t_raw != "" else None
            )
            c = the_comments_store.add(
                session_id=rec.session_id,
                tenant=rec.tenant,
                author_id=uid,
                author_email=email,
                body=str(payload.get("body", "")),
                visibility=str(payload.get("visibility", "org")),  # type: ignore[arg-type]
                t_seconds=t_seconds,
                parent_id=str(payload["parent_id"]) if payload.get("parent_id") else None,
                mentions=mentions,
            )
        except (CommentError, ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(_serialise_comment(c), status_code=201)

    async def edit_comment(request: Request) -> JSONResponse:
        user, err = case_access(request)
        if err is not None:
            return err
        rec = _call_for_comment(request, user)
        if rec is None:
            return JSONResponse({"error": "no such call"}, status_code=404)
        uid, _email, is_admin = _actor(user)
        body = str((await read_json_body(request)).get("body", ""))
        try:
            c = the_comments_store.edit(
                request.path_params["comment_id"], actor_id=uid, is_admin=is_admin, body=body
            )
        except CommentError as exc:
            code = 404 if "no such" in str(exc) else 403
            return JSONResponse({"error": str(exc)}, status_code=code)
        return JSONResponse(_serialise_comment(c))

    async def delete_comment(request: Request) -> Response:
        user, err = case_access(request)
        if err is not None:
            return err
        rec = _call_for_comment(request, user)
        if rec is None:
            return JSONResponse({"error": "no such call"}, status_code=404)
        uid, _email, is_admin = _actor(user)
        try:
            the_comments_store.delete(
                request.path_params["comment_id"], actor_id=uid, is_admin=is_admin
            )
        except CommentError as exc:
            code = 404 if "no such" in str(exc) else 403
            return JSONResponse({"error": str(exc)}, status_code=code)
        return Response(status_code=204)

    async def resolve_comment(request: Request) -> JSONResponse:
        user, err = case_access(request)
        if err is not None:
            return err
        if user is not None and user.role == "guardian":
            return JSONResponse({"error": "forbidden"}, status_code=403)
        rec = _call_for_comment(request, user)
        if rec is None:
            return JSONResponse({"error": "no such call"}, status_code=404)
        uid, _email, _is_admin = _actor(user)
        resolved = bool((await read_json_body(request)).get("resolved", True))
        try:
            c = the_comments_store.set_resolved(
                request.path_params["comment_id"], actor_id=uid, resolved=resolved
            )
        except CommentError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return JSONResponse(_serialise_comment(c))

    def _resolve_mentions(raw: object, tenant: str) -> tuple[str, ...]:
        if not isinstance(raw, list):
            return ()
        out: list[str] = []
        for entry in raw:
            u = the_identity.get_user_by_email(str(entry).strip().lower())
            if u is not None and u.org_id == tenant:
                out.append(u.id)
        return tuple(dict.fromkeys(out))

    async def capture(ws: WebSocket) -> None:
        session = ws.query_params.get("session", "")
        leg = ws.query_params.get("leg", "far")
        consent = ws.query_params.get("consent")
        api_key = _bearer(ws.headers.get("authorization")) or ws.query_params.get("key")
        # the employee the integration says was on this call (opaque id + label);
        # scoped to the tenant of the API key, so it can only mis-label within
        # that tenant's own data
        user_ref = (ws.query_params.get("user") or ws.headers.get("x-ringfence-user") or "")[:200]
        user_label = (ws.query_params.get("user_label") or "")[:200]

        tenant, reason, key_id = admit(
            api_key=api_key, tenant_hint=ws.query_params.get("tenant", ""), consent=consent
        )
        if reason is not None:
            metrics.rejected[reason] += 1
            await ws.accept()
            await ws.send_json({"type": "rejected", "reason": reason})
            await ws.close(code=1008)
            return
        assert tenant is not None

        await ws.accept()
        live = sessions.get(session)
        if live is None:
            spec = StreamSpec(session_id=session, leg_id=leg, sample_rate=_RATE)
            pipe = Pipeline(
                make_provider(spec),
                pack=the_pack,
                bus=the_bus,
                session_manager=sm,
                case_store=the_cases,
                judge=the_judge,
            )
            # far/near => a dedicated leg with a known role (two-socket SDK);
            # anything else (mixed, the speakerphone default) => attribute
            # each turn acoustically
            role = {"far": RoleHint.CALLER, "near": RoleHint.CALLEE}.get(leg, RoleHint.MIXED)
            await pipe.start(
                SessionDescriptor(
                    session_id=session,
                    tenant_id=tenant,
                    mode=Mode.SDK,
                    legs=(LegSpec(leg_id=leg, role_hint=role, sample_rate=_RATE),),
                    started_at=time.time(),
                    language="en",
                )
            )
            live = _Live(
                pipeline=pipe,
                tenant=tenant,
                started_at=time.time(),
                api_key_id=key_id,
                user_ref=user_ref or None,
            )
            sessions[session] = live
            metrics.admitted += 1
            with contextlib.suppress(Exception):
                the_ledger.open(
                    session,
                    tenant=tenant,
                    api_key_id=key_id,
                    user_ref=user_ref or None,
                    user_label=user_label or None,
                    started_at=live.started_at,
                )
        live.legs.add(leg)
        live.seen_legs.add(leg)

        seq = 0
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                data = msg.get("bytes")
                if data:
                    await live.pipeline.feed(
                        Frame(
                            session_id=session,
                            leg_id=leg,
                            pcm=data,
                            sample_rate=_RATE,
                            seq=seq,
                            captured_at=time.time(),
                        )
                    )
                    seq += 1
        except WebSocketDisconnect:
            pass
        finally:
            live.legs.discard(leg)
            if not live.legs:
                sessions.pop(session, None)
                with contextlib.suppress(Exception):
                    await live.pipeline.end(session)
                closed_at = time.time()
                minutes = max(0.0, (closed_at - live.started_at) / 60.0)
                the_billing_store.record(
                    live.tenant, "call_minutes", minutes, key_id=live.api_key_id, ts=closed_at
                )
                the_billing_store.record(
                    live.tenant, "calls", 1, key_id=live.api_key_id, ts=closed_at
                )
                with contextlib.suppress(Exception):
                    the_billing_provider.report_usage(
                        live.tenant, "call_minutes", minutes, ts=closed_at
                    )
                with contextlib.suppress(Exception):
                    the_ledger.close(session, ended_at=closed_at, leg_count=len(live.seen_legs))

    async def usage(request: Request) -> JSONResponse:
        user, err = case_access(request)
        if err is not None:
            return err
        tenant = user.org_id if user is not None else request.query_params.get("tenant", "")
        if not tenant:
            return JSONResponse({"error": "tenant unresolved"}, status_code=400)
        body = billing.snapshot(tenant).as_dict()
        body["portal_url"] = the_billing_provider.portal_url(tenant)
        return JSONResponse(body)

    async def set_plan(request: Request) -> JSONResponse:
        user = authenticate(request, the_identity, the_secret)
        if user is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        if user.role != "admin":
            return JSONResponse({"error": "admin role required"}, status_code=403)
        plan_id = str((await read_json_body(request)).get("plan", ""))
        if plan_id not in get_plans().ids():
            return JSONResponse(
                {"error": f"unknown plan; choose from {get_plans().ids()}"}, status_code=400
            )
        the_billing_store.set_plan(user.org_id, plan_id)
        return JSONResponse(billing.snapshot(user.org_id).as_dict())

    routes: list[Route | WebSocketRoute | Mount] = [
        Route("/health", health),
        Route("/metrics", metrics_endpoint),
        Route("/events/{session_id}", events),
        Route("/replay/{fixture_id}", replay_fixture, methods=["POST"]),
        Route("/cases", list_cases),
        Route("/cases/{session_id}", get_case),
        Route("/cases/{session_id}/feedback", post_feedback, methods=["POST"]),
        Route("/calls", list_calls),
        Route("/calls/users", list_call_users),
        Route("/calls/{session_id}", get_call),
        Route("/calls/{session_id}/comments", list_comments),
        Route("/calls/{session_id}/comments", add_comment, methods=["POST"]),
        Route("/calls/{session_id}/comments/{comment_id}", edit_comment, methods=["PATCH"]),
        Route("/calls/{session_id}/comments/{comment_id}", delete_comment, methods=["DELETE"]),
        Route(
            "/calls/{session_id}/comments/{comment_id}/resolve",
            resolve_comment,
            methods=["POST"],
        ),
        Route("/usage", usage),
        Route("/orgs/plan", set_plan, methods=["POST"]),
        *build_auth_routes(the_identity, the_secret),
        *build_org_routes(the_identity, the_secret, billing=the_billing_store),
        WebSocketRoute("/ws/capture", capture),
    ]
    console = Path(__file__).resolve().parents[1] / "console"
    if console.is_dir():
        routes.append(Mount("/", app=StaticFiles(directory=console, html=True)))

    cfg = get_settings()
    mw = [Middleware(_AccessLog)]
    if cfg.ratelimit_enabled:
        limiter = RateLimiter(
            per_min=cfg.ratelimit_per_min, auth_per_min=cfg.ratelimit_auth_per_min
        )
        mw.insert(0, Middleware(RateLimitMiddleware, limiter=limiter))

    # guardian webhook dispatch: watch rf.*.decision, fire on INTERVENE
    guardian = guardian_dispatcher or GuardianDispatcher(the_tenants, the_pack, dry_run=cfg.dry_run)
    # call ledger: watch rf.*.decision, append the escalation graph
    recorder = CallLedgerRecorder(the_ledger)

    @contextlib.asynccontextmanager
    async def _lifespan(_: Starlette) -> AsyncIterator[None]:
        tasks = [
            asyncio.create_task(guardian.run(the_bus)),
            asyncio.create_task(recorder.run(the_bus)),
        ]
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(BaseException):
                    await t
            await guardian.aclose()
            for close in on_shutdown:  # pool closes registered above
                close()

    app = Starlette(routes=routes, lifespan=_lifespan, middleware=mw)
    app.state.metrics = metrics
    app.state.bus = the_bus
    app.state.sessions = sessions
    app.state.identity = the_identity
    app.state.session_secret = the_secret
    app.state.cases = the_cases
    app.state.call_ledger = the_ledger
    app.state.comment_store = the_comments_store
    app.state.asr_router = the_router
    app.state.guardian = guardian
    return app
