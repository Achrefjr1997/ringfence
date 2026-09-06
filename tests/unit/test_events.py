import asyncio
import contextlib
from typing import Any, AsyncGenerator, Protocol, runtime_checkable

from packages.contracts.events import InProcessBus


async def test_protocol_typed_consumer_works_with_aclosing() -> None:
    """T-2.4+ consume EventBus, not InProcessBus.

    The Protocol's subscribe() must expose aclose-able AsyncGenerator,
    or aclosing() around a Protocol-typed consumer fails mypy exactly
    where the intended pattern uses it.
    """
    from packages.contracts.events import EventBus

    @runtime_checkable
    class _BusLike(Protocol):
        async def publish(self, subject: str, payload: dict[str, Any]) -> None: ...
        def subscribe(self, pattern: str) -> AsyncGenerator[tuple[str, dict[str, Any]], None]: ...

    bus: EventBus = InProcessBus(maxlen=10)
    await bus.publish("rf.tn.x.1", {"i": 1})
    got: list[tuple[str, dict[str, Any]]] = []
    async with contextlib.aclosing(bus.subscribe("rf.tn.*")) as gen:
        async for item in gen:
            got.append(item)
            break
    assert got == [("rf.tn.x.1", {"i": 1})]
    assert isinstance(bus, _BusLike)


async def test_bus_bounded_drop_oldest_no_subscriber() -> None:
    bus = InProcessBus(maxlen=1000)
    for i in range(1500):
        await bus.publish(f"rf.tenant.0.session.{i}", {"i": i})
    assert len(bus._q) == 1000
    assert bus._q[0][0] == 500  # oldest surviving sequence number
    assert bus._q[-1][1] == "rf.tenant.0.session.1499"


async def test_subscribe_filters_by_pattern() -> None:
    bus = InProcessBus(maxlen=100)
    await bus.publish("rf.tn.a.1", {"x": 1})
    await bus.publish("rf.fr.b.2", {"x": 2})
    got: list[tuple[str, dict[str, Any]]] = []
    async with contextlib.aclosing(bus.subscribe("rf.tn.*")) as gen:
        async for item in gen:
            got.append(item)
            break  # stream is infinite by design; drain one snapshot then exit
    assert got == [("rf.tn.a.1", {"x": 1})]


async def test_subscribe_still_works_after_buffer_wraps() -> None:
    bus = InProcessBus(maxlen=10)
    for i in range(25):  # well past maxlen
        await bus.publish(f"rf.x.{i}", {"i": i})

    async def take(pattern: str, n: int) -> list[tuple[str, dict[str, Any]]]:
        got: list[tuple[str, dict[str, Any]]] = []
        async with contextlib.aclosing(bus.subscribe(pattern)) as gen:
            async for item in gen:
                got.append(item)
                if len(got) == n:
                    return got
        return got

    got = await asyncio.wait_for(take("rf.*", 3), timeout=1.0)
    assert [s for s, _ in got] == ["rf.x.15", "rf.x.16", "rf.x.17"]

    # Proves the bus isn't permanently stuck: a publish long after the
    # buffer has wrapped must still reach a new subscriber.
    await bus.publish("rf.x.25", {"i": 25})
    got2 = await asyncio.wait_for(take("rf.x.25", 1), timeout=1.0)
    assert got2 == [("rf.x.25", {"i": 25})]
