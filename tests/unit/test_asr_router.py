import asyncio

from packages.asr.null import NullASR
from packages.asr.provider import ASRCapabilities, ASRStream, StreamSpec
from packages.asr.router import (
    ASRRouter,
    BreakerConfig,
    BreakerState,
    RouterPolicy,
    _DegradedStream,
)
from packages.contracts.events import InProcessBus
from packages.contracts.transcript import Turn

SPEC = StreamSpec(session_id="s1", leg_id="far", language="en")
CAPS = ASRCapabilities(languages=("en",))


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def tick(self, dt: float) -> None:
        self.t += dt


class FakeStream:
    def __init__(self, turns: list[Turn] | None = None, *, raise_after: int | None = None) -> None:
        self._turns = turns or []
        self._raise_after = raise_after
        self.closed = False

    async def feed(self, pcm: bytes) -> None:
        return

    async def turns(self):  # noqa: ANN201
        for i, turn in enumerate(self._turns):
            if self._raise_after is not None and i >= self._raise_after:
                raise ConnectionResetError("socket dropped mid-stream")
            yield turn

    async def close(self) -> None:
        self.closed = True


class FakeProvider:
    """Fails its first ``fail_first`` open() calls, then succeeds.  Each
    successful open() advances the injected clock by ``open_latency`` so the
    router records a realistic latency sample."""

    def __init__(
        self,
        name: str,
        clock: FakeClock,
        *,
        fail_first: int = 0,
        always_fail: bool = False,
        open_latency: float = 0.0,
        stream_factory=FakeStream,
    ) -> None:
        self.name = name
        self.capabilities = CAPS
        self._clock = clock
        self._fail_first = fail_first
        self._always_fail = always_fail
        self._open_latency = open_latency
        self._stream_factory = stream_factory
        self.open_calls = 0

    async def open(self, spec: StreamSpec) -> ASRStream:
        self.open_calls += 1
        if self._always_fail or self.open_calls <= self._fail_first:
            raise RuntimeError(f"{self.name} open failed")
        self._clock.tick(self._open_latency)
        return self._stream_factory()


async def _drain(stream: ASRStream) -> list[Turn]:
    return [t async for t in stream.turns()]


# --------------------------------------------------------------------------


async def test_prefers_first_healthy_provider() -> None:
    clock = FakeClock()
    a = FakeProvider("assemblyai", clock)
    b = FakeProvider("local", clock)
    router = ASRRouter([a, b], time_fn=clock.now)

    await router.open(SPEC)
    assert a.open_calls == 1
    assert b.open_calls == 0
    assert router.breaker("assemblyai").state is BreakerState.CLOSED


async def test_fails_over_to_second_provider() -> None:
    clock = FakeClock()
    a = FakeProvider("assemblyai", clock, always_fail=True)
    b = FakeProvider("local", clock)
    router = ASRRouter([a, b], time_fn=clock.now)

    stream = await router.open(SPEC)
    assert isinstance(stream, ASRStream)
    assert a.open_calls == 1 and b.open_calls == 1


async def test_breaker_opens_after_five_consecutive_failures() -> None:
    clock = FakeClock()
    a = FakeProvider("assemblyai", clock, always_fail=True)
    router = ASRRouter([a], fallback=NullASR([]), time_fn=clock.now)

    for _ in range(5):
        await router.open(SPEC)
    assert router.breaker("assemblyai").state is BreakerState.OPEN
    assert a.open_calls == 5

    await router.open(SPEC)  # breaker open: provider must not be tried
    assert a.open_calls == 5


async def test_breaker_half_opens_after_cooldown_then_closes_on_success() -> None:
    clock = FakeClock()
    a = FakeProvider("assemblyai", clock, fail_first=5)
    router = ASRRouter(
        [a], fallback=NullASR([]), time_fn=clock.now, breaker_config=BreakerConfig(cooldown_s=30.0)
    )

    for _ in range(5):
        await router.open(SPEC)
    assert router.breaker("assemblyai").state is BreakerState.OPEN

    clock.tick(30.0)
    await router.open(SPEC)  # trial call, provider has recovered
    assert router.breaker("assemblyai").state is BreakerState.CLOSED
    assert a.open_calls == 6


