"""End-to-end SRS: a scripted SBC sends INVITE + RTP over real localhost
UDP sockets; assert normalised 16 kHz / 40 ms frames come out tagged with
the right leg, and the dialog tears down on BYE.
"""

import asyncio
import socket

import pytest

from packages.ingress.siprec.g711 import PT_PCMU
from packages.ingress.siprec.sipmsg import parse_message
from packages.ingress.siprec.srs import SiprecSession, SiprecSrs

_META = (
    "<?xml version='1.0'?>"
    "<recording xmlns='urn:ietf:params:xml:ns:recording:1'>"
    "<session session_id='sess-ABC=='/>"
    "<participant participant_id='p-bob'><nameID aor='sip:bob@biloxi.com'/></participant>"
    "<participant participant_id='p-paul'><nameID aor='sip:paul@example.com'/></participant>"
    "<stream stream_id='st-96' session_id='sess-ABC=='><label>96</label></stream>"
    "<stream stream_id='st-97' session_id='sess-ABC=='><label>97</label></stream>"
    "<participantstreamassoc participant_id='p-bob'>"
    "<send>st-96</send><recv>st-97</recv></participantstreamassoc>"
    "<participantstreamassoc participant_id='p-paul'>"
    "<send>st-97</send><recv>st-96</recv></participantstreamassoc>"
    "</recording>"
)


def _invite(target: tuple[str, int], sbc_port: int) -> bytes:
    boundary = "b1"
    sdp = (
        "v=0\r\no=sbc 1 1 IN IP4 127.0.0.1\r\ns=rec\r\nc=IN IP4 127.0.0.1\r\nt=0 0\r\n"
        f"m=audio {sbc_port} RTP/AVP 0\r\na=rtpmap:0 PCMU/8000\r\na=sendonly\r\na=label:96\r\n"
        f"m=audio {sbc_port + 2} RTP/AVP 0\r\na=rtpmap:0 PCMU/8000\r\na=sendonly\r\na=label:97\r\n"
    )
    body = (
        f"--{boundary}\r\nContent-Type: application/sdp\r\n\r\n{sdp}\r\n"
        f"--{boundary}\r\nContent-Type: application/rs-metadata+xml\r\n\r\n{_META}\r\n"
        f"--{boundary}--\r\n"
    )
    head = (
        f"INVITE sip:srs@{target[0]}:{target[1]} SIP/2.0\r\n"
        "Via: SIP/2.0/UDP 127.0.0.1;branch=z9hG4bKsbc1\r\n"
        "Max-Forwards: 70\r\n"
        "From: <sip:sbc@127.0.0.1>;tag=sbctag\r\n"
        f"To: <sip:srs@{target[0]}>\r\n"
        "Call-ID: call-1@sbc\r\n"
        "CSeq: 1 INVITE\r\n"
        f'Content-Type: multipart/mixed;boundary="{boundary}"\r\n'
        f"Content-Length: {len(body)}\r\n\r\n"
    )
    return head.encode() + body.encode()


def _bye(target: tuple[str, int]) -> bytes:
    return (
        f"BYE sip:srs@{target[0]}:{target[1]} SIP/2.0\r\n"
        "Via: SIP/2.0/UDP 127.0.0.1;branch=z9hG4bKsbc2\r\n"
        "From: <sip:sbc@127.0.0.1>;tag=sbctag\r\n"
        f"To: <sip:srs@{target[0]}>\r\n"
        "Call-ID: call-1@sbc\r\n"
        "CSeq: 2 BYE\r\n\r\n"
    ).encode()


def _rtp(seq: int, ts: int, payload: bytes, *, pt: int = PT_PCMU, ssrc: int = 0x11223344) -> bytes:
    return (
        bytes([0x80, pt & 0x7F])
        + seq.to_bytes(2, "big")
        + ts.to_bytes(4, "big")
        + ssrc.to_bytes(4, "big")
        + payload
    )


def _answer_ports(ok: bytes) -> list[int]:
    msg = parse_message(ok)
    assert msg.status == 200
    ports: list[int] = []
    for line in msg.body.decode().splitlines():
        if line.startswith("m=audio "):
            ports.append(int(line.split()[1]))
    return ports


