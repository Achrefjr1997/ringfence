import asyncio

import pytest

from packages.contracts.audio import Frame, LegSpec, Mode, RoleHint, SessionDescriptor
from packages.contracts.events import InProcessBus
from packages.session.manager import (
    CloseReason,
    SessionError,
    SessionManager,
    SessionState,
)


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def tick(self, dt: float) -> None:
        self.t += dt


def _desc(session_id: str = "s1", tenant: str = "acme") -> SessionDescriptor:
    return SessionDescriptor(
        session_id=session_id,
        tenant_id=tenant,
        mode=Mode.CARRIER,
        legs=(
            LegSpec(leg_id="far", role_hint=RoleHint.CALLER, sample_rate=16000),
            LegSpec(leg_id="near", role_hint=RoleHint.CALLEE, sample_rate=16000),
        ),
        started_at=0.0,
    )


def _frame(session_id: str = "s1", leg: str = "far", n: int = 320) -> Frame:
    return Frame(
        session_id=session_id,
        leg_id=leg,
        pcm=b"\x00\x00" * n,
        sample_rate=16000,
        seq=0,
        captured_at=0.0,
    )


def _mgr(clock: FakeClock, **kw: object) -> SessionManager:
    return SessionManager(time_fn=clock.now, **kw)  # type: ignore[arg-type]


# --------------------------------------------------------------------------


async def test_admit_creates_admitted_session_with_legs() -> None:
    m = _mgr(FakeClock())
    s = await m.admit(_desc())
    assert s.state is SessionState.ADMITTED
    assert set(s.legs) == {"far", "near"}
    assert s.legs["far"].role_hint is RoleHint.CALLER


async def test_double_admit_raises() -> None:
    m = _mgr(FakeClock())
    await m.admit(_desc())
    with pytest.raises(SessionError):
        await m.admit(_desc())


async def test_unknown_session_raises() -> None:
    m = _mgr(FakeClock())
    with pytest.raises(SessionError):
        m.get("nope")
    with pytest.raises(SessionError):
        await m.on_hangup("nope")


async def test_first_frame_moves_admitted_to_streaming() -> None:
    clock = FakeClock()
    m = _mgr(clock)
    await m.admit(_desc())
    clock.tick(2.0)
    s = await m.on_frame(_frame())
    assert s.state is SessionState.STREAMING
    assert s.first_frame_at == 2.0
    assert s.last_media_at == 2.0
    assert s.legs["far"].frames == 1


async def test_frames_accumulate_counters() -> None:
    clock = FakeClock()
    m = _mgr(clock)
    await m.admit(_desc())
    await m.on_frame(_frame(leg="far", n=160))
    await m.on_frame(_frame(leg="near", n=160))
    await m.on_frame(_frame(leg="far", n=160))
    s = m.get("s1")
    assert s.legs["far"].frames == 2 and s.legs["far"].bytes_in == 640
    assert s.legs["near"].frames == 1


async def test_unknown_leg_frame_raises() -> None:
    m = _mgr(FakeClock())
    await m.admit(_desc())
    with pytest.raises(SessionError):
        await m.on_frame(_frame(leg="ghost"))


async def test_streaming_to_degraded_and_back() -> None:
    clock = FakeClock()
    m = _mgr(clock)
    await m.admit(_desc())
    await m.on_frame(_frame())
    clock.tick(1.0)
    s = await m.on_asr_down("s1")
    assert s.state is SessionState.DEGRADED and s.degraded_since == 1.0
    s = await m.on_asr_up("s1")
    assert s.state is SessionState.STREAMING and s.degraded_since is None


async def test_hangup_from_streaming_then_drained_to_closed() -> None:
    m = _mgr(FakeClock())
    await m.admit(_desc())
    await m.on_frame(_frame())
    s = await m.on_hangup("s1")
    assert s.state is SessionState.DRAINING
    s = await m.on_drained("s1")
    assert s.state is SessionState.CLOSED and s.close_reason is CloseReason.HANGUP


async def test_hangup_from_degraded() -> None:
    m = _mgr(FakeClock())
    await m.admit(_desc())
    await m.on_frame(_frame())
    await m.on_asr_down("s1")
    s = await m.on_hangup("s1")
    assert s.state is SessionState.DRAINING


