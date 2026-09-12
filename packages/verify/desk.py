"""The verification desk: who picks up when the agent calls.

There is no telephony anywhere in this feature. The "institution's fraud desk"
is a browser page we serve (``/verify-desk``), and a verification reaches it
through two small pieces:

* :class:`DeskExchange` -- offers a conversation to whichever desk page is
  watching, as a **single-use ticket** with a short lifetime. Redeeming the
  ticket is the only way to obtain the audio line; a ticket is popped the
  moment anyone presents it, valid or not, so it cannot be replayed.
* :class:`DeskLine` -- the audio line itself: two bounded queues, desk->agent
  and agent->desk, plus an out-of-band hang-up.

An offer carries the institution's display name and nothing else about the
call. The desk never learns the protected person's session, tenant, or words.

Pure asyncio; the Starlette WebSocket that drives the desk side lives in the
gateway, so this module is testable without one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import time
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from typing import Literal, TypeVar

from packages.verify.directory import Institution

TICKET_TTL_S = 30.0  # how long the desk may ring before it counts as unanswered
_MAX_PENDING = 8
_SUBSCRIBER_QUEUE = 32
# Desk microphone -> agent. ~3 s of 50 ms frames: if the agent side stalls,
# the oldest speech is dropped and the latest kept.
_UP_QUEUE = 64
# Agent -> desk. Agent audio arrives in bursts faster than real time, so this
# side applies backpressure instead of dropping (a gap in the agent's own voice
# is worse than a delay). Still bounded -- invariant #6.
_DOWN_QUEUE = 512

_T = TypeVar("_T")


def _put_latest(q: asyncio.Queue[_T], item: _T) -> None:
    """Enqueue, evicting the oldest item if full. Never blocks."""
    while True:
        try:
            q.put_nowait(item)
            return
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                q.get_nowait()


class DeskLine:
    def __init__(self) -> None:
        self._up: asyncio.Queue[bytes | None] = asyncio.Queue(_UP_QUEUE)
        self._down: asyncio.Queue[bytes | str | None] = asyncio.Queue(_DOWN_QUEUE)
        self._closed = False  # the agent side is finished
        self._hung_up = False  # the desk side is gone

    # -- agent side (packages/verify/agent.py::DeskAudio) -----------------

    async def receive_audio(self) -> bytes | None:
        """Next PCM16 frame from the desk; ``None`` once the desk hangs up."""
        return await self._up.get()

    async def send_audio(self, pcm16: bytes) -> None:
        if not (self._closed or self._hung_up):
            await self._down.put(pcm16)

    async def send_event(self, event: Mapping[str, str]) -> None:
        if not (self._closed or self._hung_up):
            await self._down.put(json.dumps(dict(event)))

    def close(self, final_event: Mapping[str, str] | None = None) -> None:
        """The conversation is over. Queued agent audio still plays out first."""
        if self._closed:
            return
        self._closed = True
        if self._hung_up:
            return
        if final_event is not None:
            _put_latest(self._down, json.dumps(dict(final_event)))
        _put_latest(self._down, None)

    # -- desk side (apps/gateway WebSocket) -------------------------------

    def push_audio(self, pcm16: bytes) -> None:
        if not self._hung_up and pcm16:
            _put_latest(self._up, pcm16)

    def hang_up(self) -> None:
        if self._hung_up:
            return
        self._hung_up = True
        _put_latest(self._up, None)
        # Unblock an agent-side put waiting on a desk that will never drain.
        while not self._down.empty():
            self._down.get_nowait()

    async def next_outbound(self) -> bytes | str | None:
        """Agent audio (bytes), a JSON event (str), or ``None``: close now."""
        return await self._down.get()


@dataclass(frozen=True, slots=True)
class Offer:
    ticket: str
    desk_id: str
    institution_display: str
    line_label: str
    expires_at: float

    def public(self) -> dict[str, str]:
        """What the desk page is shown. No session, no tenant, no transcript."""
        return {
            "ticket": self.ticket,
            "desk_id": self.desk_id,
            "institution": self.institution_display,
            "line_label": self.line_label,
        }


DeskNotice = tuple[Literal["offer", "withdrawn"], Offer]


class DeskExchange:
    def __init__(
        self,
        *,
        ttl_s: float = TICKET_TTL_S,
        max_pending: int = _MAX_PENDING,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_s = ttl_s
        self._max_pending = max_pending
        self._now = now
        self._pending: dict[str, tuple[Offer, asyncio.Future[DeskLine]]] = {}
        self._subscribers: set[asyncio.Queue[DeskNotice]] = set()

    def offer(self, institution: Institution) -> Offer | None:
        """Ring the desk. ``None`` if too many offers are already waiting."""
        self._expire()
        if len(self._pending) >= self._max_pending:
            return None
        offer = Offer(
            ticket=secrets.token_urlsafe(24),
            desk_id=institution.desk_id,
            institution_display=institution.display_name,
            line_label=institution.line_label,
            expires_at=self._now() + self._ttl_s,
        )
        future: asyncio.Future[DeskLine] = asyncio.get_running_loop().create_future()
        self._pending[offer.ticket] = (offer, future)
        self._notify("offer", offer)
        return offer

    def redeem(self, ticket: str) -> DeskLine | None:
        """The desk answered. Single-use: the ticket is consumed even when it
        turns out to be expired, so a second presentation always fails."""
        entry = self._pending.pop(ticket, None)
        if entry is None:
            return None
        offer, future = entry
        self._notify("withdrawn", offer)  # any other desk tab stops ringing
        if future.done() or self._now() >= offer.expires_at:
            if not future.done():
                future.cancel()
            return None
        line = DeskLine()
        future.set_result(line)
        return line

    async def wait_answer(self, offer: Offer) -> DeskLine | None:
        """The line once the desk answers; ``None`` if it rang out."""
        entry = self._pending.get(offer.ticket)
        if entry is None:
            return None
        future = entry[1]
        try:
            remaining = max(0.0, offer.expires_at - self._now())
            return await asyncio.wait_for(asyncio.shield(future), remaining)
        except TimeoutError:
            if future.done() and not future.cancelled():
                return future.result()  # answered on the last tick
            return None
        finally:
            if not future.done():
                self.withdraw(offer.ticket)

    def withdraw(self, ticket: str) -> None:
        entry = self._pending.pop(ticket, None)
        if entry is None:
            return
        offer, future = entry
        if not future.done():
            future.cancel()
        self._notify("withdrawn", offer)

    def pending_offers(self) -> list[Offer]:
        self._expire()
        return [offer for offer, _ in self._pending.values()]

    async def notices(self) -> AsyncIterator[DeskNotice]:
        """Offers as they ring and stop ringing, starting with those already
        waiting -- a desk page opened mid-ring still sees the call."""
        queue: asyncio.Queue[DeskNotice] = asyncio.Queue(_SUBSCRIBER_QUEUE)
        self._subscribers.add(queue)
        try:
            for offer in self.pending_offers():
                yield ("offer", offer)
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    def _notify(self, kind: Literal["offer", "withdrawn"], offer: Offer) -> None:
        for queue in self._subscribers:
            _put_latest(queue, (kind, offer))

    def _expire(self) -> None:
        now = self._now()
        for ticket, (offer, _) in list(self._pending.items()):
            if now >= offer.expires_at:
                self.withdraw(ticket)
