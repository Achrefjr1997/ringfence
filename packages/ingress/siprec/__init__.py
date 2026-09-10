"""SIPREC ingress adapter (Adapter A — carrier).  See ``docs/SIPREC.md``.

P1 is the pure protocol core: G.711 decode, RTP depacketisation, SDP
offer/answer, SIP message parsing, and ``rs-metadata`` (RFC 7865) parsing.
No sockets live here — that is P2 (``srs.py``).
"""

from __future__ import annotations

from packages.ingress.siprec.g711 import alaw_decode, decode, ulaw_decode
from packages.ingress.siprec.metadata import (
    Participant,
    RecordingMetadata,
    Stream,
    parse_recording_metadata,
)
from packages.ingress.siprec.rtp import RtpPacket, SeqReorderer
from packages.ingress.siprec.sdp import SdpMedia, SdpOffer, build_answer, parse_offer
from packages.ingress.siprec.sipmsg import (
    SipMessage,
    build_response,
    parse_message,
    split_multipart,
)

__all__ = [
    "Participant",
    "RecordingMetadata",
    "RtpPacket",
    "SdpMedia",
    "SdpOffer",
    "SeqReorderer",
    "SipMessage",
    "Stream",
    "alaw_decode",
    "build_answer",
    "build_response",
    "decode",
    "parse_message",
    "parse_offer",
    "parse_recording_metadata",
    "split_multipart",
    "ulaw_decode",
]
