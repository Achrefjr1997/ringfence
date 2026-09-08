"""T-5.3 -- the gateway runs ASR behind a circuit breaker.

A flapping provider must not drop calls: the session degrades to no
transcript ("rules on signalling only") and the breaker state shows up on
/metrics.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from starlette.testclient import TestClient

from apps.gateway.app import create_app
from packages.asr.provider import ASRCapabilities, StreamSpec
from packages.contracts.transcript import Turn

SECRET = "test-session-secret"
_CAPS = ASRCapabilities(languages=("en",), diarisation=False, keyterms=False)


class _HealthyProvider:
    name = "assemblyai"
    capabilities = _CAPS

    async def open(self, spec: StreamSpec) -> "_Stream":
        return _Stream()


class _FailingProvider:
    name = "assemblyai"
    capabilities = _CAPS

    async def open(self, spec: StreamSpec) -> "_Stream":
        raise RuntimeError("provider down")


class _Stream:
    async def feed(self, pcm: bytes) -> None: ...

    async def turns(self) -> AsyncIterator[Turn]:
        return
        yield  # pragma: no cover - makes this an async generator

    async def close(self) -> None: ...


def _client(provider: object) -> TestClient:
    return TestClient(
        create_app(asr=provider, session_secret=SECRET, dev_mode=True)  # type: ignore[arg-type]
    )


def _one_session(c: TestClient, n: int) -> None:
    with c.websocket_connect(f"/ws/capture?session=s{n}&leg=far&tenant=testco") as ws:
        ws.send_bytes(b"\x00\x00" * 320)


def test_flapping_provider_trips_the_breaker_but_calls_survive() -> None:
    c = _client(_FailingProvider())

    for i in range(5):  # breaker opens after 5 consecutive open() failures
        _one_session(c, i)

    health = c.get("/health").json()
    assert health["metrics"]["admitted"] == 5  # every call was admitted (degraded)

    body = c.get("/metrics").text
    assert 'ringfence_asr_breaker_state{provider="assemblyai",state="open"} 1' in body
    assert 'ringfence_asr_breaker_state{provider="assemblyai",state="closed"} 0' in body


def test_healthy_provider_keeps_the_breaker_closed() -> None:
    c = _client(_HealthyProvider())
    _one_session(c, 0)
    body = c.get("/metrics").text
    assert 'ringfence_asr_breaker_state{provider="assemblyai",state="closed"} 1' in body


def test_a_test_provided_factory_still_bypasses_the_router() -> None:
    # provider_factory keeps the direct path -- no breaker series at all
    from packages.asr.null import NullASR

    app = create_app(
        provider_factory=lambda spec: NullASR([]), session_secret=SECRET, dev_mode=True
    )
    with TestClient(app) as c:
        assert "ringfence_asr_breaker_state" not in c.get("/metrics").text
