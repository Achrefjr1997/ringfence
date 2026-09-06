"""``NullASR`` — replays a stored transcript on its own timeline.

Fed audio is discarded.  The point is to make the entire detection suite
and every task after this one runnable with no network and no cost.  Turns
are emitted at their ``t_start`` (scaled by ``1 / speed``) relative to the
first iteration of :meth:`turns`, so a test can assert real timing.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from packages.asr.provider import ASRCapabilities, StreamSpec
from packages.contracts.transcript import Turn


class NullASRStream:
    def __init__(self, script: Sequence[Turn], *, speed: float = 1.0) -> None:
        if speed <= 0:
            raise ValueError(f"speed must be positive, got {speed}")
        self._script = tuple(sorted(script, key=lambda t: (t.t_start, t.turn_order)))
        self._speed = speed
        self._closed = asyncio.Event()
        self._fed_bytes = 0

    async def feed(self, pcm: bytes) -> None:
        # Audio is intentionally ignored; count it so a test can prove the
        # capture path stays live while replay runs.
        self._fed_bytes += len(pcm)

    async def turns(self) -> AsyncIterator[Turn]:
        """Yield each turn at ``t_start / speed`` relative to the first
        iteration.  ``close()`` stops the pacing and flushes the remaining
        turns immediately — a replayed call still gets fully transcribed
        (§4.3 "final turn flushed") rather than truncated mid-script."""
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        for turn in self._script:
            while not self._closed.is_set():
                remaining = (t0 + turn.t_start / self._speed) - loop.time()
                if remaining <= 0:
                    break
                try:
                    await asyncio.wait_for(self._closed.wait(), timeout=remaining)
                except (asyncio.TimeoutError, TimeoutError):
                    pass
            yield turn

    async def close(self) -> None:
        self._closed.set()

    @property
    def fed_bytes(self) -> int:
        return self._fed_bytes


class NullASR:
    """An :class:`~packages.asr.provider.ASRProvider` backed by a fixed script."""

    name = "null"
    capabilities = ASRCapabilities(
        languages=("en", "fr", "ar_tn"),
        diarisation=False,
        keyterms=False,
        max_concurrency=None,
    )

    def __init__(self, script: Sequence[Turn], *, speed: float = 1.0) -> None:
        self._script = tuple(script)
        self._speed = speed

    async def open(self, spec: StreamSpec) -> NullASRStream:
        return NullASRStream(self._script, speed=self._speed)