async def test_srs_invite_rtp_bye_roundtrip() -> None:
    frames: list[tuple[str, str, int]] = []
    starts: list[SiprecSession] = []
    ends: list[tuple[str, str]] = []

    async def on_audio(sid: str, leg: str, frame: bytes) -> None:
        frames.append((sid, leg, len(frame)))

    async def on_start(s: SiprecSession) -> None:
        starts.append(s)

    async def on_end(sid: str, reason: str) -> None:
        ends.append((sid, reason))

    srs = SiprecSrs(
        on_audio=on_audio,
        on_session_start=on_start,
        on_session_end=on_end,
        caller_aor="sip:bob@biloxi.com",
    )
    host, port = await srs.start()
    loop = asyncio.get_running_loop()

    sbc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sbc.bind(("127.0.0.1", 0))
    sbc.setblocking(False)
    sbc_port = sbc.getsockname()[1]
    try:
        await loop.sock_sendto(sbc, _invite((host, port), sbc_port), (host, port))
        ok = await asyncio.wait_for(loop.sock_recvfrom(sbc, 65535), timeout=2.0)
        rtp_ports = _answer_ports(ok[0])
        assert len(rtp_ports) == 2 and all(p != 0 for p in rtp_ports)

        # 200 ms of mu-law silence per leg: 10 packets x 160 samples @ 8 kHz.
        for i in range(10):
            payload = b"\xff" * 160
            await loop.sock_sendto(sbc, _rtp(i, i * 160, payload), ("127.0.0.1", rtp_ports[0]))
            await loop.sock_sendto(
                sbc, _rtp(i, i * 160, payload, ssrc=0x55667788), ("127.0.0.1", rtp_ports[1])
            )
        await asyncio.sleep(0.2)

        await loop.sock_sendto(sbc, _bye((host, port)), (host, port))
        bye_ok = await asyncio.wait_for(loop.sock_recvfrom(sbc, 65535), timeout=2.0)
        assert parse_message(bye_ok[0]).status == 200
        await asyncio.sleep(0.05)
    finally:
        sbc.close()
        await srs.close()

    assert len(starts) == 1
    assert starts[0].session_id == "sess-ABC--"  # sanitised from metadata session_id
    assert set(starts[0].legs) == {"far", "near"}

    legs_seen = {leg for _sid, leg, _n in frames}
    assert legs_seen == {"far", "near"}
    assert frames and all(n == 640 * 2 for _sid, _leg, n in frames)  # 40 ms @ 16 kHz
    assert all(sid == "sess-ABC--" for sid, _leg, _n in frames)
    assert ends == [("sess-ABC--", "bye")]


async def test_srs_answers_options_keepalive() -> None:
    srs = SiprecSrs(on_audio=_noop)
    host, port = await srs.start()
    loop = asyncio.get_running_loop()
    sbc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sbc.bind(("127.0.0.1", 0))
    sbc.setblocking(False)
    try:
        opts = (
            f"OPTIONS sip:srs@{host}:{port} SIP/2.0\r\n"
            "Via: SIP/2.0/UDP 127.0.0.1;branch=z9hG4bKo\r\n"
            "From: <sip:sbc@127.0.0.1>;tag=t\r\n"
            f"To: <sip:srs@{host}>\r\nCall-ID: k-opts\r\nCSeq: 1 OPTIONS\r\n\r\n"
        ).encode()
        await loop.sock_sendto(sbc, opts, (host, port))
        ok = await asyncio.wait_for(loop.sock_recvfrom(sbc, 65535), timeout=2.0)
        msg = parse_message(ok[0])
        assert msg.status == 200
        assert "INVITE" in (msg.header("allow") or "")
    finally:
        sbc.close()
        await srs.close()


async def test_srs_rejects_invite_without_sdp() -> None:
    srs = SiprecSrs(on_audio=_noop)
    host, port = await srs.start()
    loop = asyncio.get_running_loop()
    sbc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sbc.bind(("127.0.0.1", 0))
    sbc.setblocking(False)
    try:
        bad = (
            f"INVITE sip:srs@{host}:{port} SIP/2.0\r\n"
            "Via: SIP/2.0/UDP 127.0.0.1;branch=z9hG4bKx\r\n"
            "From: <sip:sbc@127.0.0.1>;tag=t\r\n"
            f"To: <sip:srs@{host}>\r\nCall-ID: k-nosdp\r\nCSeq: 1 INVITE\r\n\r\n"
        ).encode()
        await loop.sock_sendto(sbc, bad, (host, port))
        ok = await asyncio.wait_for(loop.sock_recvfrom(sbc, 65535), timeout=2.0)
        assert parse_message(ok[0]).status == 488
    finally:
        sbc.close()
        await srs.close()


async def _noop(sid: str, leg: str, frame: bytes) -> None:
    return None


@pytest.mark.parametrize("raw", [b"not a sip message", b"", b"\x00\x01\x02"])
async def test_srs_ignores_garbage_datagrams(raw: bytes) -> None:
    srs = SiprecSrs(on_audio=_noop)
    host, port = await srs.start()
    loop = asyncio.get_running_loop()
    sbc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sbc.bind(("127.0.0.1", 0))
    sbc.setblocking(False)
    try:
        await loop.sock_sendto(sbc, raw, (host, port))
        await asyncio.sleep(0.05)  # must not raise / must stay up
        opts = (
            f"OPTIONS sip:srs@{host}:{port} SIP/2.0\r\nVia: SIP/2.0/UDP 127.0.0.1;branch=z\r\n"
            f"From: <sip:s>;tag=t\r\nTo: <sip:srs>\r\nCall-ID: k\r\nCSeq: 1 OPTIONS\r\n\r\n"
        ).encode()
        await loop.sock_sendto(sbc, opts, (host, port))
        ok = await asyncio.wait_for(loop.sock_recvfrom(sbc, 65535), timeout=2.0)
        assert parse_message(ok[0]).status == 200
    finally:
        sbc.close()
        await srs.close()
