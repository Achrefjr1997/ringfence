"""ITU-T G.711 companding — the carrier baseline codec (SDP payload types
0 = PCMU / mu-law, 8 = PCMA / A-law).

Both directions are a fixed 8-bit -> 14-bit mapping, so we precompute the
256-entry tables once at import and decode a whole RTP payload with a single
NumPy gather.  The scalar builders below are the Sun ``g711.c`` reference,
kept readable rather than clever because they run 256 times total.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

Int16 = npt.NDArray[np.int16]

_BIAS = 0x84  # 132
_SIGN_BIT = 0x80
_QUANT_MASK = 0x0F
_SEG_MASK = 0x70
_SEG_SHIFT = 4

# Payload types that carry G.711 (RFC 3551 static assignments).
PT_PCMU = 0
PT_PCMA = 8


def _ulaw_sample(byte: int) -> int:
    u = (~byte) & 0xFF
    t = ((u & _QUANT_MASK) << 3) + _BIAS
    t <<= (u & _SEG_MASK) >> _SEG_SHIFT
    return _BIAS - t if (u & _SIGN_BIT) else t - _BIAS


def _alaw_sample(byte: int) -> int:
    a = byte ^ 0x55
    t = (a & _QUANT_MASK) << 4
    seg = (a & _SEG_MASK) >> _SEG_SHIFT
    if seg == 0:
        t += 8
    elif seg == 1:
        t += 0x108
    else:
        t = (t + 0x108) << (seg - 1)
    return t if (a & _SIGN_BIT) else -t


_ULAW_TABLE: Int16 = np.array([_ulaw_sample(b) for b in range(256)], dtype=np.int16)
_ALAW_TABLE: Int16 = np.array([_alaw_sample(b) for b in range(256)], dtype=np.int16)


def ulaw_decode(payload: bytes) -> Int16:
    """mu-law bytes -> linear PCM16 samples."""
    return _ULAW_TABLE[np.frombuffer(payload, dtype=np.uint8)]


def alaw_decode(payload: bytes) -> Int16:
    """A-law bytes -> linear PCM16 samples."""
    return _ALAW_TABLE[np.frombuffer(payload, dtype=np.uint8)]


def decode(payload: bytes, payload_type: int) -> Int16:
    """Decode an RTP audio payload by its SDP payload type (0 or 8)."""
    if payload_type == PT_PCMU:
        return ulaw_decode(payload)
    if payload_type == PT_PCMA:
        return alaw_decode(payload)
    raise ValueError(f"unsupported G.711 payload type {payload_type} (want 0 PCMU or 8 PCMA)")
