"""Call-audio helpers (oversight console P7).

Legs arrive as separate raw PCM16 mono streams. :func:`mix_legs` sums them
into one; :func:`encode_opus` writes an Ogg/Opus blob via ``soundfile``
(already a project dependency -- no libopus install needed).

Nothing here is called unless ``RF_RETAIN_AUDIO`` is on *and* the tenant
opted in; :func:`opus_available` lets the gateway fail fast at startup
rather than at the end of a call.
"""

from __future__ import annotations

import io

_RATE = 16_000
CONTENT_TYPE = "audio/ogg"


def opus_available() -> bool:
    try:
        import soundfile as sf  # noqa: PLC0415

        return "OPUS" in sf.available_subtypes("OGG")
    except Exception:  # noqa: BLE001 - any import/lookup failure means "no"
        return False


def mix_legs(legs: list[bytes]) -> bytes:
    """Sum PCM16-LE mono leg buffers into one, zero-padded and clipped."""
    import numpy as np  # noqa: PLC0415

    arrs = [np.frombuffer(b, dtype="<i2").astype(np.int32) for b in legs if b]
    if not arrs:
        return b""
    n = max(a.size for a in arrs)
    acc = np.zeros(n, dtype=np.int32)
    for a in arrs:
        acc[: a.size] += a
    return np.clip(acc, -32768, 32767).astype("<i2").tobytes()


def encode_opus(pcm: bytes, *, sample_rate: int = _RATE) -> tuple[bytes, str]:
    """(ogg/opus bytes, content-type) from PCM16-LE mono."""
    import numpy as np  # noqa: PLC0415
    import soundfile as sf  # noqa: PLC0415

    audio = np.frombuffer(pcm, dtype="<i2")
    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, format="OGG", subtype="OPUS")
    return buf.getvalue(), CONTENT_TYPE
