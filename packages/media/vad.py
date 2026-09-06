"""Voice activity detection (T-3.2).

``webrtcvad`` at aggressiveness 2 over 30 ms sub-frames, with a 200 ms
hangover so the tail of a word is not clipped: once speech is seen, the
next ~7 sub-frames stay marked as speech even if the detector goes quiet.

Feeds are arbitrary-length int16 PCM; the detector re-chunks internally.
"""

from __future__ import annotations

from dataclasses import dataclass

import webrtcvad

_SUBFRAME_MS = 30
_HANGOVER_MS = 200
_VALID_RATES = (8_000, 16_000, 32_000, 48_000)


@dataclass(frozen=True, slots=True)
class VadFrame:
    index: int
    t_start: float
    t_end: float
    speech: bool  # after hangover
    raw: bool  # webrtcvad's own decision for this sub-frame


class VoiceActivityDetector:
    def __init__(
        self,
        *,
        aggressiveness: int = 2,
        hangover_ms: int = _HANGOVER_MS,
        sample_rate: int = 16_000,
    ) -> None:
        if sample_rate not in _VALID_RATES:
            raise ValueError(f"sample_rate must be one of {_VALID_RATES}, got {sample_rate}")
        if not 0 <= aggressiveness <= 3:
            raise ValueError(f"aggressiveness must be 0-3, got {aggressiveness}")
        self._vad = webrtcvad.Vad(aggressiveness)
        self._rate = sample_rate
        self._sub_bytes = (sample_rate * _SUBFRAME_MS // 1000) * 2
        self._hang_frames = max(1, round(hangover_ms / _SUBFRAME_MS))
        self._buf = b""
        self._index = 0
        self._hang = 0

    @property
    def subframe_bytes(self) -> int:
        return self._sub_bytes

    def push(self, pcm: bytes) -> list[VadFrame]:
        self._buf += pcm
        out: list[VadFrame] = []
        while len(self._buf) >= self._sub_bytes:
            chunk = self._buf[: self._sub_bytes]
            self._buf = self._buf[self._sub_bytes :]
            out.append(self._classify(chunk))
        return out

    def flush(self) -> VadFrame | None:
        """Emit the trailing partial sub-frame (zero-padded), if any."""
        if not self._buf:
            return None
        chunk = self._buf + b"\x00" * (self._sub_bytes - len(self._buf))
        self._buf = b""
        return self._classify(chunk)

    def _classify(self, chunk: bytes) -> VadFrame:
        raw = self._vad.is_speech(chunk, self._rate)
        if raw:
            self._hang = self._hang_frames
            speech = True
        elif self._hang > 0:
            self._hang -= 1
            speech = True
        else:
            speech = False
        t0 = self._index * _SUBFRAME_MS / 1000.0
        frame = VadFrame(self._index, t0, t0 + _SUBFRAME_MS / 1000.0, speech, raw)
        self._index += 1
        return frame


def speech_ratio(frames: list[VadFrame]) -> float:
    return sum(f.speech for f in frames) / len(frames) if frames else 0.0
