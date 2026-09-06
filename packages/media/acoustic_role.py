"""Acoustic role attribution for mixed single streams (T-3.5, §6.3).

Physical fact: the far-end voice has been through a telephone codec and
carries almost no energy above 4 kHz; the near-end voice was picked up
directly in the room and does.

    hf_ratio = Σ|X(f)|, f ∈ [4000, 8000)  /  Σ|X(f)|, f ∈ [300, 3400)

Calibrate over the first ~8 s, split the observed ratios into two clusters,
classify each turn by its median (log) ratio, and take a confidence from
the log-margin.  Below ``min_confidence`` the turn is ``UNKNOWN`` — the
combination rules that need ``CALLER`` then do not fire and tier-1 signals
count at half weight.  The system degrades toward silence, never toward
false alarms.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from packages.contracts.transcript import Role

F64 = npt.NDArray[np.float64]

_LOW_BAND = (300.0, 3400.0)
_HIGH_BAND = (4000.0, 8000.0)
_SILENCE_RMS = 120.0  # int16; windows quieter than this are ignored


@dataclass(frozen=True, slots=True)
class RoleGuess:
    role: Role
    confidence: float


def hf_ratio(window: F64, rate: int) -> float | None:
    """High-band / low-band magnitude ratio for one window, or ``None`` if
    the window is silent or has no low-band energy."""
    if np.sqrt(np.mean(window**2)) < _SILENCE_RMS:
        return None
    spec = np.abs(np.fft.rfft(window * np.hanning(window.size)))
    freqs = np.fft.rfftfreq(window.size, 1.0 / rate)

    def band(lo: float, hi: float) -> float:
        return float(spec[(freqs >= lo) & (freqs < hi)].sum())

    denom = band(*_LOW_BAND)
    if denom <= 0.0:
        return None
    return band(*_HIGH_BAND) / denom


def _kmeans2(values: F64, iters: int = 50) -> tuple[float, float]:
    """1-D 2-means; returns the two centroids sorted ascending."""
    c = np.percentile(values, [20.0, 80.0]).astype(np.float64)
    for _ in range(iters):
        labels = (np.abs(values[:, None] - c[None, :])).argmin(axis=1)
        new = np.array(
            [values[labels == k].mean() if np.any(labels == k) else c[k] for k in (0, 1)]
        )
        if np.allclose(new, c):
            break
        c = new
    lo, hi = float(np.min(c)), float(np.max(c))
    return lo, hi


class AcousticRoleClassifier:
    def __init__(
        self,
        *,
        rate: int = 16_000,
        window_ms: int = 200,
        calibration_s: float = 8.0,
        min_confidence: float = 0.4,
    ) -> None:
        self._rate = rate
        self._win = int(rate * window_ms / 1000)
        self._calib_samples = int(rate * calibration_s)
        self._min_conf = min_confidence
        self._buf: F64 = np.zeros(0, dtype=np.float64)
        self._samples_seen = 0
        self._track: list[tuple[float, float]] = []  # (t_center_s, log hf_ratio)
        self._centroids: tuple[float, float] | None = None  # (far/low, near/high) in log space

    # -- ingest ------------------------------------------------------------

    def observe(self, pcm: bytes | F64) -> None:
        x = (
            np.frombuffer(pcm, dtype="<i2").astype(np.float64)
            if isinstance(pcm, bytes | bytearray)
            else np.asarray(pcm, dtype=np.float64)
        )
        self._buf = np.concatenate([self._buf, x])
        while self._buf.size >= self._win:
            w = self._buf[: self._win]
            self._buf = self._buf[self._win :]
            t_center = (self._samples_seen + self._win / 2) / self._rate
            self._samples_seen += self._win
            r = hf_ratio(w, self._rate)
            if r is not None and r > 0.0:
                self._track.append((t_center, float(np.log(r))))
        self._maybe_calibrate()

    def _maybe_calibrate(self) -> None:
        if self._centroids is not None or self._samples_seen < self._calib_samples:
            return
        vals = np.array([lr for _, lr in self._track], dtype=np.float64)
        if vals.size < 8:
            return
        self._centroids = _kmeans2(vals)

    @property
    def calibrated(self) -> bool:
        return self._centroids is not None

    @property
    def centroids(self) -> tuple[float, float] | None:
        """(far/low-band, near/high-band) log-ratio cluster centres, once calibrated."""
        return self._centroids

    # -- classify --------------------------------------------------------

    def classify(self, t_start: float, t_end: float) -> RoleGuess:
        if self._centroids is None:
            return RoleGuess("UNKNOWN", 0.0)
        seg = [lr for (t, lr) in self._track if t_start <= t <= t_end]
        if not seg:
            return RoleGuess("UNKNOWN", 0.0)

        lr = float(np.median(seg))
        low, high = self._centroids
        d_low, d_high = abs(lr - low), abs(lr - high)
        sep = max(high - low, 1e-6)
        confidence = min(1.0, abs(d_high - d_low) / sep)  # 0 at the midpoint, 1 at a centroid
        if confidence < self._min_conf:
            return RoleGuess("UNKNOWN", confidence)
        # nearer the LOW centroid => telephone-band => the far party => CALLER
        role: Role = "CALLER" if d_low < d_high else "CALLEE"
        return RoleGuess(role, confidence)
