"""G.711 decode tables against the ITU-T reference points."""

import numpy as np
import pytest

from packages.ingress.siprec.g711 import alaw_decode, decode, ulaw_decode


def test_ulaw_reference_points() -> None:
    assert ulaw_decode(b"\xff")[0] == 0  # mu-law positive zero (silence)
    assert ulaw_decode(b"\x7f")[0] == 0  # negative zero
    assert ulaw_decode(b"\x00")[0] == -32124  # full-scale negative
    assert ulaw_decode(b"\x80")[0] == 32124  # full-scale positive


def test_alaw_reference_points() -> None:
    assert alaw_decode(b"\x55")[0] == -8  # A-law idle byte
    assert alaw_decode(b"\xd5")[0] == 8


def test_decode_preserves_length_and_dtype() -> None:
    payload = bytes(range(256))
    out = ulaw_decode(payload)
    assert out.dtype == np.int16
    assert out.shape == (256,)
    assert out.min() >= -32768 and out.max() <= 32767


def test_decode_dispatches_on_payload_type() -> None:
    payload = bytes([0x00, 0x55, 0xFF, 0xD5])
    assert np.array_equal(decode(payload, 0), ulaw_decode(payload))
    assert np.array_equal(decode(payload, 8), alaw_decode(payload))
    with pytest.raises(ValueError, match="unsupported G.711 payload type 96"):
        decode(payload, 96)


def test_ulaw_is_sign_symmetric() -> None:
    lo = ulaw_decode(bytes(range(128)))  # 0x00..0x7f  -> negative half
    hi = ulaw_decode(bytes(range(128, 256)))  # 0x80..0xff -> positive half
    assert np.array_equal(hi, -lo)
