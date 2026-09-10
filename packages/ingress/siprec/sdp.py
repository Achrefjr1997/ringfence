"""Just enough SDP (RFC 4566) for a SIPREC SRS: read the offer's audio
``m=`` lines and write a ``recvonly`` answer that keeps only the G.711
payload types we decode.

An SRS never sends media, so every answered stream is ``a=recvonly`` and
the offer's own direction (``sendonly`` / ``sendrecv``) is informational.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from packages.ingress.siprec.g711 import PT_PCMA, PT_PCMU

_SUPPORTED_PT = (PT_PCMU, PT_PCMA)
_DIRECTIONS = frozenset({"sendrecv", "sendonly", "recvonly", "inactive"})


@dataclass(frozen=True, slots=True)
class SdpMedia:
    kind: str  # "audio", "video", ...
    port: int
    payload_types: tuple[int, ...]
    rtpmap: dict[int, str] = field(default_factory=dict)
    direction: str = "sendrecv"
    label: str | None = None  # a=label: — ties the stream to rs-metadata

    def first_supported_pt(self) -> int | None:
        for pt in self.payload_types:
            if pt in _SUPPORTED_PT:
                return pt
        return None


@dataclass(frozen=True, slots=True)
class SdpOffer:
    connection_ip: str | None
    media: tuple[SdpMedia, ...]

    def audio(self) -> tuple[SdpMedia, ...]:
        return tuple(m for m in self.media if m.kind == "audio")


@dataclass
class _MutMedia:
    kind: str
    port: int
    payload_types: list[int]
    rtpmap: dict[int, str] = field(default_factory=dict)
    direction: str = "sendrecv"
    label: str | None = None

    def freeze(self) -> SdpMedia:
        return SdpMedia(
            kind=self.kind,
            port=self.port,
            payload_types=tuple(self.payload_types),
            rtpmap=self.rtpmap,
            direction=self.direction,
            label=self.label,
        )


def _lines(sdp: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for raw in sdp.replace("\r\n", "\n").split("\n"):
        if not raw or "=" not in raw:
            continue
        key, _, value = raw.partition("=")
        out.append((key.strip(), value.strip()))
    return out


def parse_offer(sdp: str) -> SdpOffer:
    session_ip: str | None = None
    media: list[_MutMedia] = []
    current: _MutMedia | None = None

    for key, value in _lines(sdp):
        if key == "c" and current is None:
            parts = value.split()
            if len(parts) >= 3:
                session_ip = parts[2]
        elif key == "m":
            parts = value.split()
            if len(parts) < 4:
                raise ValueError(f"malformed m= line: {value!r}")
            current = _MutMedia(
                kind=parts[0],
                port=int(parts[1]),
                payload_types=[int(p) for p in parts[3:] if p.isdigit()],
            )
            media.append(current)
        elif key == "a" and current is not None:
            attr, _, rest = value.partition(":")
            if attr == "rtpmap":
                num, _, name = rest.partition(" ")
                if num.isdigit():
                    current.rtpmap[int(num)] = name.strip()
            elif attr in _DIRECTIONS:
                current.direction = attr
            elif attr == "label":
                current.label = rest.strip()

    return SdpOffer(connection_ip=session_ip, media=tuple(m.freeze() for m in media))


def build_answer(offer: SdpOffer, *, local_ip: str, ports: list[int], session_id: int = 0) -> str:
    """Answer SDP: one ``recvonly`` line per offered audio stream, port 0 for
    any stream that offered no G.711 payload type.  ``ports`` must have one
    entry per audio ``m=`` line in ``offer``.
    """
    audio = offer.audio()
    if len(ports) != len(audio):
        raise ValueError(
            f"need {len(audio)} ports for {len(audio)} audio streams, got {len(ports)}"
        )

    out = [
        "v=0",
        f"o=ringfence-srs {session_id} 1 IN IP4 {local_ip}",
        "s=RingFence SIPREC SRS",
        f"c=IN IP4 {local_ip}",
        "t=0 0",
    ]
    for media, port in zip(audio, ports, strict=True):
        pt = media.first_supported_pt()
        if pt is None:
            fallback = media.payload_types[0] if media.payload_types else 0
            out.append(f"m=audio 0 RTP/AVP {fallback}")
            continue
        name = media.rtpmap.get(pt, "PCMU/8000" if pt == PT_PCMU else "PCMA/8000")
        out += [
            f"m=audio {port} RTP/AVP {pt}",
            f"a=rtpmap:{pt} {name}",
            "a=recvonly",
        ]
        if media.label is not None:
            out.append(f"a=label:{media.label}")
    return "\r\n".join(out) + "\r\n"
