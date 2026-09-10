"""SIPREC Session Recording Server (``docs/SIPREC.md`` P2).

A passive recorder: the SBC (recording client) sends one ``INVITE`` per
call carrying SDP + ``rs-metadata`` and forks the media as two RTP streams;
this server answers ``recvonly``, receives the RTP, decodes G.711, resamples
to 16 kHz, and hands 40 ms frames to a callback.  It is never in the call
path.

Scope, deliberately small (a SIPREC SRS needs no more):

* one dialog per call — ``INVITE`` / ``ACK`` / ``BYE``; ``CANCEL`` is a BYE;
* stateless ``OPTIONS`` -> ``200 OK`` so the SBC's keepalive sees a live
  socket (``docs/DESIGN_PRODUCTION.md`` §3.2 failure mode);
* no forking, no auth (mTLS terminates at the SBC / a TLS front — P4), no
  re-INVITE.

The uplink to ``/ws/capture`` is P3; here the callbacks are the seam.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from packages.ingress.siprec.g711 import decode
from packages.ingress.siprec.metadata import RecordingMetadata, parse_recording_metadata
from packages.ingress.siprec.rtp import RtpPacket, SeqReorderer
from packages.ingress.siprec.sdp import parse_offer
from packages.ingress.siprec.sdp import build_answer as _build_answer
from packages.ingress.siprec.sipmsg import (
    SipMessage,
    build_response,
    parse_message,
    split_multipart,
)
from packages.media.normalise import AudioNormaliser, SessionNormaliser

_G711_RATE = 8_000
_ALLOW = "INVITE, ACK, BYE, CANCEL, OPTIONS"
_QUEUE_MAX = 400  # ~16 s of 40 ms frames per leg before we drop


def _sanitise_session_id(raw: str) -> str:
    """Make a metadata/Call-ID value safe as a URL param and a path segment."""
    return re.sub(r"[^A-Za-z0-9_.-]", "-", raw)[:128] or "siprec"


@dataclass(frozen=True, slots=True)
class SiprecSession:
    session_id: str
    call_id: str
    metadata: RecordingMetadata | None
    legs: tuple[str, ...]  # the leg ids that will appear in on_audio, in SDP order


OnSessionStart = Callable[[SiprecSession], Awaitable[None]]
OnAudio = Callable[[str, str, bytes], Awaitable[None]]  # session_id, leg, 40 ms PCM16 @ 16 kHz
OnSessionEnd = Callable[[str, str], Awaitable[None]]  # session_id, reason


@dataclass
class _Leg:
    leg_id: str
    payload_type: int
    reorderer: SeqReorderer[RtpPacket]
    normaliser: AudioNormaliser
    transport: asyncio.DatagramTransport
    lost_seen: int = 0  # reorderer.lost already folded into SiprecSrs.rtp_lost


@dataclass
class _Dialog:
    call_id: str
    session_id: str
    to_tag: str
    peer: tuple[str, int]
    metadata: RecordingMetadata | None
    legs: dict[str, _Leg]  # leg_id -> leg
    queue: asyncio.Queue[tuple[str, bytes] | None]
    session_norm: SessionNormaliser
    pump: asyncio.Task[None] | None = None
    last_ok: bytes = b""  # last 200 OK, for retransmission
    ended: bool = False


class _RtpProtocol(asyncio.DatagramProtocol):
    def __init__(self, on_packet: Callable[[bytes], None]) -> None:
        self._on_packet = on_packet

    def datagram_received(self, data: bytes, addr: object) -> None:
        self._on_packet(data)


class _SipProtocol(asyncio.DatagramProtocol):
    def __init__(self, srs: SiprecSrs) -> None:
        self._srs = srs

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        assert isinstance(transport, asyncio.DatagramTransport)
        self._srs._sip_transport = transport

    def datagram_received(self, data: bytes, addr: object) -> None:
        if not (isinstance(addr, tuple) and len(addr) >= 2):
            return
        peer = (str(addr[0]), int(addr[1]))
        self._srs._spawn(self._srs._on_sip(data, peer))


class SiprecSrs:
    """Serve SIPREC on one UDP port.  Wire the callbacks to ``/ws/capture``
    (P3) or, in tests, straight to an assertion.

    ``caller_aor`` — when the deployment knows which participant AOR is the
    calling party, streams resolve to ``far`` (caller) / ``near`` (callee)
    exactly.  Without it a leg is passed through as ``leg-<label>`` and the
    capture path attributes turns acoustically (degraded, never wrong).

    ``rtp_port_range`` — ``(lo, hi)`` inclusive to allocate RTP sockets from a
    fixed span (so an external SBC can be firewalled to it); ``None`` uses an
    ephemeral port per stream.
    """

    def __init__(
        self,
        *,
        on_audio: OnAudio,
        on_session_start: OnSessionStart | None = None,
        on_session_end: OnSessionEnd | None = None,
        advertise_ip: str = "127.0.0.1",
        caller_aor: str | None = None,
        rtp_port_range: tuple[int, int] | None = None,
    ) -> None:
        self._on_audio = on_audio
        self._on_start = on_session_start
        self._on_end = on_session_end
        self._advertise_ip = advertise_ip
        self._caller_aor = caller_aor
        self._rtp_lo, self._rtp_hi = rtp_port_range or (0, 0)
        self._rtp_next = self._rtp_lo

        self._sip_transport: asyncio.DatagramTransport | None = None
        self._dialogs: dict[str, _Dialog] = {}
        self._tasks: set[asyncio.Task[object]] = set()
        self._host = "127.0.0.1"
        self._port = 0

        # observability (P4) — read via ``stats``
        self.sessions_total = 0
        self.rtp_packets = 0
        self.rtp_lost = 0
        self.reorder_depth_max = 0

    def stats(self) -> dict[str, int]:
        return {
            "siprec_sessions_total": self.sessions_total,
            "siprec_sessions_active": len(self._dialogs),
            "siprec_rtp_packets_total": self.rtp_packets,
            "siprec_rtp_lost_total": self.rtp_lost,
            "siprec_reorder_depth_max": self.reorder_depth_max,
        }

    # -- lifecycle -------------------------------------------------------

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> tuple[str, int]:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _SipProtocol(self), local_addr=(host, port)
        )
        sock = transport.get_extra_info("sockname")
        self._host, self._port = sock[0], sock[1]
        return self._host, self._port

    async def close(self) -> None:
        for dialog in list(self._dialogs.values()):
            await self._teardown(dialog, "shutdown")
        if self._sip_transport is not None:
            self._sip_transport.close()
        for task in list(self._tasks):
            task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(*self._tasks, return_exceptions=True)

    # -- SIP ------------------------------------------------------------

    def _spawn(self, coro: Awaitable[object]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _send(self, data: bytes, peer: tuple[str, int]) -> None:
        if self._sip_transport is not None:
            self._sip_transport.sendto(data, peer)

    async def _on_sip(self, data: bytes, peer: tuple[str, int]) -> None:
        try:
            msg = parse_message(data)
        except ValueError:
            return
        if not msg.is_request:
            return
        method = msg.method or ""
        if method == "INVITE":
            await self._on_invite(msg, peer)
        elif method == "OPTIONS":
            self._send(build_response(msg, 200, "OK", extra_headers=[("Allow", _ALLOW)]), peer)
        elif method in ("BYE", "CANCEL"):
            call_id = msg.call_id or ""
            dialog = self._dialogs.get(call_id)
            self._send(
                build_response(msg, 200, "OK", to_tag=dialog.to_tag if dialog else _tag()), peer
            )
            if dialog is not None:
                await self._teardown(dialog, method.lower())
        elif method == "ACK":
            return

    async def _on_invite(self, msg: SipMessage, peer: tuple[str, int]) -> None:
        call_id = msg.call_id or ""
        existing = self._dialogs.get(call_id)
        if existing is not None:  # retransmitted INVITE — resend the answer
            self._send(existing.last_ok, peer)
            return

        sdp_bytes, meta_bytes = _body_parts(msg)
        if not sdp_bytes:
            self._send(build_response(msg, 488, "Not Acceptable Here"), peer)
            return
        try:
            offer = parse_offer(sdp_bytes.decode("utf-8", "replace"))
        except ValueError:
            self._send(build_response(msg, 488, "Not Acceptable Here"), peer)
            return
        metadata = None
        if meta_bytes:
            with contextlib.suppress(ValueError):
                metadata = parse_recording_metadata(meta_bytes)

        session_id = _sanitise_session_id(
            (metadata.session_id if metadata and metadata.session_id else None) or call_id
        )
        to_tag = _tag()
        session_norm = SessionNormaliser()
        queue: asyncio.Queue[tuple[str, bytes] | None] = asyncio.Queue(maxsize=_QUEUE_MAX)
        dialog = _Dialog(
            call_id=call_id,
            session_id=session_id,
            to_tag=to_tag,
            peer=peer,
            metadata=metadata,
            legs={},
            queue=queue,
            session_norm=session_norm,
        )

        loop = asyncio.get_running_loop()
        ports: list[int] = []
        audio = offer.audio()
        for idx, media in enumerate(audio):
            pt = media.first_supported_pt()
            leg_id = _leg_id(media.label, metadata, self._caller_aor, idx)
            if pt is None:
                ports.append(0)
                continue
            rtp_transport = await self._bind_rtp(loop, self._rtp_factory(dialog, leg_id))
            local_port = rtp_transport.get_extra_info("sockname")[1]
            ports.append(local_port)
            dialog.legs[leg_id] = _Leg(
                leg_id=leg_id,
                payload_type=pt,
                reorderer=SeqReorderer(),
                normaliser=AudioNormaliser(src_rate=_G711_RATE, session=session_norm),
                transport=rtp_transport,
            )

        answer = _build_answer(offer, local_ip=self._advertise_ip, ports=ports)
        contact = f"<sip:srs@{self._advertise_ip}:{self._port}>"
        response = build_response(
            msg,
            200,
            "OK",
            extra_headers=[("Contact", contact), ("Allow", _ALLOW)],
            body=answer.encode(),
            content_type="application/sdp",
            to_tag=to_tag,
        )
        dialog.last_ok = response
        self._dialogs[call_id] = dialog
        self.sessions_total += 1
        dialog.pump = asyncio.ensure_future(self._pump(dialog))
        self._send(response, peer)

        if self._on_start is not None:
            legs = tuple(leg.leg_id for leg in dialog.legs.values())
            self._spawn(
                self._on_start(
                    SiprecSession(
                        session_id=session_id,
                        call_id=call_id,
                        metadata=metadata,
                        legs=legs,
                    )
                )
            )

    # -- RTP ----------------------------------------------------------

    def _rtp_factory(self, dialog: _Dialog, leg_id: str) -> Callable[[], _RtpProtocol]:
        def on_packet(raw: bytes) -> None:
            self._on_rtp(dialog, leg_id, raw)

        return lambda: _RtpProtocol(on_packet)

    async def _bind_rtp(
        self, loop: asyncio.AbstractEventLoop, factory: Callable[[], _RtpProtocol]
    ) -> asyncio.DatagramTransport:
        if self._rtp_hi <= self._rtp_lo:
            transport, _ = await loop.create_datagram_endpoint(factory, local_addr=(self._host, 0))
            return transport
        span = self._rtp_hi - self._rtp_lo + 1
        for _ in range(span):
            port = self._rtp_next
            self._rtp_next = self._rtp_lo + (self._rtp_next - self._rtp_lo + 1) % span
            try:
                transport, _ = await loop.create_datagram_endpoint(
                    factory, local_addr=(self._host, port)
                )
                return transport
            except OSError:
                continue
        raise OSError(f"no free RTP port in {self._rtp_lo}-{self._rtp_hi}")

    def _on_rtp(self, dialog: _Dialog, leg_id: str, raw: bytes) -> None:
        if dialog.ended:
            return
        try:
            pkt = RtpPacket.parse(raw)
        except ValueError:
            return
        leg = dialog.legs.get(leg_id)
        if leg is None:
            return
        self.rtp_packets += 1
        for ordered in leg.reorderer.push(pkt.sequence, pkt):
            samples = decode(ordered.payload, leg.payload_type)
            for frame in leg.normaliser.process(samples.astype("<i2").tobytes()):
                with contextlib.suppress(asyncio.QueueFull):
                    dialog.queue.put_nowait((leg_id, frame))
        self.rtp_lost += leg.reorderer.lost - leg.lost_seen
        leg.lost_seen = leg.reorderer.lost
        self.reorder_depth_max = max(self.reorder_depth_max, leg.reorderer.pending)

    async def _pump(self, dialog: _Dialog) -> None:
        while True:
            item = await dialog.queue.get()
            if item is None:
                return
            leg_id, frame = item
            with contextlib.suppress(Exception):
                await self._on_audio(dialog.session_id, leg_id, frame)

    # -- teardown ---------------------------------------------------

    async def _teardown(self, dialog: _Dialog, reason: str) -> None:
        if dialog.ended:
            return
        dialog.ended = True
        self._dialogs.pop(dialog.call_id, None)
        for leg in dialog.legs.values():
            for tail in leg.reorderer.flush():
                samples = decode(tail.payload, leg.payload_type)
                for frame in leg.normaliser.process(samples.astype("<i2").tobytes()):
                    with contextlib.suppress(asyncio.QueueFull):
                        dialog.queue.put_nowait((leg.leg_id, frame))
            leg.transport.close()
        with contextlib.suppress(asyncio.QueueFull):
            dialog.queue.put_nowait(None)
        if dialog.pump is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(dialog.pump, timeout=2.0)
        if self._on_end is not None:
            self._spawn(self._on_end(dialog.session_id, reason))


def _tag() -> str:
    return secrets.token_hex(6)


def _body_parts(msg: SipMessage) -> tuple[bytes, bytes]:
    """(sdp, rs-metadata) from the INVITE body — multipart or a bare SDP."""
    if msg.content_type() == "multipart/mixed":
        boundary = msg.multipart_boundary()
        if not boundary:
            return b"", b""
        sdp = meta = b""
        for part in split_multipart(msg.body, boundary):
            if part.content_type == "application/sdp":
                sdp = part.content
            elif part.content_type in ("application/rs-metadata+xml", "application/rs-metadata"):
                meta = part.content
        return sdp, meta
    if msg.content_type() == "application/sdp":
        return msg.body, b""
    return b"", b""


def _leg_id(
    label: str | None,
    metadata: RecordingMetadata | None,
    caller_aor: str | None,
    index: int,
) -> str:
    if metadata is not None and label is not None:
        leg = metadata.leg_for_label(label, caller_aor=caller_aor)
        if leg is not None:
            return leg
    if label is not None:
        return f"leg-{label}"
    return f"leg-{index}"
