"""RTP header parsing (RFC 3550) and the bounded sequence reorder buffer."""

import pytest

from packages.ingress.siprec.rtp import RtpPacket, SeqReorderer


def _rtp(
    payload: bytes,
    *,
    seq: int = 1,
    ts: int = 160,
    ssrc: int = 0xDEADBEEF,
    pt: int = 0,
    marker: bool = False,
    csrc: list[int] | None = None,
    ext: bytes | None = None,
    pad: int = 0,
) -> bytes:
    csrc = csrc or []
    b0 = 0x80 | (0x10 if ext is not None else 0) | (0x20 if pad else 0) | len(csrc)
    b1 = (0x80 if marker else 0) | (pt & 0x7F)
    head = (
        bytes([b0, b1]) + seq.to_bytes(2, "big") + ts.to_bytes(4, "big") + ssrc.to_bytes(4, "big")
    )
    for c in csrc:
        head += c.to_bytes(4, "big")
    if ext is not None:
        assert len(ext) % 4 == 0
        head += (0xBEDE).to_bytes(2, "big") + (len(ext) // 4).to_bytes(2, "big") + ext
    body = payload
    if pad:
        body = body + b"\x00" * (pad - 1) + bytes([pad])
    return head + body


def test_parse_minimal_packet() -> None:
    pkt = RtpPacket.parse(_rtp(b"abcd", seq=42, ts=320, pt=8, marker=True))
    assert pkt.payload_type == 8
    assert pkt.sequence == 42
    assert pkt.timestamp == 320
    assert pkt.ssrc == 0xDEADBEEF
    assert pkt.marker is True
    assert pkt.payload == b"abcd"


def test_parse_skips_csrc_and_extension() -> None:
    pkt = RtpPacket.parse(_rtp(b"payload", csrc=[1, 2], ext=b"\x00\x00\x00\x00"))
    assert pkt.payload == b"payload"


def test_parse_trims_padding() -> None:
    pkt = RtpPacket.parse(_rtp(b"voiced", pad=4))
    assert pkt.payload == b"voiced"


def test_parse_rejects_short_and_bad_version() -> None:
    with pytest.raises(ValueError, match="too short"):
        RtpPacket.parse(b"\x80\x00\x00")
    with pytest.raises(ValueError, match="version"):
        RtpPacket.parse(b"\x00" * 12)
    with pytest.raises(ValueError, match="padding"):
        RtpPacket.parse(b"\xa0\x00" + b"\x00" * 10 + b"\x05")  # P set, count past start


def test_reorderer_passes_in_order() -> None:
    r: SeqReorderer[str] = SeqReorderer()
    assert r.push(10, "a") == ["a"]
    assert r.push(11, "b") == ["b"]
    assert r.push(12, "c") == ["c"]


def test_reorderer_buffers_then_releases_on_gap_fill() -> None:
    r: SeqReorderer[str] = SeqReorderer()
    assert r.push(1, "a") == ["a"]
    assert r.push(3, "c") == []  # waiting for 2
    assert r.push(2, "b") == ["b", "c"]


def test_reorderer_drops_late_and_duplicate() -> None:
    r: SeqReorderer[str] = SeqReorderer()
    assert r.push(5, "e") == ["e"]
    assert r.push(3, "late") == []  # below the release point
    assert r.dropped_late == 1
    assert r.push(8, "h") == []  # held, waiting on 6-7
    assert r.push(8, "dup") == []  # already pending
    assert r.dropped_duplicate == 1


def test_reorderer_gives_up_on_a_gap_past_depth() -> None:
    r: SeqReorderer[int] = SeqReorderer(depth=3)
    assert r.push(0, 0) == [0]
    out: list[int] = []
    for s in (2, 3, 4):
        out += r.push(s, s)
    assert out == []  # 1 still missing, 3 held
    out += r.push(5, 5)  # 4 held now > depth 3 -> declare 1 lost
    assert out == [2, 3, 4, 5]
    assert r.lost == 1


def test_reorderer_handles_16bit_wraparound() -> None:
    r: SeqReorderer[int] = SeqReorderer()
    assert r.push(65534, 1) == [1]
    assert r.push(65535, 2) == [2]
    assert r.push(0, 3) == [3]
    assert r.push(1, 4) == [4]


def test_reorderer_flush_drains_remainder_in_order() -> None:
    r: SeqReorderer[str] = SeqReorderer()
    r.push(1, "a")
    r.push(4, "d")
    r.push(3, "c")
    assert r.flush() == ["c", "d"]
    assert r.flush() == []
