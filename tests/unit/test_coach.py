"""LLMCoach: turns the single-signal static template into a sentence that
reasons over every active signal at once. Mirrors BoundedJudge's shape
exactly (schema-constrained JSON, hard timeout, degrade on any failure) --
proven safe there, so the same shape is trusted here rather than invented
fresh for a feature that speaks directly to a protected person.
"""

from __future__ import annotations

import asyncio

import pytest

from packages.intervene.coach import CoachRequest, LLMCoach

_MODEL = "test-model"


class _StubCaller:
    def __init__(self, reply: str | None = None, *, delay: float = 0.0, raises: bool = False):
        self.reply = reply
        self.delay = delay
        self.raises = raises
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str, *, model: str, temperature: float, schema):  # noqa: ANN001
        self.calls.append((system, user))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises:
            raise RuntimeError("caller exploded")
        assert self.reply is not None
        return self.reply


def _request(**kw: object) -> CoachRequest:
    base = dict(
        session_id="s1",
        score=90.0,
        signals=(("VERIF_INVERT", "Never read a code back to someone who called you."),),
        language="en",
    )
    base.update(kw)
    return CoachRequest(**base)  # type: ignore[arg-type]


async def test_a_good_reply_is_returned_verbatim() -> None:
    caller = _StubCaller(reply='{"text": "Ask for a callback number and hang up now."}')
    coach = LLMCoach(caller, model=_MODEL)
    text = await coach.suggest(_request())
    assert text == "Ask for a callback number and hang up now."


async def test_the_prompt_names_every_active_signal_not_just_one() -> None:
    """The whole point over the static template: it reasons across the full
    active set, not the single highest-weight signal."""
    caller = _StubCaller(reply='{"text": "ok"}')
    coach = LLMCoach(caller, model=_MODEL)
    await coach.suggest(
        _request(
            signals=(
                ("VERIF_INVERT", "Never read a code back to someone who called you."),
                ("RAIL_UNUSUAL", "A bank never asks for gift cards."),
            )
        )
    )
    _system, user = caller.calls[0]
    assert "VERIF_INVERT" in user and "RAIL_UNUSUAL" in user
    assert "gift cards" in user


async def test_the_prompt_asks_for_the_requested_language() -> None:
    caller = _StubCaller(reply='{"text": "ok"}')
    coach = LLMCoach(caller, model=_MODEL)
    await coach.suggest(_request(language="fr"))
    system, _user = caller.calls[0]
    assert "fr" in system.lower() or "french" in system.lower()


async def test_a_timeout_degrades_to_none_not_an_exception() -> None:
    caller = _StubCaller(reply='{"text": "too slow"}', delay=1.0)
    coach = LLMCoach(caller, model=_MODEL, timeout_s=0.02)
    assert await coach.suggest(_request()) is None


async def test_a_caller_error_degrades_to_none() -> None:
    caller = _StubCaller(raises=True)
    coach = LLMCoach(caller, model=_MODEL)
    assert await coach.suggest(_request()) is None


async def test_malformed_json_degrades_to_none() -> None:
    caller = _StubCaller(reply="not json at all")
    coach = LLMCoach(caller, model=_MODEL)
    assert await coach.suggest(_request()) is None


async def test_a_missing_text_field_degrades_to_none() -> None:
    caller = _StubCaller(reply='{"wrong_field": "hi"}')
    coach = LLMCoach(caller, model=_MODEL)
    assert await coach.suggest(_request()) is None


async def test_an_empty_text_field_degrades_to_none() -> None:
    """An empty sentence is not a coaching message -- treat it as a miss so
    the caller falls back to the static template, not a blank banner."""
    caller = _StubCaller(reply='{"text": "  "}')
    coach = LLMCoach(caller, model=_MODEL)
    assert await coach.suggest(_request()) is None


async def test_an_overlong_reply_is_truncated_not_rejected() -> None:
    long_text = " ".join(["word"] * 60)
    caller = _StubCaller(reply=f'{{"text": "{long_text}"}}')
    coach = LLMCoach(caller, model=_MODEL)
    text = await coach.suggest(_request())
    assert text is not None
    assert len(text.split()) <= 25


@pytest.mark.parametrize("bad_score", [float("nan")])
def test_a_nan_score_is_rejected_at_construction(bad_score: float) -> None:
    """A NaN in the prompt would render as the literal string 'nan' -- catch
    it at the boundary rather than let a broken score reach the model."""
    with pytest.raises(ValueError, match="score"):
        _request(score=bad_score)