async def test_half_open_trial_failure_reopens_breaker() -> None:
    clock = FakeClock()
    a = FakeProvider("assemblyai", clock, always_fail=True)
    router = ASRRouter([a], fallback=NullASR([]), time_fn=clock.now)

    for _ in range(5):
        await router.open(SPEC)
    opened_first = router.breaker("assemblyai")._opened_at
    clock.tick(35.0)
    await router.open(SPEC)  # half-open trial fails
    br = router.breaker("assemblyai")
    assert br.state is BreakerState.OPEN
    assert br._opened_at == clock.now() and br._opened_at != opened_first


async def test_p99_latency_over_threshold_opens_breaker() -> None:
    clock = FakeClock()
    a = FakeProvider("assemblyai", clock, open_latency=3.5)
    router = ASRRouter(
        [a],
        time_fn=clock.now,
        breaker_config=BreakerConfig(min_samples_for_p99=20, p99_threshold_s=3.0, window_s=1e9),
    )
    for _ in range(20):
        await router.open(SPEC)
    assert router.breaker("assemblyai").state is BreakerState.OPEN


async def test_all_providers_down_no_fallback_degrades_without_raising() -> None:
    clock = FakeClock()
    bus = InProcessBus()
    a = FakeProvider("assemblyai", clock, always_fail=True)
    b = FakeProvider("local", clock, always_fail=True)
    router = ASRRouter([a, b], bus=bus, policy=RouterPolicy(tenant="acme"), time_fn=clock.now)

    events: list[tuple[str, dict]] = []

    async def collect() -> None:
        async for subject, payload in bus.subscribe("rf.*.degraded"):
            events.append((subject, payload))
            return

    sub = asyncio.create_task(collect())
    await asyncio.sleep(0)

    stream = await router.open(SPEC)
    assert isinstance(stream, _DegradedStream)
    await stream.feed(b"\x00" * 320)
    assert await _drain(stream) == []
    await stream.close()

    await asyncio.wait_for(sub, timeout=1.0)
    assert events == [
        (
            "rf.acme.degraded",
            {"session_id": "s1", "leg_id": "far", "reason": "all ASR providers unavailable"},
        )
    ]


async def test_all_providers_down_falls_back_to_nullasr() -> None:
    clock = FakeClock()
    script = [
        Turn("s1", "far", 0, "hello", True, True, 0.0, 1.0, (), 1.0, "en"),
        Turn("s1", "far", 1, "world", True, True, 2.0, 3.0, (), 1.0, "en"),
    ]
    a = FakeProvider("assemblyai", clock, always_fail=True)
    router = ASRRouter([a], fallback=NullASR(script, speed=1000.0), time_fn=clock.now)

    stream = await router.open(SPEC)
    assert [t.text for t in await _drain(stream)] == ["hello", "world"]


async def test_no_egress_policy_never_touches_cloud_provider() -> None:
    clock = FakeClock()
    cloud = FakeProvider("assemblyai", clock)
    local = FakeProvider("local", clock)
    router = ASRRouter([cloud, local], policy=RouterPolicy(allow_cloud=False), time_fn=clock.now)

    await router.open(SPEC)
    assert cloud.open_calls == 0
    assert local.open_calls == 1


async def test_keyterms_from_policy_are_primed_onto_the_spec() -> None:
    clock = FakeClock()
    seen: list[StreamSpec] = []

    class Recorder(FakeProvider):
        async def open(self, spec: StreamSpec) -> ASRStream:
            seen.append(spec)
            return await super().open(spec)

    router = ASRRouter(
        [Recorder("local", clock)],
        policy=RouterPolicy(keyterms=("Amana Bank", "STB")),
        time_fn=clock.now,
    )
    await router.open(SPEC)
    assert seen[0].keyterms == ("Amana Bank", "STB")


async def test_mid_stream_failure_terminates_cleanly_and_trips_breaker() -> None:
    clock = FakeClock()
    turns = [
        Turn("s1", "far", 0, "hi", True, True, 0.0, 1.0, (), 1.0, "en"),
        Turn("s1", "far", 1, "there", True, True, 2.0, 3.0, (), 1.0, "en"),
    ]

    def factory() -> FakeStream:
        return FakeStream(turns, raise_after=1)

    a = FakeProvider("local", clock, stream_factory=factory)
    router = ASRRouter([a], time_fn=clock.now)

    stream = await router.open(SPEC)
    got = await _drain(stream)  # must not raise
    assert [t.text for t in got] == ["hi"]
    assert router.breaker("local")._consecutive_failures == 1