async def test_admission_timeout_closes_after_10s_no_media() -> None:
    clock = FakeClock()
    m = _mgr(clock, admission_timeout_s=10.0)
    await m.admit(_desc())
    clock.tick(9.0)
    assert await m.poll() == []
    clock.tick(1.0)
    closed = await m.poll()
    assert [s.session_id for s in closed] == ["s1"]
    assert m.get("s1").state is SessionState.CLOSED
    assert m.get("s1").close_reason is CloseReason.ADMISSION_TIMEOUT


async def test_admission_timeout_not_triggered_if_frame_arrived() -> None:
    clock = FakeClock()
    m = _mgr(clock, admission_timeout_s=10.0)
    await m.admit(_desc())
    await m.on_frame(_frame())
    clock.tick(30.0)
    # streaming now; media timeout (8s) applies, not admission
    closed = await m.poll()
    assert closed[0].close_reason is CloseReason.MEDIA_TIMEOUT


async def test_media_timeout_closes_after_8s_silence() -> None:
    clock = FakeClock()
    m = _mgr(clock, media_timeout_s=8.0)
    await m.admit(_desc())
    await m.on_frame(_frame())
    clock.tick(7.0)
    assert await m.poll() == []
    clock.tick(1.0)
    closed = await m.poll()
    assert closed[0].close_reason is CloseReason.MEDIA_TIMEOUT


async def test_frame_resets_media_timeout() -> None:
    clock = FakeClock()
    m = _mgr(clock, media_timeout_s=8.0)
    await m.admit(_desc())
    await m.on_frame(_frame())
    clock.tick(5.0)
    await m.on_frame(_frame())  # last_media_at -> 5.0
    clock.tick(7.0)  # now 12.0, 7s since last frame
    assert await m.poll() == []
    clock.tick(1.5)  # 8.5s since last frame
    assert (await m.poll())[0].close_reason is CloseReason.MEDIA_TIMEOUT


async def test_poll_returns_only_newly_closed() -> None:
    clock = FakeClock()
    m = _mgr(clock, admission_timeout_s=10.0)
    await m.admit(_desc())
    clock.tick(11.0)
    assert len(await m.poll()) == 1
    assert await m.poll() == []  # already closed, not returned again


async def test_late_frame_after_hangup_is_ignored() -> None:
    m = _mgr(FakeClock())
    await m.admit(_desc())
    await m.on_frame(_frame())
    await m.on_hangup("s1")
    s = await m.on_frame(_frame())
    assert s.state is SessionState.DRAINING
    assert s.legs["far"].frames == 1  # not counted


@pytest.mark.parametrize(
    "setup, op",
    [
        ([], "on_asr_down"),  # from ADMITTED
        ([], "on_drained"),  # from ADMITTED
        (["frame"], "on_asr_up"),  # from STREAMING
        (["frame"], "on_drained"),  # from STREAMING
        (["frame", "hangup", "drained"], "on_hangup"),  # from CLOSED
    ],
)
async def test_illegal_transitions_raise(setup: list[str], op: str) -> None:
    m = _mgr(FakeClock())
    await m.admit(_desc())
    for step in setup:
        if step == "frame":
            await m.on_frame(_frame())
        elif step == "hangup":
            await m.on_hangup("s1")
        elif step == "drained":
            await m.on_drained("s1")
    with pytest.raises(SessionError):
        await getattr(m, op)("s1")


async def test_events_emitted_on_every_transition() -> None:
    bus = InProcessBus()
    clock = FakeClock()
    m = _mgr(clock, bus=bus, media_timeout_s=8.0)

    seen: list[str] = []

    async def collect() -> None:
        async for subject, _ in bus.subscribe("rf.acme.session.*"):
            seen.append(subject.split(".")[-1])
            if len(seen) == 5:
                return

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)

    await m.admit(_desc())
    await m.on_frame(_frame())
    await m.on_asr_down("s1")
    await m.on_asr_up("s1")
    clock.tick(20.0)
    await m.poll()

    await asyncio.wait_for(task, timeout=1.0)
    assert seen == ["admitted", "streaming", "degraded", "recovered", "closed"]
