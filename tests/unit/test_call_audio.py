"""P7 -- audio mixing + Opus encode."""

from __future__ import annotations

import numpy as np

from packages.calls import audio


def _pcm(values: list[int]) -> bytes:
    return np.array(values, dtype="<i2").tobytes()


def test_opus_is_available_in_this_env() -> None:
    # soundfile is a hard project dependency; its bundled libsndfile has Opus
    assert audio.opus_available() is True


def test_mix_legs_sums_pads_and_clips() -> None:
    a = _pcm([100, 200, 300])
    b = _pcm([50, 60])
    mixed = np.frombuffer(audio.mix_legs([a, b]), dtype="<i2")
    assert list(mixed) == [150, 260, 300]

    hot = _pcm([30000, -30000])
    clipped = np.frombuffer(audio.mix_legs([hot, hot]), dtype="<i2")
    assert list(clipped) == [32767, -32768]

    assert audio.mix_legs([]) == b""
    assert audio.mix_legs([b"", b""]) == b""


def test_encode_opus_round_trips() -> None:
    import io

    import soundfile as sf

    pcm = (np.sin(np.linspace(0, 40, 16000)) * 8000).astype("<i2").tobytes()
    blob, ct = audio.encode_opus(pcm, sample_rate=16000)
    assert ct == "audio/ogg"
    assert 0 < len(blob) < len(pcm)  # compressed
    back, sr = sf.read(io.BytesIO(blob))
    assert sr == 16000 and back.shape[0] > 15000
