"""Streaming orchestrator (T-2.5).

frames -> ASR -> turns -> role attribution -> extractors -> evidence
window -> combos -> scoring -> state machine -> ``Decision`` on the bus.

Deliberately the same pure components, in the same order, as the offline
``eval.harness`` — so a fixture replayed through :class:`Pipeline` and
``NullASR`` lands on exactly the outcomes the invariant suite asserts.
This task proves the wiring; the logic is proven elsewhere.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from packages.asr.provider import ASRProvider, ASRStream, StreamSpec
from packages.contracts.audio import Frame, SessionDescriptor
from packages.contracts.events import EventBus
from packages.contracts.risk import Contribution, Decision, State, Verdict
from packages.contracts.transcript import AttributedTurn, Role
from packages.media.role import RoleAttributor, StubRoleAttributor, role_for_hint
from packages.policy.pack import PolicyPack, load_pack
from packages.risk.combos import evaluate_combos
from packages.risk.derived import evaluate_derived
from packages.intervene.cases import CaseStore
from packages.risk.judge import DialogueWindow, Judge, should_trigger
from packages.risk.lexical import LexicalExtractor
from packages.risk.lexicons import load_lexicons
from packages.risk.numeric import NumericExtractor
from packages.risk.scoring import EvidenceWindow, score_window
from packages.risk.state import RiskStateMachine
from packages.session.manager import CloseReason, SessionManager, SessionState

_DEFAULT_PACK = load_pack("config/policy/default.yaml")

_RANK: dict[State, int] = {"CALM": 0, "WATCH": 1, "ALERT": 2, "INTERVENE": 3, "RESOLVED": 0}


@dataclass
class SessionResult:
    session_id: str
    peak_score: float
    peak_state: State
    final_state: State
    first_alert_t: float | None
    first_intervene_t: float | None
    turns_seen: int
    session_state: SessionState
    decisions: list[Decision] = field(default_factory=list)


class Pipeline:
    def __init__(
        self,
        provider: ASRProvider,
        *,
        pack: PolicyPack | None = None,
        bus: EventBus | None = None,
        session_manager: SessionManager | None = None,
        judge: Judge | None = None,
        case_store: CaseStore | None = None,
    ) -> None:
        self._provider = provider
        self._pack = pack or _DEFAULT_PACK
        self._bus = bus
        self._sm = session_manager or SessionManager(bus=bus)
        self._judge = judge
        self._cases = case_store
        self._last_judge_t: float | None = None
        self._verdicts: list[Verdict] = []
        self._transcript: list[tuple[Role, str, float]] = []

        self._desc: SessionDescriptor | None = None
        self._stream: ASRStream | None = None
        self._attr: RoleAttributor | None = None
        self._consume: asyncio.Task[None] | None = None

        self._window = EvidenceWindow()
        self._machine: RiskStateMachine | None = None
        self._lexical: LexicalExtractor | None = None
        self._numeric: NumericExtractor | None = None

        self._peak_score = 0.0
        self._peak_state: State = "CALM"
        self._first_alert_t: float | None = None
        self._first_intervene_t: float | None = None
        self._turns_seen = 0
        self._decisions: list[Decision] = []

    # -- lifecycle ---------------------------------------------------------

    async def start(self, desc: SessionDescriptor) -> None:
        self._desc = desc
        await self._sm.admit(desc)
        self._attr = StubRoleAttributor(desc)
        # A single-leg session (SDK / speakerphone) has one hinted role; use it
        # when the stream's leg id doesn't match a descriptor leg.
        self._sole_role: Role | None = (
            role_for_hint(desc.legs[0].role_hint) if len(desc.legs) == 1 else None
        )

        lang = desc.language or "en"
        weights = {sid: spec.weight for sid, spec in self._pack.signals.items()}
        lexicons = load_lexicons(lang)
        for sid, spec in self._pack.signals.items():
            if spec.extra_terms:
                lexicons.setdefault(sid, []).extend(spec.extra_terms)
        self._lexical = LexicalExtractor(lexicons, weights)
        self._numeric = NumericExtractor(weights)
        self._machine = RiskStateMachine(self._pack, session_id=desc.session_id)

        sr = desc.legs[0].sample_rate if desc.legs else 16_000
        stream_spec = StreamSpec(
            session_id=desc.session_id, leg_id="mixed", sample_rate=sr, language=desc.language
        )
        self._stream = await self._provider.open(stream_spec)
        self._consume = asyncio.create_task(self._run())

    async def feed(self, frame: Frame) -> None:
        await self._sm.on_frame(frame)
        if self._stream is not None:
            await self._stream.feed(frame.pcm)

    async def end(self, session_id: str) -> SessionResult:
        if self._stream is not None:
            await self._stream.close()
        if self._consume is not None:
            await self._consume
        s = self._sm.get(session_id)
        if s.state in (SessionState.STREAMING, SessionState.DEGRADED):
            await self._sm.on_hangup(session_id)
            await self._sm.on_drained(session_id)
        elif s.state is not SessionState.CLOSED:
            await self._sm.close(session_id, CloseReason.HANGUP)

        assert self._machine is not None
        return SessionResult(
            session_id=session_id,
            peak_score=self._peak_score,
            peak_state=self._peak_state,
            final_state=self._machine.state,
            first_alert_t=self._first_alert_t,
            first_intervene_t=self._first_intervene_t,
            turns_seen=self._turns_seen,
            session_state=self._sm.get(session_id).state,
            decisions=list(self._decisions),
        )

    # -- turn loop -------------------------------------------------------

    async def _run(self) -> None:
        assert self._stream is not None and self._attr is not None and self._machine is not None
        assert self._lexical is not None and self._numeric is not None
        async for turn in self._stream.turns():
            self._turns_seen += 1
            role = self._attr.role(turn.leg_id)
            if role == "UNKNOWN" and self._sole_role is not None:
                role = self._sole_role
            at = AttributedTurn(turn=turn, role=role, role_confidence=1.0)
            self._transcript.append((at.role, turn.text, turn.t_end))
            hits = [*self._lexical.extract(at), *self._numeric.extract(at)]
            for hit in hits:
                self._window.add(hit)

            if self._bus is not None and self._desc is not None:
                await self._bus.publish(
                    f"rf.{self._desc.tenant_id}.turn",
                    {
                        "session_id": self._machine.session_id,
                        "t_start": turn.t_start,
                        "t_end": turn.t_end,
                        "role": at.role,
                        "text": turn.text,
                        "signals": sorted({h.signal_id for h in hits if h.role == at.role}),
                    },
                )

            now = turn.t_end
            extras = list(evaluate_combos(self._window, now, self._pack))
            extras += evaluate_derived(self._window, now, self._pack)

            if self._judge is not None:
                base, _ = score_window(self._window, now, self._pack, extras=tuple(extras))
                active = self._window.active(now)
                sigs = [h.signal_id for h in active if h.role == "CALLER"]
                protective = {sid for sid, spec in self._pack.signals.items() if spec.weight < 0}
                since = None if self._last_judge_t is None else now - self._last_judge_t
                if should_trigger(
                    score=base,
                    active_signals=sigs,
                    state=self._machine.state,
                    seconds_since_last_call=since,
                    pack=self._pack,
                ):
                    self._last_judge_t = now
                    window = DialogueWindow(
                        session_id=self._machine.session_id,
                        turns=tuple(self._transcript[-8:]),
                        score=base,
                        active_signals=tuple(s for s in sigs if s not in protective),
                        active_protective=tuple(s for s in sigs if s in protective),
                    )
                    verdict = await self._judge.evaluate(window, self._pack)
                    self._verdicts.append(verdict)
                    if verdict.adjustment != 0:
                        extras.append(
                            Contribution(
                                source="judge",
                                id=f"JUDGE_{verdict.verdict.upper()}",
                                value=float(verdict.adjustment),
                                detail=verdict.rationale,
                            )
                        )

            score, contributions = score_window(self._window, now, self._pack, extras=tuple(extras))
            decision = self._machine.update(score, contributions, now)

            state = self._machine.state
            self._peak_score = max(self._peak_score, score)
            if _RANK[state] > _RANK[self._peak_state]:
                self._peak_state = state
            if self._first_alert_t is None and _RANK[state] >= _RANK["ALERT"]:
                self._first_alert_t = now
            if self._first_intervene_t is None and state == "INTERVENE":
                self._first_intervene_t = now

            if decision is not None:
                self._decisions.append(decision)
                if self._cases is not None and _RANK[decision.state] >= _RANK["ALERT"]:
                    tenant = self._desc.tenant_id if self._desc is not None else ""
                    self._cases.record(
                        decision.session_id, decision, self._transcript, tenant=tenant
                    )
                if self._bus is not None and self._desc is not None:
                    await self._bus.publish(
                        f"rf.{self._desc.tenant_id}.decision",
                        {
                            "session_id": decision.session_id,
                            "t": decision.t,
                            "state": decision.state,
                            "score": decision.score,
                            "counterfactual": decision.counterfactual,
                            "contributions": [
                                {
                                    "source": c.source,
                                    "id": c.id,
                                    "value": round(c.value, 2),
                                    "role": c.role,
                                }
                                for c in decision.contributions
                            ],
                        },
                    )
