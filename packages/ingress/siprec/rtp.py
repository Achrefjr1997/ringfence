"""RTP depacketisation (RFC 3550) and a small jitter reorder buffer.

Only what an SRS needs to hand clean PCM downstream: parse the 12-byte
header (+ CSRC list, + optional header extension, + trailing padding), and
put late/reordered packets back in sequence order with a bounded buffer so
one lost packet never stalls the leg.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

_SEQ_MOD = 1 << 16
_HALF = 1 << 15


def _seq_before(a: int, b: int) -> bool:
    """True when ``a`` precedes ``b`` in 16-bit serial-number order (RFC 1982)."""
    d = (b - a) % _SEQ_MOD
    return d != 0 and d < _HALF


@dataclass(frozen=True, slots=True)
class RtpPacket:
    payload_type: int
    sequence: int
    timestamp: int
    ssrc: int
    marker: bool
    payload: bytes

    @classmethod
    def parse(cls, data: bytes) -> RtpPacket:
        if len(data) < 12:
            raise ValueError(f"RTP packet too short: {len(data)} bytes")
        b0, b1 = data[0], data[1]
        version = b0 >> 6
        if version != 2:
            raise ValueError(f"unsupported RTP version {version}")
        padding = bool(b0 & 0x20)
        extension = bool(b0 & 0x10)
        csrc_count = b0 & 0x0F
        marker = bool(b1 & 0x80)
        payload_type = b1 & 0x7F
        sequence = int.from_bytes(data[2:4], "big")
        timestamp = int.from_bytes(data[4:8], "big")
        ssrc = int.from_bytes(data[8:12], "big")

        offset = 12 + 4 * csrc_count
        if len(data) < offset:
            raise ValueError("RTP CSRC list truncated")
        if extension:
            if len(data) < offset + 4:
                raise ValueError("RTP header extension truncated")
            ext_words = int.from_bytes(data[offset + 2 : offset + 4], "big")
            offset += 4 + 4 * ext_words
            if len(data) < offset:
                raise ValueError("RTP header extension truncated")

        end = len(data)
        if padding:
            if end <= offset:
                raise ValueError("RTP padding flag set on an empty payload")
            pad = data[-1]
            if pad == 0 or end - pad < offset:
                raise ValueError(f"invalid RTP padding length {pad}")
            end -= pad

        return cls(
            payload_type=payload_type,
            sequence=sequence,
            timestamp=timestamp,
            ssrc=ssrc,
            marker=marker,
            payload=bytes(data[offset:end]),
        )


T = TypeVar("T")


class SeqReorderer(Generic[T]):
    """Bounded in-order release keyed on the RTP sequence number.

    ``push`` returns the items that are now contiguous from the last one
    released.  A packet older than the release point is dropped as late; a
    repeat of a pending or released sequence is dropped as duplicate.  Once
    ``depth`` packets are held waiting on a gap, the gap is declared lost and
    the buffer advances past it.
    """

    def __init__(self, depth: int = 16) -> None:
        if depth < 1:
            raise ValueError("depth must be >= 1")
        self._depth = depth
        self._next: int | None = None
        self._pending: dict[int, T] = {}
        self.dropped_late = 0
        self.dropped_duplicate = 0
        self.lost = 0

    def push(self, sequence: int, item: T) -> list[T]:
        seq = sequence % _SEQ_MOD
        if self._next is None:
            self._next = seq
        if seq != self._next and _seq_before(seq, self._next):
            self.dropped_late += 1
            return []
        if seq in self._pending:
            self.dropped_duplicate += 1
            return []
        self._pending[seq] = item
        out = self._drain()
        while len(self._pending) > self._depth:
            gap_from = self._next
            assert gap_from is not None
            nxt = min(self._pending, key=lambda k: (k - gap_from) % _SEQ_MOD)
            self.lost += (nxt - gap_from) % _SEQ_MOD
            self._next = nxt
            out.extend(self._drain())
        return out

    @property
    def pending(self) -> int:
        """Packets currently held waiting on an earlier sequence number."""
        return len(self._pending)

    def flush(self) -> list[T]:
        if self._next is None:
            return []
        start = self._next
        ordered = sorted(self._pending, key=lambda k: (k - start) % _SEQ_MOD)
        out = [self._pending[k] for k in ordered]
        self._pending.clear()
        return out

    def _drain(self) -> list[T]:
        assert self._next is not None
        out: list[T] = []
        while self._next in self._pending:
            out.append(self._pending.pop(self._next))
            self._next = (self._next + 1) % _SEQ_MOD
        return out
