"""A private asyncio loop on a daemon thread (T-7.2a).

``asyncpg`` needs a running event loop and a pool that is only ever touched
from the loop that created it.  The gateway, though, calls the identity
store from plain synchronous code (``admit()``, the auth handlers) and must
keep doing so -- ``docs`` and ``store.py`` both promise the interface does
not change when Postgres lands.

:class:`LoopThread` bridges the two: one background loop, owned start to
finish by this object, with :meth:`run` marshalling a coroutine onto it and
blocking for the result.  Identity lookups happen once per call-setup or
per auth request -- never per audio frame -- so the round-trip cost sits
well inside the budget.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import TypeVar

_T = TypeVar("_T")

_DEFAULT_TIMEOUT_S = 10.0


class LoopThread:
    """Own an event loop running on a daemon thread."""

    def __init__(self, *, name: str = "rf-identity-loop") -> None:
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._serve, name=name, daemon=True)
        self._thread.start()
        self._ready.wait()

    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.call_soon(self._ready.set)
        self._loop.run_forever()
        # run_forever has returned: drain and close so no "pending task" noise.
        self._loop.close()

    def run(
        self, coro: Coroutine[object, object, _T], *, timeout_s: float = _DEFAULT_TIMEOUT_S
    ) -> _T:
        """Schedule ``coro`` on the loop and block until it returns or raises."""
        if not self._thread.is_alive():  # pragma: no cover - defensive
            raise RuntimeError("identity loop thread is not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout_s)

    def close(self) -> None:
        """Stop the loop and join the thread.  Safe to call more than once."""
        if not self._thread.is_alive():
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=_DEFAULT_TIMEOUT_S)
