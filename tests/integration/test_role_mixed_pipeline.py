"""T-3.5 / T-6.3 -- acoustic role attribution wired into the live Pipeline.

The MVP gateway feeds one mixed speakerphone stream. Before this, the
pipeline pinned every turn to a single hinted role, so either the victim's
speech inflated the score (leg=far) or nothing scored at all (leg=near).
Here the pipeline runs the AcousticRoleClassifier over the PCM and
attributes each turn on its own.
"""

from __future__ import annotations

import asyncio

from packages.asr.null import NullASR
from packages.contracts.audio import Frame, LegSpec, Mode, RoleHint, SessionDescriptor
from packages.eval.fixtures import fixture_turns, load_fixture
from packages.eval.role_eval import synth_mixed_audio
from packages.pipeline.pipeline import Pipeline

_RATE = 16_000
_FRAME = 320  # 20 ms


def _mixed_desc(sid: str) -> SessionDescriptor:
    return SessionDescriptor(
        session_id=sid,
        tenant_id="t",
        mode=Mode.SDK,
        legs=(LegSpec(leg_id="mixed", role_hint=RoleHint.MIXED, sample_rate=_RATE),),
        started_at=0.0,
        language="en",
    )


def _two_leg_desc(sid: str) -> SessionDescriptor:
    return SessionDescriptor(
        session_id=sid,
        tenant_id="t",
        mode=Mode.SDK,
        legs=(
            LegSpec(leg_id="far", role_hint=RoleHint.CALLER, sample_rate=_RATE),
            LegSpec(leg_id="near", role_hint=RoleHint.CALLEE, sample_rate=_RATE),
        ),
        started_at=0.0,
        language="en",
    )


async def _run_mixed(fixture_id: str, sid: str) -> Pipeline:
    fx = load_fixture(fixture_id)
    audio = synth_mixed_audio(fx, seed=0).tobytes()

    # speed=1 so NullASR does not flush turns before the audio is in; feed
    # every frame first (no await into the loop), then end() drains _run
    # against a fully-populated classifier.
    pipe = Pipeline(NullASR(fixture_turns(fx), speed=1.0))
    await pipe.start(_mixed_desc(sid))
    for i in range(0, len(audio), _FRAME * 2):
        await pipe.feed(
            Frame(
                session_id=sid,
                leg_id="mixed",
                pcm=audio[i : i + _FRAME * 2],
                sample_rate=_RATE,
                seq=i,
                captured_at=0.0,
            )
        )
    await pipe.end(sid)
    return pipe


def test_mixed_stream_attributes_turns_acoustically() -> None:
    async def go() -> None:
        fx = load_fixture("fx_gift_card_en_001")
        pipe = await _run_mixed("fx_gift_card_en_001", "m1")

        # pipeline chose the acoustic path, not a pinned role
        assert pipe._acoustic is not None and pipe._acoustic.calibrated

        got = [role for role, _text, _t in pipe._transcript]
        assert len(got) == len(fx.turns)
        assert len(set(got)) > 1, "every turn got the same role -- classifier not wired"

        true = [t.role for t in fx.turns]
        # invariant #1's spirit: never label the victim as the caller
        for g, tr in zip(got, true, strict=True):
            assert not (tr == "CALLEE" and g == "CALLER"), "victim misattributed as caller"

        confident = [(g, tr) for g, tr in zip(got, true, strict=True) if g != "UNKNOWN"]
        assert len(confident) >= len(true) // 2
        acc = sum(g == tr for g, tr in confident) / len(confident)
        assert acc >= 0.85  # matches the role_eval bar

    asyncio.run(go())


def test_the_scam_still_escalates_through_the_mixed_path() -> None:
    async def go() -> None:
        pipe = await _run_mixed("fx_gift_card_en_001", "m2")
        assert pipe._peak_state in ("ALERT", "INTERVENE")

    asyncio.run(go())


def test_a_benign_call_stays_calm_through_the_mixed_path() -> None:
    async def go() -> None:
        pipe = await _run_mixed("fx_real_bank_frauddesk_fr_001", "m3")
        assert pipe._peak_state == "CALM"

    asyncio.run(go())


def test_a_hinted_single_leg_does_not_use_the_acoustic_path() -> None:
    async def go() -> None:
        pipe = Pipeline(NullASR([], speed=1000.0))
        await pipe.start(
            SessionDescriptor(
                session_id="s",
                tenant_id="t",
                mode=Mode.SDK,
                legs=(LegSpec(leg_id="far", role_hint=RoleHint.CALLER, sample_rate=_RATE),),
                started_at=0.0,
                language="en",
            )
        )
        assert pipe._acoustic is None and pipe._sole_role == "CALLER"
        await pipe.end("s")

    asyncio.run(go())


def test_two_leg_path_is_unchanged() -> None:
    async def go() -> None:
        fx = load_fixture("fx_gift_card_en_001")
        from packages.eval.fixtures import fixture_turns

        pipe = Pipeline(NullASR(fixture_turns(fx), speed=1.0))
        await pipe.start(_two_leg_desc("s2"))
        assert pipe._acoustic is None
        for i in range(400):
            await pipe.feed(
                Frame(
                    session_id="s2",
                    leg_id="far",
                    pcm=b"\x00\x00" * _FRAME,
                    sample_rate=_RATE,
                    seq=i,
                    captured_at=0.0,
                )
            )
        await pipe.end("s2")
        roles = {r for r, _, _ in pipe._transcript}
        assert roles == {"CALLER", "CALLEE"}  # exact by construction

    asyncio.run(go())
