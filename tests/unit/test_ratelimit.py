"""T-7.4 -- ingress token-bucket rate limiter."""

from __future__ import annotations

from apps.gateway.ratelimit import RateLimiter


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_burst_up_to_the_rate_then_blocks() -> None:
    clk = _Clock()
    rl = RateLimiter(per_min=5, auth_per_min=2, now=clk)
    assert all(rl.retry_after("1.2.3.4", "/cases") == 0.0 for _ in range(5))
    wait = rl.retry_after("1.2.3.4", "/cases")
    assert wait > 0.0  # 6th in the same instant is denied


def test_tokens_refill_over_time() -> None:
    clk = _Clock()
    rl = RateLimiter(per_min=60, auth_per_min=2, now=clk)  # 1 token/sec
    for _ in range(60):
        rl.retry_after("c", "/x")
    assert rl.retry_after("c", "/x") > 0.0
    clk.t = 2.0  # two seconds -> two tokens back
    assert rl.retry_after("c", "/x") == 0.0
    assert rl.retry_after("c", "/x") == 0.0
    assert rl.retry_after("c", "/x") > 0.0


def test_auth_lane_is_separate_and_tighter() -> None:
    clk = _Clock()
    rl = RateLimiter(per_min=100, auth_per_min=3, now=clk)
    assert [rl.retry_after("c", "/auth/login") == 0.0 for _ in range(3)] == [True, True, True]
    assert rl.retry_after("c", "/auth/login") > 0.0
    # the default lane for the same client is untouched
    assert rl.retry_after("c", "/cases") == 0.0
    assert rl.retry_after("c", "/orgs/keys") > 0.0  # /orgs/* shares the auth lane


def test_clients_are_isolated() -> None:
    clk = _Clock()
    rl = RateLimiter(per_min=1, auth_per_min=1, now=clk)
    assert rl.retry_after("a", "/x") == 0.0
    assert rl.retry_after("a", "/x") > 0.0
    assert rl.retry_after("b", "/x") == 0.0  # different client, fresh bucket
