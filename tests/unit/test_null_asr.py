import asyncio

from packages.asr.null import NullASR
from packages.asr.provider import ASRStream, StreamSpec
from packages.contracts.transcript import Turn
from packages.eval.fixtures import fixture_turns, load_fixture

SCRIPT = fixture_turns(load_fixture("fx_callee_repeats_terms_en_001"))
SPEC = StreamSpec(session_id="s1", leg_id="far", language="en")


async def _drain(stream: ASRStream) -> list[tuple[Turn, float]]:
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    out: list[tuple[Turn, float]] = []
    async for turn in stream.turns():
        out.append((turn, loop.time() - t0))
    return out


async def test_open_returns_an_asr_stream() -> None:
    stream = await NullASR(SCRIPT, speed=1000.0).open(SPEC)
    assert isinstance(stream, ASRStream)


async def test_turns_arrive_in_order() -> None:
    stream = await NullASR(SCRIPT, speed=1000.0).open(SPEC)
    seen = [turn for turn, _ in await _drain(stream)]
    assert [t.turn_order for t in seen] == list(range(len(SCRIPT)))
    assert [t.text for t in seen] == [t.text for t in SCRIPT]


async def test_timing_within_50ms_of_the_fixture() -> None:
    speed = 20.0
    stream = await NullASR(SCRIPT, speed=speed).open(SPEC)
    for turn, dt in await _drain(stream):
        expected = turn.t_start / speed
        assert abs(dt - expected) < 0.05, f"turn {turn.turn_order}: {dt:.3f}s vs {expected:.3f}s"


async def test_close_flushes_remaining_turns_then_terminates() -> None:
    stream = await NullASR(SCRIPT, speed=1.0).open(SPEC)  # slow timeline
    it = stream.turns()
    first = await it.__anext__()
    assert first.turn_order == 0

    await stream.close()

    # close() stops the pacing: the rest arrive at once, the iterator ends,
    # and it must not hang.
    rest = await asyncio.wait_for(_collect(it), timeout=1.0)
    assert [t.turn_order for t in rest] == list(range(1, len(SCRIPT)))


async def test_feed_is_ignored_but_counted() -> None:
    stream = await NullASR(SCRIPT, speed=1000.0).open(SPEC)
    await stream.feed(b"\x00\x00" * 320)
    await stream.feed(b"\x01\x00" * 160)
    assert stream.fed_bytes == 960
    seen = await _drain(stream)
    assert len(seen) == len(SCRIPT)  # replay is unaffected by fed audio


async def _collect(it: object) -> list[Turn]:
    return [turn async for turn in it]  # type: ignore[union-attr]
