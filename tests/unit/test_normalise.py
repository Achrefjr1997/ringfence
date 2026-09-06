import numpy as np
import pytest

from packages.media.normalise import (
    FRAME_SAMPLES,
    TARGET_RATE,
    AudioNormaliser,
    DCBlocker,
    Framer,
    SessionNormaliser,
    resample_to_16k,
    to_int16_mono,
)


def _sine(freq: float, rate: int, seconds: float, amp: float = 8000.0) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    return np.round(amp * np.sin(2 * np.pi * freq * t)).astype(np.int16)


def _snr_db(ref: np.ndarray, got: np.ndarray) -> float:
    n = min(len(ref), len(got))
    ref, got = ref[:n].astype(np.float64), got[:n].astype(np.float64)
    # align on the best integer lag (polyphase resampling adds group delay)
    corr = np.correlate(got, ref, mode="full")
    lag = int(np.argmax(corr)) - (n - 1)
    if lag > 0:
        got, ref = got[lag:], ref[: len(got[lag:])]
    elif lag < 0:
        ref, got = ref[-lag:], got[: len(ref[-lag:])]
    m = len(ref)
    lo, hi = m // 10, m - m // 10  # drop filter transients at the edges
    ref, got = ref[lo:hi], got[lo:hi]
    noise = got - ref
    return 10.0 * np.log10(np.sum(ref**2) / np.sum(noise**2))


@pytest.mark.parametrize("src_rate", [8_000, 44_100, 48_000])
def test_resample_to_16k_keeps_snr_above_40db(src_rate: int) -> None:
    src = _sine(1_000.0, src_rate, 1.0)
    ref = _sine(1_000.0, TARGET_RATE, 1.0)
    out = resample_to_16k(src, src_rate)
    assert abs(len(out) - TARGET_RATE) <= 2
    assert _snr_db(ref, out) > 40.0


def test_resample_16k_is_identity() -> None:
    x = _sine(500.0, TARGET_RATE, 0.1)
    assert np.array_equal(resample_to_16k(x, TARGET_RATE), x)


def test_frames_are_exactly_640_samples() -> None:
    fr = Framer()
    frames = fr.push(np.zeros(640 * 3 + 100, dtype=np.int16))
    assert len(frames) == 3
    assert all(f.shape == (FRAME_SAMPLES,) for f in frames)
    assert fr.pending == 100
    frames = fr.push(np.zeros(540, dtype=np.int16))  # 100 + 540 = 640
    assert len(frames) == 1 and fr.pending == 0


def test_dc_blocker_removes_offset() -> None:
    # 250 Hz -> 64 samples/period, so a whole number of periods has true mean 0
    x = (_sine(250.0, TARGET_RATE, 0.5).astype(np.float64) + 4000.0).astype(np.int16)
    y = DCBlocker().process(x)
    window = y[3200 : 3200 + 64 * 70]  # settled, exactly 70 periods
    assert abs(np.mean(x)) > 3000
    assert abs(np.mean(window)) < 1.0  # DC offset gone


def test_dc_blocker_is_seamless_across_chunks() -> None:
    x = (_sine(300.0, TARGET_RATE, 0.4).astype(np.float64) + 4000.0).astype(np.int16)
    whole = DCBlocker().process(x)
    b = DCBlocker()
    chunked = np.concatenate([b.process(x[:1234]), b.process(x[1234:])])
    assert np.allclose(whole, chunked, atol=1e-6)


def test_session_normaliser_uses_one_gain_for_both_legs() -> None:
    sn = SessionNormaliser()
    loud = _sine(200.0, TARGET_RATE, 0.5, amp=20000.0)  # caller leg
    quiet = _sine(200.0, TARGET_RATE, 0.5, amp=2000.0)  # callee leg
    sn.observe(loud)
    sn.observe(quiet)
    g = sn.gain
    # same gain object applied to each leg -> relative level preserved
    out_loud = sn.apply(loud).astype(np.float64)
    out_quiet = sn.apply(quiet).astype(np.float64)
    ratio_in = np.sqrt(np.mean(loud.astype(np.float64) ** 2)) / np.sqrt(
        np.mean(quiet.astype(np.float64) ** 2)
    )
    ratio_out = np.sqrt(np.mean(out_loud**2)) / np.sqrt(np.mean(out_quiet**2))
    assert g != 1.0
    assert ratio_out == pytest.approx(ratio_in, rel=0.02)


def test_to_int16_mono_downmixes_stereo() -> None:
    stereo = np.array([[100, 300], [200, 400]], dtype=np.int16).reshape(-1)
    mono = to_int16_mono(stereo.tobytes(), channels=2)
    assert list(mono) == [200, 300]


def test_audio_normaliser_end_to_end_emits_640_sample_frames() -> None:
    an = AudioNormaliser(src_rate=8_000)
    # 8 kHz -> 16 kHz roughly doubles the sample count
    frames = an.process(_sine(440.0, 8_000, 1.0).tobytes())
    assert frames  # produced something
    assert all(len(f) == FRAME_SAMPLES * 2 for f in frames)  # 640 int16 = 1280 bytes
    total = sum(len(f) // 2 for f in frames) + an._framer.pending
    assert abs(total - TARGET_RATE) <= 4
