import numpy as np
from scipy.signal import butter, sosfiltfilt

from packages.media.acoustic_role import AcousticRoleClassifier, RoleGuess, hf_ratio

RATE = 16_000
rng = np.random.default_rng(7)


def _voice(seconds: float, *, bandlimited: bool) -> np.ndarray:
    """A speech-ish signal: a stack of partials 150–7800 Hz.  ``bandlimited``
    low-passes it at 3.4 kHz like a telephone codec would."""
    n = int(RATE * seconds)
    t = np.arange(n) / RATE
    sig = np.zeros(n)
    for f in range(150, 7900, 130):
        sig += (1.0 / (1 + f / 400)) * np.sin(2 * np.pi * f * t + rng.uniform(0, 2 * np.pi))
    sig += 0.05 * rng.standard_normal(n)
    if bandlimited:
        sos = butter(8, 3400, btype="low", fs=RATE, output="sos")
        sig = sosfiltfilt(sos, sig)
    sig *= 8000.0 / np.sqrt(np.mean(sig**2))
    return sig.astype(np.int16).astype(np.float64)


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(RATE * seconds))


def test_hf_ratio_orders_bandlimited_below_fullband() -> None:
    full = _voice(0.5, bandlimited=False)
    tele = _voice(0.5, bandlimited=True)
    assert hf_ratio(tele, RATE) < hf_ratio(full, RATE) * 0.5
    assert hf_ratio(_silence(0.5), RATE) is None


def _mixed_stream() -> tuple[np.ndarray, list[tuple[float, float, str]]]:
    """Alternating 1.4 s turns, full-band (=> CALLEE) and telephone-band
    (=> CALLER), with 0.25 s gaps.  Returns (pcm, [(t0, t1, role)])."""
    chunks: list[np.ndarray] = [_silence(0.3)]
    turns: list[tuple[float, float, str]] = []
    t = 0.3
    for i in range(12):
        far = i % 2 == 0
        seg = _voice(1.4, bandlimited=far)
        turns.append((t, t + 1.4, "CALLER" if far else "CALLEE"))
        chunks.append(seg)
        chunks.append(_silence(0.25))
        t += 1.4 + 0.25
    return np.concatenate(chunks), turns


def test_per_turn_accuracy_at_least_85_percent() -> None:
    pcm, turns = _mixed_stream()
    clf = AcousticRoleClassifier()
    assert not clf.calibrated
    clf.observe(pcm)
    assert clf.calibrated

    guessed = [clf.classify(t0 + 0.3, t1 - 0.3) for (t0, t1, _) in turns]
    confident = [
        (g, exp) for g, (_, _, exp) in zip(guessed, turns, strict=True) if g.role != "UNKNOWN"
    ]

    assert len(confident) >= int(0.75 * len(turns)), "too many turns went UNKNOWN"
    correct = sum(g.role == exp for g, exp in confident)
    assert correct / len(confident) >= 0.85, f"{correct}/{len(confident)}"


def test_ambiguous_turn_is_unknown_not_guessed() -> None:
    pcm, _ = _mixed_stream()
    clf = AcousticRoleClassifier()
    clf.observe(pcm)
    low, high = clf.centroids  # type: ignore[misc]
    midpoint = (low + high) / 2

    # mix the two voices so the window's hf_ratio lands on the cluster midpoint
    full, tele = _voice(1.4, bandlimited=False), _voice(1.4, bandlimited=True)
    alpha = min(
        np.linspace(0.02, 0.8, 60),
        key=lambda a: abs(np.log(hf_ratio(a * full + tele, RATE)) - midpoint),  # type: ignore[arg-type]
    )
    blend = alpha * full + tele

    tail = np.concatenate([pcm, _silence(0.25), blend])
    clf2 = AcousticRoleClassifier()
    clf2.observe(tail)
    t0 = len(pcm) / RATE + 0.25
    g = clf2.classify(t0 + 0.3, t0 + 1.1)
    assert g.role == "UNKNOWN"
    assert g.confidence < 0.4


def test_uncalibrated_classifier_returns_unknown() -> None:
    clf = AcousticRoleClassifier()
    clf.observe(_voice(2.0, bandlimited=False))  # < 8 s calibration
    assert not clf.calibrated
    assert clf.classify(0.5, 1.5) == RoleGuess("UNKNOWN", 0.0)
