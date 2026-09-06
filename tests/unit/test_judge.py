"""T-2.6: the bounded LLM judge, exercised with a fake caller (no network)."""

import asyncio
import json


from packages.policy.pack import load_pack
from packages.risk.judge import BoundedJudge, DialogueWindow, should_trigger

PACK = load_pack("config/policy/default.yaml")  # max_adjustment=30, trigger_score=35, budget=12
CAP = PACK.judge.max_adjustment

WINDOW = DialogueWindow(
    session_id="s1",
    turns=(
        ("CALLER", "this is your bank's fraud team, act now", 4.0),
        ("CALLEE", "ok what do I do", 6.0),
    ),
    score=48.0,
    active_signals=("AUTH_CLAIM", "URGENCY"),
    active_protective=(),
)


class FakeCaller:
    def __init__(self, reply: str | Exception, *, delay: float = 0.0) -> None:
        self.reply = reply
        self.delay = delay
        self.calls: list[dict[str, object]] = []

    async def complete(
        self, system: str, user: str, *, model: str, temperature: float, schema: dict[str, object]
    ) -> str:
        self.calls.append(
            {"system": system, "user": user, "model": model, "temperature": temperature}
        )
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _verdict_json(**over: object) -> str:
    base = {
        "verdict": "suspicious",
        "adjustment": 15,
        "signals": ["AUTH_CLAIM"],
        "protective": [],
        "rationale": "caller impersonates the bank and pressures for immediate action",
    }
    base.update(over)
    return json.dumps(base)


# --------------------------------------------------------------------------


async def test_valid_verdict_passes_through_bounded() -> None:
    j = BoundedJudge(FakeCaller(_verdict_json(adjustment=12)), model="gpt-oss:120b")
    v = await j.evaluate(WINDOW, PACK)
    assert v.verdict == "suspicious"
    assert v.adjustment == 12
    assert v.signals == ("AUTH_CLAIM",)
    assert v.model_version == "gpt-oss:120b"
    assert v.latency_ms >= 0
    assert j.last_prompt_hash is not None


async def test_adjustment_999_is_clamped_to_max_adjustment() -> None:
    j = BoundedJudge(FakeCaller(_verdict_json(adjustment=999)), model="m")
    assert (await j.evaluate(WINDOW, PACK)).adjustment == CAP


async def test_negative_overrun_is_clamped() -> None:
    j = BoundedJudge(FakeCaller(_verdict_json(adjustment=-999, verdict="benign")), model="m")
    assert (await j.evaluate(WINDOW, PACK)).adjustment == -CAP


async def test_malformed_json_yields_adjustment_zero() -> None:
    j = BoundedJudge(FakeCaller("not json at all"), model="m")
    v = await j.evaluate(WINDOW, PACK)
    assert v.adjustment == 0 and v.verdict == "unclear"


async def test_schema_invalid_json_yields_adjustment_zero() -> None:
    # valid JSON, but 'protective' (required) missing
    j = BoundedJudge(FakeCaller(json.dumps({"verdict": "fraud", "adjustment": 20})), model="m")
    assert (await j.evaluate(WINDOW, PACK)).adjustment == 0


async def test_timeout_yields_adjustment_zero() -> None:
    j = BoundedJudge(FakeCaller(_verdict_json(), delay=0.5), model="m", timeout_s=0.05)
    v = await j.evaluate(WINDOW, PACK)
    assert v.adjustment == 0
    assert "timeout" in v.rationale


async def test_caller_exception_yields_adjustment_zero() -> None:
    j = BoundedJudge(FakeCaller(RuntimeError("boom")), model="m")
    assert (await j.evaluate(WINDOW, PACK)).adjustment == 0


async def test_per_session_budget_is_enforced() -> None:
    caller = FakeCaller(_verdict_json())
    j = BoundedJudge(caller, model="m")
    budget = PACK.judge.max_calls_per_session
    for _ in range(budget):
        assert (await j.evaluate(WINDOW, PACK)).adjustment == 15
    # budget hit: no more real calls, miss verdict
    v = await j.evaluate(WINDOW, PACK)
    assert v.adjustment == 0 and "budget" in v.rationale
    assert len(caller.calls) == budget  # the over-budget call never reached the LLM
    assert j.calls_made("s1") == budget


async def test_temperature_zero_is_sent() -> None:
    caller = FakeCaller(_verdict_json())
    await BoundedJudge(caller, model="m").evaluate(WINDOW, PACK)
    assert caller.calls[0]["temperature"] == 0.0


async def test_rationale_is_capped_at_25_words() -> None:
    long = " ".join(["word"] * 60)
    j = BoundedJudge(FakeCaller(_verdict_json(rationale=long)), model="m")
    assert len((await j.evaluate(WINDOW, PACK)).rationale.split()) == 25


def test_should_trigger_rules() -> None:
    assert should_trigger(
        score=40, active_signals=[], state="CALM", seconds_since_last_call=None, pack=PACK
    )
    assert should_trigger(
        score=10,
        active_signals=["RAIL_UNUSUAL"],
        state="CALM",
        seconds_since_last_call=1,
        pack=PACK,
    )
    assert should_trigger(
        score=10, active_signals=[], state="WATCH", seconds_since_last_call=25, pack=PACK
    )
    assert not should_trigger(
        score=10, active_signals=[], state="WATCH", seconds_since_last_call=5, pack=PACK
    )
    assert not should_trigger(
        score=10, active_signals=[], state="CALM", seconds_since_last_call=None, pack=PACK
    )
