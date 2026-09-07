"""Ingest gateway (T-3.3).

    WS  /ws/capture?session=&leg=&tenant=[&consent=]   PCM16 LE binary frames in
    GET /health                                        readiness + admission counters
    GET /events/{session_id}                           SSE stream of decisions

Admission runs in the §3.6 order — tenant, consent, quota, capacity — and
**every rejection is explicit and counted**.  A silent rejection is an
unprotected subscriber.
"""

from __future__ import annotations

import contextlib
import json
import mimetypes
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path

from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from packages.asr.null import NullASR
from packages.asr.provider import ASRProvider, StreamSpec
from packages.contracts.audio import Frame, LegSpec, Mode, RoleHint, SessionDescriptor
from packages.contracts.events import EventBus, InProcessBus
from packages.intervene.cases import Case, CaseStore
from packages.pipeline.pipeline import Pipeline
from packages.policy.pack import PolicyPack, load_pack
from packages.policy.tenants import TenantRegistry, load_tenants, tenant_pattern
from packages.session.manager import SessionManager

_RATE = 16_000  # §3.6 rejection reasons, in check order: TENANT, CONSENT, QUOTA, CAPACITY

# Windows' registry maps .js -> text/plain, which browsers refuse to run as a
# module or an AudioWorklet. Force the correct type before StaticFiles reads it.
mimetypes.add_type("text/javascript", ".js")

ProviderFactory = Callable[[StreamSpec], ASRProvider]


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
    legs: set[str] = field(default_factory=set)


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


def _default_provider_factory() -> ProviderFactory:
    def factory(spec: StreamSpec) -> ASRProvider:
        import os
        from pathlib import Path

        key = os.environ.get("ASSEMBLYAI_API_KEY")
        if not key:
            env = Path(__file__).resolve().parents[2] / ".env"
            if env.exists():
                for line in env.read_text(encoding="utf-8").splitlines():
                    if line.startswith("ASSEMBLYAI_API_KEY="):
                        key = line.split("=", 1)[1].strip() or None
        if not key:
            return NullASR([])  # no ASR configured: sessions run, no transcript
        from packages.asr.assemblyai import AssemblyAIStreaming

        return AssemblyAIStreaming(key)

    return factory


def create_app(
    *,
    provider_factory: ProviderFactory | None = None,
    pack: PolicyPack | None = None,
    bus: EventBus | None = None,
    case_store: CaseStore | None = None,
    tenants: TenantRegistry | None = None,
    quota_per_tenant: int | None = None,
    capacity: int = 500,
    dev_consent: bool = True,
) -> Starlette:
    the_pack = pack or load_pack("config/policy/default.yaml")
    the_bus = bus or InProcessBus()
    the_cases = case_store or CaseStore()
    the_tenants = tenants or load_tenants()
    make_provider = provider_factory or _default_provider_factory()
    metrics = GatewayMetrics()
    sessions: dict[str, _Live] = {}
    sm = SessionManager(bus=the_bus)

    def admit(tenant: str, consent: str | None) -> str | None:
        if not tenant.strip():
            return "TENANT"
        cfg = the_tenants.get(tenant)
        if (cfg.consent_required or not dev_consent) and not consent:
            return "CONSENT"
        quota = quota_per_tenant if quota_per_tenant is not None else cfg.quota
        active_for_tenant = sum(1 for s in sessions.values() if s.tenant == tenant)
        if active_for_tenant >= quota:
            return "QUOTA"
        if len(sessions) >= capacity:
            return "CAPACITY"
        return None

    async def health(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "sessions": len(sessions),
                "capacity": capacity,
                "metrics": metrics.as_dict(),
            }
        )

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

    async def events(request: Request) -> EventSourceResponse:
        session_id = request.path_params["session_id"]
        tenant = request.query_params.get("tenant")
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

    async def list_cases(_: Request) -> JSONResponse:
        return JSONResponse(
            [
                {
                    "session_id": c.session_id,
                    "opened_at": c.opened_at,
                    "peak_state": c.peak_state,
                    "peak_score": round(c.peak_score, 1),
                    "feedback": c.feedback,
                }
                for c in the_cases.list()
            ]
        )

    async def get_case(request: Request) -> JSONResponse:
        case = the_cases.get(request.path_params["session_id"])
        if case is None:
            return JSONResponse({"error": "no such case"}, status_code=404)
        return JSONResponse(_serialise_case(case))

    async def post_feedback(request: Request) -> JSONResponse:
        session_id = request.path_params["session_id"]
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

    async def capture(ws: WebSocket) -> None:
        session = ws.query_params.get("session", "")
        leg = ws.query_params.get("leg", "far")
        tenant = ws.query_params.get("tenant", "")
        consent = ws.query_params.get("consent")

        reason = admit(tenant, consent)
        if reason is not None:
            metrics.rejected[reason] += 1
            await ws.accept()
            await ws.send_json({"type": "rejected", "reason": reason})
            await ws.close(code=1008)
            return

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
            )
            role = RoleHint.CALLER if leg == "far" else RoleHint.CALLEE
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
            live = _Live(pipeline=pipe, tenant=tenant)
            sessions[session] = live
            metrics.admitted += 1
        live.legs.add(leg)

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

    routes: list[Route | WebSocketRoute | Mount] = [
        Route("/health", health),
        Route("/events/{session_id}", events),
        Route("/replay/{fixture_id}", replay_fixture, methods=["POST"]),
        Route("/cases", list_cases),
        Route("/cases/{session_id}", get_case),
        Route("/cases/{session_id}/feedback", post_feedback, methods=["POST"]),
        WebSocketRoute("/ws/capture", capture),
    ]
    console = Path(__file__).resolve().parents[1] / "console"
    if console.is_dir():
        routes.append(Mount("/", app=StaticFiles(directory=console, html=True)))

    app = Starlette(routes=routes)
    app.state.metrics = metrics
    app.state.bus = the_bus
    app.state.sessions = sessions
    return app
