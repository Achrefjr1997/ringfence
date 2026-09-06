"""Replay adapter (T-3.6).

    python -m packages.ingress.replay --fixture fx_tech_support_en_001 --speed 4
    python -m packages.ingress.replay --file corpus/audio/call.wav --fixture <id> --speed 4

Streams a fixture's transcript through the real ingress path — session
manager, pipeline, event bus — at real-time pace divided by ``speed``,
tagged ``Mode.REPLAY``.  The turns come from ``NullASR`` (so it stays
deterministic and offline); when ``--file`` is given its PCM is streamed
through the frame path too, but the decisions are identical to the direct
pipeline path either way.  That determinism is what makes evaluation and
the demo reproducible.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import Iterator
from pathlib import Path

from packages.asr.null import NullASR
from packages.contracts.audio import Frame, LegSpec, Mode, RoleHint, SessionDescriptor
from packages.contracts.events import EventBus
from packages.contracts.risk import Decision
from packages.eval.fixtures import fixture_turns, load_fixture
from packages.intervene.cases import CaseStore
from packages.pipeline.pipeline import Pipeline
from packages.policy.pack import PolicyPack

_RATE = 16_000
_FRAME = 640  # 40 ms
_FRAME_BYTES = _FRAME * 2
_MIN_SLEEP = 0.001  # below this the pace is not worth an event-loop timer


def _silent_frames(seconds: float) -> Iterator[bytes]:
    blank = b"\x00" * _FRAME_BYTES
    for _ in range(max(1, int(seconds * _RATE / _FRAME))):
        yield blank


def _wav_frames(path: Path) -> Iterator[bytes]:
    import soundfile as sf

    data, sr = sf.read(path, dtype="int16")
    if sr != _RATE or data.ndim != 1:
        raise ValueError(f"{path}: expected 16 kHz mono, got {sr} Hz, ndim {data.ndim}")
    raw = data.tobytes()
    for i in range(0, len(raw), _FRAME_BYTES):
        chunk = raw[i : i + _FRAME_BYTES]
        if len(chunk) == _FRAME_BYTES:
            yield chunk


async def replay(
    *,
    fixture_id: str,
    wav_path: str | Path | None = None,
    speed: float = 1.0,
    session_id: str | None = None,
    tenant_id: str = "replay",
    pack: PolicyPack | None = None,
    bus: EventBus | None = None,
    case_store: CaseStore | None = None,
    on_decision: object = None,
) -> list[Decision]:
    if speed <= 0:
        raise ValueError("speed must be positive")
    fx = load_fixture(fixture_id)
    sid = session_id or f"replay-{fixture_id}"
    script = fixture_turns(fx)

    pipe = Pipeline(NullASR(script, speed=speed), pack=pack, bus=bus, case_store=case_store)
    # Two legs named as fixture_turns names them, so role attribution recovers
    # each turn's role and the decisions match the direct path exactly.
    await pipe.start(
        SessionDescriptor(
            session_id=sid,
            tenant_id=tenant_id,
            mode=Mode.REPLAY,
            legs=(
                LegSpec(leg_id="far", role_hint=RoleHint.CALLER, sample_rate=_RATE),
                LegSpec(leg_id="near", role_hint=RoleHint.CALLEE, sample_rate=_RATE),
            ),
            started_at=time.time(),
            language=fx.language,
        )
    )

    total_s = max((t.t_end for t in script), default=1.0)
    frames = _wav_frames(Path(wav_path)) if wav_path else _silent_frames(total_s)
    period = (_FRAME / _RATE) / speed
    for i, chunk in enumerate(frames):
        await pipe.feed(
            Frame(
                session_id=sid,
                leg_id="far",
                pcm=chunk,
                sample_rate=_RATE,
                seq=i,
                captured_at=time.time(),
            )
        )
        if period >= _MIN_SLEEP:
            await asyncio.sleep(period)
        elif i % 64 == 0:
            await asyncio.sleep(0)  # let the turn loop run

    result = await pipe.end(sid)
    if callable(on_decision):
        for d in result.decisions:
            on_decision(d)
    return result.decisions


def _sidecar_fixture(wav: Path) -> str | None:
    for cand in (wav.with_suffix(".json"), wav.parent.parent / "labels" / f"{wav.stem}.json"):
        if cand.exists():
            return cand.stem
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m packages.ingress.replay")
    ap.add_argument("--file", type=Path, help="16 kHz mono wav to stream through the frame path")
    ap.add_argument("--fixture", help="fixture id supplying the transcript (turns)")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--session", default=None)
    args = ap.parse_args(argv)

    fixture_id = args.fixture or (_sidecar_fixture(args.file) if args.file else None)
    if not fixture_id:
        ap.error("--fixture is required (no <name>.json sidecar found next to --file)")

    def emit(d: Decision) -> None:
        print(
            json.dumps(
                {"t": d.t, "state": d.state, "score": round(d.score, 3), "cf": d.counterfactual}
            )
        )

    decisions = asyncio.run(
        replay(
            fixture_id=fixture_id,
            wav_path=args.file,
            speed=args.speed,
            session_id=args.session,
            on_decision=emit,
        )
    )
    print(f"# {len(decisions)} decisions, mode=REPLAY, speed={args.speed}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
