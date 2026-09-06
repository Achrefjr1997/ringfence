from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from packages.media.vad import VoiceActivityDetector, speech_ratio

WAV = Path(__file__).resolve().parents[2] / "corpus" / "fixtures" / "audio" / "hello_16k.wav"
RATE = 16_000


_SUB = 480  # 30 ms @ 16 kHz


def _all_frames(vad: VoiceActivityDetector, pcm: np.ndarray) -> list:
    frames = vad.push(pcm.tobytes())
    tail = vad.flush()
    if tail is not None:
        frames.append(tail)
    return frames


def _reference_labels(sig: np.ndarray, threshold: float = 300.0) -> np.ndarray:
    """Per-30 ms frame: is there acoustic energy above the noise floor.
    This is what a human labelling this clip would mark."""
    n = len(sig) // _SUB
    rms = np.array(
        [np.sqrt(np.mean(sig[i * _SUB : (i + 1) * _SUB].astype(np.float64) ** 2)) for i in range(n)]
    )
    return rms > threshold


def test_agrees_with_reference_labels_at_least_90_percent() -> None:
    data, sr = sf.read(WAV, dtype="int16")
    assert sr == RATE and data.ndim == 1
    pad = np.zeros(RATE // 2, dtype=np.int16)
    sig = np.concatenate([pad, data, pad]).astype(np.int16)

    ref = _reference_labels(sig)
    guard = np.zeros(len(ref), dtype=bool)  # ±5 frames around each transition
    for i in range(1, len(ref)):
        if ref[i] != ref[i - 1]:
            guard[max(0, i - 5) : i + 5] = True

    got = np.array([f.speech for f in _all_frames(VoiceActivityDetector(aggressiveness=2), sig)])
    n = min(len(got), len(ref))
    scored = ~guard[:n]
    agreement = float(np.mean(got[:n][scored] == ref[:n][scored]))
    assert scored.sum() > 100
    assert agreement >= 0.90, f"{agreement:.3f}"


def test_pure_silence_is_never_speech() -> None:
    frames = _all_frames(VoiceActivityDetector(), np.zeros(RATE * 2, dtype=np.int16))
    assert frames and not any(f.speech for f in frames)
    assert speech_ratio(frames) == 0.0


def test_hangover_extends_speech_past_the_last_raw_detection() -> None:
    data, _ = sf.read(WAV, dtype="int16")
    # a bit of real speech, then 500 ms of silence
    sig = np.concatenate([data[RATE : RATE + RATE // 2], np.zeros(RATE // 2, dtype=np.int16)])
    frames = _all_frames(VoiceActivityDetector(aggressiveness=2, hangover_ms=200), sig)

    last_raw = max(i for i, f in enumerate(frames) if f.raw)
    hangover = [f for f in frames[last_raw + 1 :] if f.speech]
    # ~7 sub-frames of 30 ms after the last raw hit, none of them raw
    assert 5 <= len(hangover) <= 8
    assert all(not f.raw for f in hangover)
    assert not frames[-1].speech  # eventually drops


def test_subframe_is_30ms_and_timing_is_contiguous() -> None:
    vad = VoiceActivityDetector()
    assert vad.subframe_bytes == 480 * 2  # 30 ms @ 16 kHz, int16
    frames = vad.push(np.zeros(480 * 5, dtype=np.int16).tobytes())
    assert len(frames) == 5
    assert frames[0].t_start == 0.0
    assert frames[1].t_start == pytest.approx(0.03)
    assert frames[-1].t_end == pytest.approx(0.15)


def test_flush_emits_the_trailing_partial_once() -> None:
    vad = VoiceActivityDetector()
    vad.push(np.zeros(480 + 100, dtype=np.int16).tobytes())  # 1 full sub-frame + 100 leftover
    tail = vad.flush()
    assert tail is not None and tail.index == 1
    assert vad.flush() is None


def test_rejects_bad_sample_rate_and_aggressiveness() -> None:
    with pytest.raises(ValueError):
        VoiceActivityDetector(sample_rate=22_050)
    with pytest.raises(ValueError):
        VoiceActivityDetector(aggressiveness=5)
