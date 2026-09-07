"""Offline fixture replay — the substrate the invariant suite (T-1.9) runs on.

Wires the pure risk components together exactly as the production pipeline
will, but with no ASR, no network and no clock: extractors -> evidence
window -> combos -> scoring -> state machine, one turn at a time, with the
turn's ``t_end`` as ``now``.  No I/O beyond reading the fixture and lexicon
files at import time.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass, field

from packages.contracts.risk import Contribution, Decision, State
from packages.contracts.transcript import AttributedTurn
from packages.eval.fixtures import fixture_turns, load_fixture
from packages.policy.pack import PolicyPack, load_pack
from packages.risk.combos import evaluate_combos
from packages.risk.derived import evaluate_derived
from packages.risk.lexical import LexicalExtractor
from packages.risk.lexicons import load_lexicons
from packages.risk.hypotheses import load_hypotheses
from packages.risk.numeric import NumericExtractor
from packages.risk.scoring import EvidenceWindow, score_window
from packages.risk.semantic import SemanticExtractor, ZeroShotClassifier
from packages.risk.state import RiskStateMachine

DEFAULT_PACK = load_pack("config/policy/default.yaml")

_STATE_RANK: dict[State, int] = {
    "CALM": 0,
    "WATCH": 1,
    "ALERT": 2,
    "INTERVENE": 3,
    "RESOLVED": 0,
}


# ---------------------------------------------------------------------------
# forced-judge override (invariant 3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _JudgeOverride:
    verdict: str
    adjustment: int


_forced_judge: _JudgeOverride | None = None


@contextlib.contextmanager
def judge_forced(*, verdict: str, adjustment: int) -> Iterator[None]:
    """Force the LLM judge to a fixed verdict/adjustment for the duration.

    The harness still clamps the adjustment to ``pack.judge.max_adjustment``
    before it reaches the score — that clamp is invariant 3.
    """
    global _forced_judge
    prev = _forced_judge
    _forced_judge = _JudgeOverride(verdict=verdict, adjustment=adjustment)
    try:
        yield
    finally:
        _forced_judge = prev


def _judge_contribution(pack: PolicyPack) -> Contribution | None:
    if _forced_judge is None or not pack.judge.enabled:
        return None
    cap = pack.judge.max_adjustment
    clamped = max(-cap, min(cap, _forced_judge.adjustment))
    return Contribution(
        source="judge",
        id=f"JUDGE_{_forced_judge.verdict.upper()}",
        value=float(clamped),
        detail=f"forced verdict={_forced_judge.verdict} adj={_forced_judge.adjustment} clamped={clamped}",
    )


# ---------------------------------------------------------------------------
# result
# ---------------------------------------------------------------------------


@dataclass
class TurnTrace:
    t: float
    role: str
    score: float
    state: State
    signals: tuple[str, ...]


@dataclass
class FixtureResult:
    fixture_id: str
    peak_score: float
    peak_state: State
    final_state: State
    first_alert_t: float | None
    first_intervene_t: float | None
    decisions: list[Decision] = field(default_factory=list)
    traces: list[TurnTrace] = field(default_factory=list)

    @property
    def alerted(self) -> bool:
        return self.first_alert_t is not None


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


def run_fixture(
    fixture_id: str,
    *,
    pack: PolicyPack | None = None,
    semantic: ZeroShotClassifier | None = None,
) -> FixtureResult:
    """Replay a fixture through the offline engine.

    ``semantic`` opts in the zero-shot NLI extractor (T-1.7): pass a
    classifier and it runs alongside the lexical/numeric extractors, using
    the hypotheses in ``risk.hypotheses`` and the pack's ``semantic``
    threshold.  Left off, the run stays deterministic and dependency-free.
    """
    pack = pack or DEFAULT_PACK
    fx = load_fixture(fixture_id)

    weights = {sid: spec.weight for sid, spec in pack.signals.items()}
    lexicons = load_lexicons(fx.language)
    for sid, spec in pack.signals.items():
        if spec.extra_terms:
            lexicons.setdefault(sid, []).extend(spec.extra_terms)
    lexical = LexicalExtractor(lexicons, weights)
    numeric = NumericExtractor(weights)

    semantic_extractor: SemanticExtractor | None = None
    if semantic is not None and fx.language in ("en", "fr"):
        semantic_extractor = SemanticExtractor(
            load_hypotheses(fx.language),
            weights,
            semantic,
            threshold=pack.semantic.threshold,
            scale_weight=pack.semantic.scale_weight,
        )

    window = EvidenceWindow()
    machine = RiskStateMachine(pack, session_id=fx.id)

    peak_score = 0.0
    peak_state: State = "CALM"
    first_alert_t: float | None = None
    first_intervene_t: float | None = None
    decisions: list[Decision] = []
    traces: list[TurnTrace] = []

    for turn, ft in zip(fixture_turns(fx), fx.turns, strict=True):
        at = AttributedTurn(turn=turn, role=ft.role, role_confidence=1.0)
        hits = [*lexical.extract(at), *numeric.extract(at)]
        if semantic_extractor is not None:
            hits += semantic_extractor.extract(at)
        for hit in hits:
            window.add(hit)

        now = ft.t_end
        extras: list[Contribution] = list(evaluate_combos(window, now, pack))
        extras += evaluate_derived(window, now, pack)
        judge = _judge_contribution(pack)
        if judge is not None:
            extras.append(judge)

        score, contributions = score_window(window, now, pack, extras=tuple(extras))
        decision = machine.update(score, contributions, now)
        if decision is not None:
            decisions.append(decision)

        state = machine.state
        if score > peak_score:
            peak_score = score
        if _STATE_RANK[state] > _STATE_RANK[peak_state]:
            peak_state = state
        if first_alert_t is None and _STATE_RANK[state] >= _STATE_RANK["ALERT"]:
            first_alert_t = now
        if first_intervene_t is None and state == "INTERVENE":
            first_intervene_t = now

        traces.append(
            TurnTrace(
                t=now,
                role=ft.role,
                score=score,
                state=state,
                signals=tuple(sorted({h.signal_id for h in hits})),
            )
        )

    return FixtureResult(
        fixture_id=fx.id,
        peak_score=peak_score,
        peak_state=peak_state,
        final_state=machine.state,
        first_alert_t=first_alert_t,
        first_intervene_t=first_intervene_t,
        decisions=decisions,
        traces=traces,
    )
