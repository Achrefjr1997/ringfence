import asyncio
import fnmatch
from collections import deque
from typing import Any, AsyncIterator, Protocol


class EventBus(Protocol):
    async def publish(self, subject: str, payload: dict[str, Any]) -> None: ...
    def subscribe(self, pattern: str) -> AsyncIterator[tuple[str, dict[str, Any]]]: ...


class InProcessBus(EventBus):
    """Single shared bounded buffer, drop-oldest. Placeholder for NATS JetStream.

    Each item gets a monotonic sequence number, independent of len(self._q) —
    the deque's length caps at maxlen and stays there once full, so it can't
    be used as a subscriber's read cursor without the cursor getting stuck.
    A subscriber sees the current backlog (whatever survives eviction) and
    then live events. Falling more than maxlen events behind silently drops
    the events evicted in between — intended, documented, not hardened
    against; this class is removed once NATS JetStream lands.
    """

    def __init__(self, maxlen: int = 1000) -> None:
        self._q: deque[tuple[int, str, dict[str, Any]]] = deque(maxlen=maxlen)
        self._next_seq = 0
        self._new = asyncio.Condition()

    async def publish(self, subject: str, payload: dict[str, Any]) -> None:
        async with self._new:
            self._q.append((self._next_seq, subject, payload))
            self._next_seq += 1
            self._new.notify_all()

    async def subscribe(self, pattern: str) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        seen = -1
        while True:
            async with self._new:
                while not self._q or self._q[-1][0] <= seen:
                    await self._new.wait()
                new_items = [item for item in self._q if item[0] > seen]
                seen = new_items[-1][0]
            for _, subject, payload in new_items:
                if fnmatch.fnmatch(subject, pattern):
                    yield subject, payload
