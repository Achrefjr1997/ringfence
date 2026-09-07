"""Tier-2 LLM judge (T-2.6, production §7.4).

The judge *adjusts*, it does not decide: its output is clamped to
``±pack.judge.max_adjustment`` so a hallucinating model can only perturb a
score, never fire an intervention (invariant #3).  A late or malformed
verdict is worth nothing — 800 ms timeout, no retry, and a bad response is
treated exactly as a timeout.  ``protective`` is a required output field:
models find fraud far more readily than they notice its absence.

``BoundedJudge`` is the safety wrapper and holds no network code — it takes
an :class:`LLMCaller`.  The real one lives in ``risk.ollama_judge``.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

from packages.contracts.risk import State, Verdict
from packages.contracts.transcript import Role
from packages.policy.pack import PolicyPack
from packages.risk.kb import Excerpt, KnowledgeBase

VerdictLabel = Literal["benign", "unclear", "suspicious", "fraud"]
_TIMEOUT_S = 0.8
_MAX_PROMPT_EXAMPLES = 12


@dataclass(frozen=True, slots=True)
class DialogueWindow:
    session_id: str
    turns: tuple[tuple[Role, str, float], ...]  # (role, text, t)
    score: float
    active_signals: tuple[str, ...]
    active_protective: tuple[str, ...]


class LLMCaller(Protocol):
    async def complete(
        self, system: str, user: str, *, model: str, temperature: float, schema: dict[str, object]
    ) -> str: ...


class Judge(Protocol):
    async def evaluate(self, window: DialogueWindow, pack: PolicyPack) -> Verdict: ...


class _VerdictJSON(BaseModel):
    verdict: VerdictLabel
    adjustment: int
    signals: list[str] = Field(default_factory=list)
    protective: list[str]  # required — see module docstring
    rationale: str


_SCHEMA: dict[str, object] = _VerdictJSON.model_json_schema()


def should_trigger(
    *,
    score: float,
    active_signals: Sequence[str],
    state: State,
    seconds_since_last_call: float | None,
    pack: PolicyPack,
) -> bool:
    """§7.4: score ≥ trigger_score, OR any RAIL_UNUSUAL / REMOTE_ACCESS hit,
    OR every 20 s once in WATCH or above."""
    if score >= pack.judge.trigger_score:
        return True
    if {"RAIL_UNUSUAL", "REMOTE_ACCESS"} & set(active_signals):
        return True
    if state in ("WATCH", "ALERT", "INTERVENE") and (
        seconds_since_last_call is None or seconds_since_last_call >= 20.0
    ):
        return True
    return False


def _miss(reason: str, latency_ms: int, model_version: str) -> Verdict:
    return Verdict(
        verdict="unclear",
        adjustment=0,
        signals=(),
        protective=(),
        rationale=f"judge unavailable: {reason}",
        model_version=model_version,
        latency_ms=latency_ms,
    )


class BoundedJudge:
    def __init__(
        self,
        caller: LLMCaller,
        *,
        model: str,
        timeout_s: float = _TIMEOUT_S,
        kb: KnowledgeBase | None = None,
    ) -> None:
        self._caller = caller
        self._model = model
        self._timeout_s = timeout_s
        self._kb = kb
        self._calls: dict[str, int] = {}
        self.last_prompt_hash: str | None = None

    def calls_made(self, session_id: str) -> int:
        return self._calls.get(session_id, 0)

    async def evaluate(self, window: DialogueWindow, pack: PolicyPack) -> Verdict:
        cap = pack.judge.max_adjustment

        if self._calls.get(window.session_id, 0) >= pack.judge.max_calls_per_session:
            return _miss("session budget exhausted", 0, self._model)
        self._calls[window.session_id] = self._calls.get(window.session_id, 0) + 1

        examples = self._kb.examples_for(window) if self._kb is not None else ()
        system, user = _build_prompt(window, cap, examples)
        self.last_prompt_hash = hashlib.sha256(f"{system}\n{user}".encode()).hexdigest()[:16]

        started = time.perf_counter()
        try:
            raw = await asyncio.wait_for(
                self._caller.complete(
                    system, user, model=self._model, temperature=0.0, schema=_SCHEMA
                ),
                timeout=self._timeout_s,
            )
        except (TimeoutError, asyncio.TimeoutError):
            return _miss("timeout", int((time.perf_counter() - started) * 1000), self._model)
        except Exception as exc:  # noqa: BLE001 — any caller failure is a miss
            return _miss(f"caller error: {type(exc).__name__}", 0, self._model)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        try:
            parsed = _VerdictJSON.model_validate_json(raw)
        except (ValidationError, ValueError):
            return _miss("malformed response", elapsed_ms, self._model)

        adjustment = max(-cap, min(cap, int(parsed.adjustment)))
        return Verdict(
            verdict=parsed.verdict,
            adjustment=adjustment,
            signals=tuple(parsed.signals),
            protective=tuple(parsed.protective),
            rationale=" ".join(parsed.rationale.split()[:25]),
            model_version=self._model,
            latency_ms=elapsed_ms,
        )


def _render_examples(examples: Sequence[Excerpt]) -> str:
    if not examples:
        return ""
    blocks = [
        f"{e.label.upper()} — {e.scam_family} — {', '.join(e.tactics)}\n  {e.text}"
        for e in examples[:_MAX_PROMPT_EXAMPLES]
    ]
    return (
        "\n\nReference patterns (illustrative, not this call):\n\n"
        + "\n\n".join(blocks)
        + "\n\nCalibrate against these: authority and urgency alone are not fraud — a genuine "
        "bank, courier or support line has both. What marks fraud is asking the CALLEE to read "
        "back a one-time code or PIN, move money by gift card / wire / crypto / cash courier, "
        "install remote-access software, keep the call secret, or not call back on an official "
        "number."
    )


def _build_prompt(
    window: DialogueWindow, cap: int, examples: Sequence[Excerpt] = ()
) -> tuple[str, str]:
    system = (
        "You are a fraud analyst reviewing a live phone call. Decide whether the CALLER "
        "is running a scam on the CALLEE. Respond with JSON only: "
        '{"verdict": one of benign|unclear|suspicious|fraud, '
        f'"adjustment": integer in [-{cap}, {cap}] (positive = more suspicious), '
        '"signals": string[] of scam tactics you observed, '
        '"protective": string[] of things that argue AGAINST fraud (REQUIRED — use [] only if '
        'there genuinely are none), "rationale": <= 25 words}. '
        "The rules engine already scored this call; you only nudge that score. "
        "A real bank or delivery company calling is benign even if it sounds urgent."
        + _render_examples(examples)
    )
    lines = [f"[{role}] {text}" for role, text, _ in window.turns]
    user = (
        "Transcript so far:\n"
        + "\n".join(lines)
        + f"\n\nRule score: {window.score:.0f}"
        + f"\nActive risk signals: {', '.join(window.active_signals) or 'none'}"
        + f"\nActive protective signals: {', '.join(window.active_protective) or 'none'}"
    )
    return system, user
