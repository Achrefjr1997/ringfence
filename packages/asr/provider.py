"""The ASR provider interface (production §5.1).

Small on purpose: air-gapped carriers, cost control at high concurrency,
and surviving a provider incident all demand that nothing downstream binds
to a concrete provider.  Everything past this boundary sees only
``contracts.transcript.Turn`` — never a provider-shaped object.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from packages.contracts.transcript import Turn


@dataclass(frozen=True, slots=True)
class ASRCapabilities:
    languages: tuple[str, ...]
    diarisation: bool = False
    keyterms: bool = False
    max_concurrency: int | None = None


@dataclass(frozen=True, slots=True)
class StreamSpec:
    """What a caller must state to open a stream.

    ``language=None`` asks the provider to auto-detect and pin (§5.4).
    """

    session_id: str
    leg_id: str
    sample_rate: int = 16_000
    language: str | None = None
    format_turns: bool = True
    keyterms: tuple[str, ...] = field(default_factory=tuple)


@runtime_checkable
class ASRStream(Protocol):
    async def feed(self, pcm: bytes) -> None: ...

    def turns(self) -> AsyncIterator[Turn]: ...

    async def close(self) -> None: ...


@runtime_checkable
class ASRProvider(Protocol):
    name: str

    @property
    def capabilities(self) -> ASRCapabilities:  # read-only: a class attribute satisfies it
        ...

    async def open(self, spec: StreamSpec) -> ASRStream: ...
