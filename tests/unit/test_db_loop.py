"""T-7.2a -- LoopThread: the sync<->async bridge under the Postgres stores."""

from __future__ import annotations

import threading

import pytest

from packages.db.loop import LoopThread


def test_runs_a_coroutine_on_its_own_thread_and_returns_the_result() -> None:
    loop = LoopThread()
    try:

        async def add(a: int, b: int) -> int:
            return a + b

        assert loop.run(add(2, 3)) == 5

        thread_names: list[str] = []

        async def where_am_i() -> None:
            thread_names.append(threading.current_thread().name)

        loop.run(where_am_i())
        assert thread_names == ["rf-db-loop"]
    finally:
        loop.close()


def test_propagates_exceptions() -> None:
    loop = LoopThread()
    try:

        async def boom() -> None:
            raise ValueError("nope")

        with pytest.raises(ValueError, match="nope"):
            loop.run(boom())
    finally:
        loop.close()


def test_close_is_idempotent_and_joins_the_thread() -> None:
    loop = LoopThread()
    loop.close()
    loop.close()  # must not raise
