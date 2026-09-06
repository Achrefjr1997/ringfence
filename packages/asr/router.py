"""ASR router: per-tenant provider selection, failover, and a circuit
breaker per provider (production §5.3).

The breaker opens on 5 consecutive failures or p99 > 3 s over a 30 s
window, and half-opens 30 s later.  When every provider is unavailable the
router degrades — to ``NullASR`` if one is supplied, otherwise to an inert
stream — and emits ``rf.<tenant>.degraded``.  It never lets an exception
reach the caller: losing transcription must not end the call.
"""

from __future__ import annotations

import contextlib
import dataclasses
import time
from collections import deque
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum

from packages.asr.provider import ASRCapabilities, ASRProvider, ASRStream, StreamSpec
from packages.contracts.events import EventBus
from packages.contracts.transcript import Turn

CLOUD_PROVIDERS = frozenset({"assemblyai"})


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class BreakerConfig:
    failure_threshold: int = 5
    p99_threshold_s: float = 3.0
    window_s: float = 30.0
    cooldown_s: float = 30.0
    min_samples_for_p99: int = 20


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        config: BreakerConfig | None = None,
        *,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.config = config or BreakerConfig()
        self._now = time_fn
        self.state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._latencies: deque[tuple[float, float]] = deque()
        self._opened_at: float | None = None

    def allow(self) -> bool:
        if self.state is BreakerState.OPEN:
            assert self._opened_at is not None
            if self._now() - self._opened_at >= self.config.cooldown_s:
                self.state = BreakerState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self, latency_s: float) -> None:
        self._consecutive_failures = 0
        self._latencies.append((self._now(), latency_s))
        self._prune()
        if self.state is not BreakerState.CLOSED:
            self._close()
        elif self._p99_exceeded():
            self._open()

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if (
            self.state is BreakerState.HALF_OPEN
            or self._consecutive_failures >= self.config.failure_threshold
        ):
            self._open()

    # -- internal -------------------------------------------------------

    def _open(self) -> None:
        self.state = BreakerState.OPEN
        self._opened_at = self._now()

    def _close(self) -> None:
        self.state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = None
        self._latencies.clear()

    def _prune(self) -> None:
        cutoff = self._now() - self.config.window_s
        while self._latencies and self._latencies[0][0] < cutoff:
            self._latencies.popleft()

    def _p99_exceeded(self) -> bool:
        if len(self._latencies) < self.config.min_samples_for_p99:
            return False
        ordered = sorted(v for _, v in self._latencies)
        p99 = ordered[min(len(ordered) - 1, int(0.99 * (len(ordered) - 1)))]
        return p99 > self.config.p99_threshold_s


@dataclass(frozen=True, slots=True)
class RouterPolicy:
    tenant: str = "default"
    provider_order: tuple[str, ...] = ()
    allow_cloud: bool = True
    keyterms: tuple[str, ...] = field(default_factory=tuple)


class _RoutedStream:
    """Wraps a live provider stream so mid-session faults trip the breaker
    and terminate the turn iterator cleanly instead of raising."""

    def __init__(
        self, inner: ASRStream, breaker: CircuitBreaker, time_fn: Callable[[], float]
    ) -> None:
        self._inner = inner
        self._breaker = breaker
        self._now = time_fn

    async def feed(self, pcm: bytes) -> None:
        try:
            await self._inner.feed(pcm)
        except Exception:  # noqa: BLE001 — capture path must never see this
            self._breaker.record_failure()

    async def turns(self) -> AsyncIterator[Turn]:
        try:
            async for turn in self._inner.turns():
                yield turn
        except Exception:  # noqa: BLE001 — a dropped socket ends turns, not the call
            self._breaker.record_failure()
            return

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self._inner.close()


class _DegradedStream:
    """Inert stream: accepts audio, yields no turns, closes cleanly.
    'Rules on signalling only' — the session stays alive with no ASR."""

    async def feed(self, pcm: bytes) -> None:
        return

    async def turns(self) -> AsyncIterator[Turn]:
        empty: list[Turn] = []
        for turn in empty:  # never iterates — an async generator that yields nothing
            yield turn

    async def close(self) -> None:
        return


class ASRRouter:
    name = "router"

    def __init__(
        self,
        providers: Sequence[ASRProvider],
        *,
        policy: RouterPolicy | None = None,
        fallback: ASRProvider | None = None,
        bus: EventBus | None = None,
        breaker_config: BreakerConfig | None = None,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._providers = {p.name: p for p in providers}
        self._policy = policy or RouterPolicy()
        self._fallback = fallback
        self._bus = bus
        self._now = time_fn
        self._breakers = {
            name: CircuitBreaker(name, breaker_config, time_fn=time_fn) for name in self._providers
        }

    @property
    def capabilities(self) -> ASRCapabilities:
        langs: tuple[str, ...] = ()
        for p in self._providers.values():
            langs += tuple(x for x in p.capabilities.languages if x not in langs)
        return ASRCapabilities(
            languages=langs,
            diarisation=all(p.capabilities.diarisation for p in self._providers.values()),
            keyterms=any(p.capabilities.keyterms for p in self._providers.values()),
            max_concurrency=None,
        )

    def breaker(self, provider_name: str) -> CircuitBreaker:
        return self._breakers[provider_name]

    def _chain(self) -> list[str]:
        names = [n for n in self._policy.provider_order if n in self._providers]
        names += [n for n in self._providers if n not in names]
        if not self._policy.allow_cloud:
            names = [n for n in names if n not in CLOUD_PROVIDERS]
        return names

    def _prime(self, spec: StreamSpec) -> StreamSpec:
        if self._policy.keyterms and not spec.keyterms:
            return dataclasses.replace(spec, keyterms=self._policy.keyterms)
        return spec

    async def _emit_degraded(self, spec: StreamSpec, reason: str) -> None:
        if self._bus is not None:
            await self._bus.publish(
                f"rf.{self._policy.tenant}.degraded",
                {"session_id": spec.session_id, "leg_id": spec.leg_id, "reason": reason},
            )

    async def open(self, spec: StreamSpec) -> ASRStream:
        spec = self._prime(spec)
        for name in self._chain():
            breaker = self._breakers[name]
            if not breaker.allow():
                continue
            started = self._now()
            try:
                inner = await self._providers[name].open(spec)
            except Exception:  # noqa: BLE001 — any open failure is a provider failure
                breaker.record_failure()
                continue
            breaker.record_success(self._now() - started)
            return _RoutedStream(inner, breaker, self._now)

        await self._emit_degraded(spec, reason="all ASR providers unavailable")
        if self._fallback is not None:
            with contextlib.suppress(Exception):
                return await self._fallback.open(spec)
        return _DegradedStream()
