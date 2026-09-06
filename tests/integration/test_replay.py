"""T-3.6 — replay adapter produces the same decisions as the direct path."""

import time
from pathlib import Path

import pytest

from packages.eval.fixtures import iter_fixtures
from packages.eval.harness import run_fixture
from packages.ingress.replay import main, replay

WAV = Path(__file__).resolve().parents[2] / "corpus" / "fixtures" / "audio" / "hello_16k.wav"
IDS = [fx.id for fx in iter_fixtures()]


def _trace(decisions: list) -> list[tuple]:
    return [(round(d.t, 6), d.state, round(d.score, 6), d.counterfactual) for d in decisions]


@pytest.mark.parametrize("fixture_id", IDS)
async def test_replay_matches_direct_pipeline(fixture_id: str) -> None:
    replayed = await replay(fixture_id=fixture_id, speed=400.0)
    direct = run_fixture(fixture_id)
    assert _trace(replayed) == _trace(direct.decisions)


async def test_replay_streams_wav_frames_and_still_matches() -> None:
    replayed = await replay(fixture_id="fx_tech_support_en_001", wav_path=WAV, speed=400.0)
    direct = run_fixture("fx_tech_support_en_001")
    assert _trace(replayed) == _trace(direct.decisions)
    assert any(d.state == "ALERT" for d in replayed)


async def test_speed_controls_wall_clock_pace() -> None:
    # speed 6 -> ~6.7 ms/frame, actually paced; speed 500 -> unpaced fast path
    t0 = time.perf_counter()
    await replay(fixture_id="fx_delivery_legit_en_001", speed=6.0)
    paced = time.perf_counter() - t0

    t0 = time.perf_counter()
    await replay(fixture_id="fx_delivery_legit_en_001", speed=500.0)
    fast = time.perf_counter() - t0

    assert paced > 0.5  # a ~13 s fixture at 6x really waits
    assert fast < paced / 3


def test_cli_requires_a_fixture(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as e:
        main(["--file", str(WAV)])
    assert e.value.code == 2  # argparse usage error
    assert "fixture is required" in capsys.readouterr().err


def test_cli_runs_and_prints_decisions(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--fixture", "fx_tech_support_en_001", "--speed", "500"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"state": "ALERT"' in out
    assert "mode=REPLAY" in out
