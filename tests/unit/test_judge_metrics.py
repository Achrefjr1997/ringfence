"""Judge observability, and keeping the 4 s budget in one place.

A judge miss is silent by design — ``_miss`` returns ``adjustment=0`` so a
slow or broken judge degrades to rules-only instead of stalling the call.
That is right, and it is also why nobody noticed the judge timing out for
weeks: from outside, "contributed nothing" and "said benign" are the same
thing.  These counters are the difference.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from packages.obs.metrics import MetricsSnapshot, prometheus_text
from packages.policy.pack import load_pack
from packages.risk.judge import DEFAULT_TIMEOUT_S, BoundedJudge, DialogueWindow, JudgeStats

PACK = load_pack("config/policy/default.yaml")
WINDOW = DialogueWindow(
    session_id="s1",
    turns=(("CALLER", "read me the code", 1.0),),
    score=40.0,
    active_signals=("VERIF_INVERT",),
    active_protective=(),
)

_GOOD = '{"verdict":"fraud","adjustment":20,"signals":[],"protective":[],"rationale":"x"}'


class _Caller:
    def __init__(self, *, delay: float = 0.0, raw: str = _GOOD, boom: bool = False) -> None:
        self._delay, self._raw, self._boom = delay, raw, boom

    async def complete(self, system: str, user: str, **kw: object) -> str:
        await asyncio.sleep(self._delay)
        if self._boom:
            raise RuntimeError("upstream is down")
        return self._raw


def _judge(**kw: object) -> BoundedJudge:
    caller = _Caller(**kw)  # type: ignore[arg-type]
    return BoundedJudge(caller, model="m", timeout_s=0.05)  # type: ignore[arg-type]


# -- the budget lives in exactly one place -----------------------------


def test_the_agreed_budget_is_four_seconds() -> None:
    assert DEFAULT_TIMEOUT_S == 4.0


def test_every_config_agrees_with_the_code() -> None:
    """The values drifted to 0.8 / 2.5 / 3 once. Not again."""
    pattern = re.compile(r"RF_JUDGE_TIMEOUT_S[:=]\s*\$?\{?[A-Z_]*:?-?([0-9.]+)")
    checked = 0
    for path in Path("infra").rglob("*"):
        if not path.is_file() or path.suffix not in {".yml", ".yaml"} and "env" not in path.name:
            continue
        for value in pattern.findall(path.read_text(encoding="utf-8", errors="ignore")):
            assert float(value) == DEFAULT_TIMEOUT_S, f"{path} says {value}"
            checked += 1
    # Only the compose files are tracked -- .env* are gitignored, so CI sees
    # two occurrences where a working tree with a local env file sees three.
    assert checked >= 2, f"expected the setting in both compose files, found {checked}"


# -- counters ----------------------------------------------------------


async def test_a_successful_call_is_counted_and_timed() -> None:
    j = _judge()
    await j.evaluate(WINDOW, PACK)
    assert j.stats.calls == 1
    assert j.stats.misses == {}
    assert j.stats.latency_count == 1


async def test_a_timeout_is_counted_as_a_miss_not_a_verdict() -> None:
    j = _judge(delay=0.5)  # timeout_s is 0.05
    verdict = await j.evaluate(WINDOW, PACK)
    assert verdict.adjustment == 0 and verdict.verdict == "unclear"
    assert j.stats.misses == {"timeout": 1}
    assert j.stats.latency_count == 1, "a timeout still tells us how long we waited"


async def test_a_caller_error_is_counted_separately() -> None:
    j = _judge(boom=True)
    await j.evaluate(WINDOW, PACK)
    assert j.stats.misses == {"caller_error": 1}


async def test_a_malformed_response_is_counted_separately() -> None:
    j = _judge(raw="not json at all")
    await j.evaluate(WINDOW, PACK)
    assert j.stats.misses == {"malformed": 1}


async def test_budget_exhaustion_is_counted_and_does_not_consume_a_call() -> None:
    j = _judge()
    for _ in range(PACK.judge.max_calls_per_session):
        await j.evaluate(WINDOW, PACK)
    calls_before = j.stats.calls
    await j.evaluate(WINDOW, PACK)
    assert j.stats.misses == {"budget": 1}
    assert j.stats.calls == calls_before


# -- exposition --------------------------------------------------------


def test_prometheus_renders_the_judge_histogram() -> None:
    stats = JudgeStats()
    for elapsed in (0.3, 1.5, 3.9):
        stats.record(elapsed)
    stats.miss("timeout")
    body = prometheus_text(MetricsSnapshot(active=1, capacity=10, admitted=1, judge=stats))
    assert "# TYPE ringfence_judge_latency_seconds histogram" in body
    assert 'ringfence_judge_latency_seconds_bucket{le="0.5"} 1' in body
    assert 'ringfence_judge_latency_seconds_bucket{le="2.0"} 2' in body
    assert 'ringfence_judge_latency_seconds_bucket{le="+Inf"} 3' in body
    assert "ringfence_judge_latency_seconds_count 3" in body
    assert 'ringfence_judge_misses_total{reason="timeout"} 1' in body


def test_no_judge_configured_renders_no_judge_blocks() -> None:
    body = prometheus_text(MetricsSnapshot(active=0, capacity=10, admitted=0))
    assert "judge" not in body


@pytest.mark.parametrize("elapsed", [0.0, 4.0, 99.0])
def test_buckets_are_cumulative_and_never_exceed_the_count(elapsed: float) -> None:
    stats = JudgeStats()
    stats.record(elapsed)
    for edge in sorted(stats.buckets):
        assert stats.buckets[edge] <= stats.latency_count
