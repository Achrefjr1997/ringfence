"""Audio normalisation (T-3.1).

Resample to 16 kHz mono int16, remove DC, apply **one session-level** gain
(never per leg), and frame at 40 ms (640 samples).

AGC, AEC and noise suppression are deliberately **not** here and must not be
added.  Role inference (§6.3) reads the *relative* level between the two
legs and the raw band-limiting of the far-end voice; automatic gain would
equalise the levels, echo cancellation and denoising would reshape the
spectrum — each erases a cue the role model depends on.  Per-leg gain is
forbidden for the same reason: the session gain is computed from the
aggregate of both legs and applied identically to each.
"""

from __future__ import annotations

from math import gcd, sqrt

import numpy as np
import numpy.typing as npt
from scipy.signal import lfilter, resample_poly

TARGET_RATE = 16_000
FRAME_SAMPLES = 640  # 40 ms @ 16 kHz
_TARGET_RMS = 0.1 * 32768.0  # ~ -20 dBFS for int16

Int16 = npt.NDArray[np.int16]
F64 = npt.NDArray[np.float64]

_INT16_MIN, _INT16_MAX = -32768, 32767


def _to_int16(x: F64) -> Int16:
    return np.clip(np.round(x), _INT16_MIN, _INT16_MAX).astype(np.int16)


def to_int16_mono(pcm: bytes, *, channels: int = 1) -> Int16:
    """Little-endian int16 PCM bytes -> mono int16 samples."""
    samples = np.frombuffer(pcm, dtype="<i2")
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
        return _to_int16(samples.astype(np.float64))
    return samples.astype(np.int16)


def resample_to_16k(x: Int16, src_rate: int) -> Int16:
    """Polyphase resample to 16 kHz.  Identity when already at 16 kHz."""
    if src_rate <= 0:
        raise ValueError(f"src_rate must be positive, got {src_rate}")
    if src_rate == TARGET_RATE:
        return x.astype(np.int16)
    g = gcd(src_rate, TARGET_RATE)
    up, down = TARGET_RATE // g, src_rate // g
    y = resample_poly(x.astype(np.float64), up, down)
    return _to_int16(y)


class DCBlocker:
    """First-order DC-blocking high-pass: ``y[n] = x[n] - x[n-1] + r·y[n-1]``.
    Carries filter state across chunks so a stream has no seam."""

    def __init__(self, r: float = 0.995) -> None:
        self._b = np.array([1.0, -1.0])
        self._a = np.array([1.0, -r])
        self._zi: F64 = np.zeros(1)

    def process(self, x: Int16 | F64) -> F64:
        y, self._zi = lfilter(self._b, self._a, np.asarray(x, dtype=np.float64), zi=self._zi)
        return np.asarray(y, dtype=np.float64)


class SessionNormaliser:
    """One gain for the whole session.

    Estimated from the accumulated energy of **every** leg, so both legs are
    scaled identically and their relative level is preserved.  It converges
    to the session RMS rather than chasing it, so it calibrates once instead
    of acting as an AGC within the call.
    """

    def __init__(self, target_rms: float = _TARGET_RMS) -> None:
        self._sumsq = 0.0
        self._n = 0
        self._target = target_rms

    def observe(self, x: Int16 | F64) -> None:
        xf = np.asarray(x, dtype=np.float64)
        self._sumsq += float(np.dot(xf, xf))
        self._n += xf.size

    @property
    def gain(self) -> float:
        if self._n == 0:
            return 1.0
        rms = sqrt(self._sumsq / self._n)
        return 1.0 if rms < 1e-6 else self._target / rms

    def apply(self, x: Int16 | F64) -> Int16:
        return _to_int16(np.asarray(x, dtype=np.float64) * self.gain)


class Framer:
    """Re-chunks a stream into exactly ``size``-sample int16 frames,
    buffering whatever does not fill a frame."""

    def __init__(self, size: int = FRAME_SAMPLES) -> None:
        self._size = size
        self._buf: Int16 = np.zeros(0, dtype=np.int16)

    def push(self, x: Int16) -> list[Int16]:
        self._buf = np.concatenate([self._buf, x.astype(np.int16)])
        out: list[Int16] = []
        while self._buf.size >= self._size:
            out.append(self._buf[: self._size].copy())
            self._buf = self._buf[self._size :]
        return out

    @property
    def pending(self) -> int:
        return int(self._buf.size)


class AudioNormaliser:
    """Per-leg front end: resample -> DC removal -> session gain -> framing.
    Share one :class:`SessionNormaliser` across a session's legs."""

    def __init__(
        self, src_rate: int, *, channels: int = 1, session: SessionNormaliser | None = None
    ) -> None:
        self._src = src_rate
        self._channels = channels
        self._dc = DCBlocker()
        self._session = session or SessionNormaliser()
        self._framer = Framer()

    @property
    def session(self) -> SessionNormaliser:
        return self._session

    def process(self, pcm: bytes) -> list[bytes]:
        x = to_int16_mono(pcm, channels=self._channels)
        x16 = resample_to_16k(x, self._src)
        blocked = self._dc.process(x16)
        self._session.observe(blocked)
        normed = self._session.apply(blocked)
        return [f.tobytes() for f in self._framer.push(normed)]
