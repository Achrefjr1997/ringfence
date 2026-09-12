"""Dynamic coaching text (T-4.2 extension).

InterventionService's static ``templates.yaml`` lookup picks exactly one
signal -- the single highest-weight CALLER signal in the last 60 s -- and
returns pre-written copy for it. That stays the safe, always-available
default. ``LLMCoach`` is additive: given every active signal at once, it
can name the *specific combination* happening right now ("they asked for
a code and are pushing urgency") in one sentence, rather than whichever
single signal happened to weigh the most.

Mirrors ``packages.risk.judge.BoundedJudge`` exactly -- schema-constrained
JSON, a hard timeout, degrade to ``None`` (never raise) on any failure --
because that shape is already proven safe for exactly this problem, and a
feature that speaks directly to a protected person must never surface a
model's failure as anything but silence. The caller falls back to the
static template when this returns ``None``.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, Field, ValidationError

from packages.risk.judge import LLMCaller

# Short: this rides alongside an already-fired INTERVENE decision to
# refine what is shown, never gates it -- the static template is already
# live by the time this is called (see apps/gateway/intervene_dispatch.py).
_TIMEOUT_S = 2.0
_MAX_WORDS = 25

_LANGUAGE_NAMES = {"en": "English", "fr": "French", "ar_tn": "Tunisian Arabic"}


@dataclass(frozen=True, slots=True)
class CoachRequest:
    session_id: str
    score: float
    # (signal_id, its static template copy) for every active CALLER signal --
    # the copy grounds the model in pre-approved framing instead of letting
    # it invent claims from a bare, cryptic signal id.
    signals: tuple[tuple[str, str], ...]
    language: str

    def __post_init__(self) -> None:
        if math.isnan(self.score):
            raise ValueError("score must not be NaN")


class CoachGenerator(Protocol):
    async def suggest(self, request: CoachRequest) -> str | None: ...


class _CoachJSON(BaseModel):
    text: str = Field(default="")


_SCHEMA: dict[str, object] = _CoachJSON.model_json_schema()


def _build_prompt(request: CoachRequest) -> tuple[str, str]:
    lang_name = _LANGUAGE_NAMES.get(request.language, request.language)
    system = (
        "A phone call is being scored live for a scam in progress. The signals "
        "below already fired and are trusted -- do not invent anything beyond "
        "them. Write ONE short, imperative coaching sentence, at most 25 words, "
        f"for the person currently on the call, in {lang_name}. Respond with "
        'JSON only: {"text": "<the sentence>"}.'
    )
    signal_lines = "\n".join(f"- {sid}: {copy}" for sid, copy in request.signals)
    user = (
        f"Risk score: {request.score:.0f}\n"
        f"Active warning signs:\n{signal_lines}\n\n"
        "Combine these into one specific, actionable sentence -- not a generic "
        "'this may be a scam' warning."
    )
    return system, user


class LLMCoach:
    def __init__(self, caller: LLMCaller, *, model: str, timeout_s: float = _TIMEOUT_S) -> None:
        self._caller = caller
        self._model = model
        self._timeout_s = timeout_s

    async def suggest(self, request: CoachRequest) -> str | None:
        system, user = _build_prompt(request)
        try:
            raw = await asyncio.wait_for(
                self._caller.complete(
                    system, user, model=self._model, temperature=0.0, schema=_SCHEMA
                ),
                timeout=self._timeout_s,
            )
        except (TimeoutError, asyncio.TimeoutError):
            return None
        except Exception:  # noqa: BLE001 - any caller failure degrades to the static fallback
            return None

        try:
            parsed = _CoachJSON.model_validate_json(raw)
        except (ValidationError, ValueError):
            return None

        words = parsed.text.split()  # also normalises internal whitespace
        if not words:
            return None
        return " ".join(words[:_MAX_WORDS])
