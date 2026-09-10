"""A loopback SIPREC recording *client* — plays two audio legs at an SRS
over real UDP, so local development and tests get the full carrier path
(``INVITE`` + ``rs-metadata`` + two RTP streams + ``BYE``) with no SBC.

    python -m packages.ingress.siprec.loopback \
        --srs 127.0.0.1:5060 --far caller.wav --near callee.wav --speed 1

``play_call`` is the reusable core; the CLI just reads WAVs and calls it.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import socket
from math import gcd
from pathlib import Path

import numpy as np
import numpy.typing as npt
from scipy.signal import resample_poly

from packages.ingress.siprec.g711 import PT_PCMU, ulaw_encode
from packages.ingress.siprec.sipmsg import parse_message

_RATE = 8_000
_PTIME = 0.02
_SAMPLES_PER_PKT = int(_RATE * _PTIME)  # 160
Int16 = npt.NDArray[np.int16]


def _metadata(session_id: str, caller_aor: str, callee_aor: str) -> str:
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<recording xmlns='urn:ietf:params:xml:ns:recording:1'>"
        "<datamode>complete</datamode>"
        f"<session session_id='{session_id}'/>"
        f"<participant participant_id='p-caller'><nameID aor='{caller_aor}'/></participant>"
        f"<participant participant_id='p-callee'><nameID aor='{callee_aor}'/></participant>"
        "<stream stream_id='st-1' session_id='s'><label>1</label></stream>"
        "<stream stream_id='st-2' session_id='s'><label>2</label></stream>"
        "<participantstreamassoc participant_id='p-caller'>"
        "<send>st-1</send><recv>st-2</recv></participantstreamassoc>"
        "<participantstreamassoc participant_id='p-callee'>"
        "<send>st-2</send><recv>st-1</recv></participantstreamassoc>"
        "</recording>"
    )


def _invite(srs: tuple[str, int], session_id: str, caller_aor: str, callee_aor: str) -> bytes:
    boundary = "rfloop"
    sdp = (
        "v=0\r\no=loop 1 1 IN IP4 127.0.0.1\r\ns=siprec\r\nc=IN IP4 127.0.0.1\r\nt=0 0\r\n"
        "m=audio 40000 RTP/AVP 0\r\na=rtpmap:0 PCMU/8000\r\na=sendonly\r\na=label:1\r\n"
        "m=audio 40002 RTP/AVP 0\r\na=rtpmap:0 PCMU/8000\r\na=sendonly\r\na=label:2\r\n"
    )
    meta = _metadata(session_id, caller_aor, callee_aor)
    body = (
        f"--{boundary}\r\nContent-Type: application/sdp\r\n\r\n{sdp}\r\n"
        f"--{boundary}\r\nContent-Type: application/rs-metadata+xml\r\n\r\n{meta}\r\n"
        f"--{boundary}--\r\n"
    )
    head = (
        f"INVITE sip:srs@{srs[0]}:{srs[1]} SIP/2.0\r\n"
        "Via: SIP/2.0/UDP 127.0.0.1;branch=z9hG4bKrfloop1\r\n"
        "Max-Forwards: 70\r\n"
        "From: <sip:loopback@127.0.0.1>;tag=rfloop\r\n"
        f"To: <sip:srs@{srs[0]}>\r\n"
        "Call-ID: rf-loopback-call\r\n"
        "CSeq: 1 INVITE\r\n"
        f'Content-Type: multipart/mixed;boundary="{boundary}"\r\n'
        f"Content-Length: {len(body)}\r\n\r\n"
    )
    return head.encode() + body.encode()


def _request(method: str, srs: tuple[str, int], cseq: int) -> bytes:
    return (
        f"{method} sip:srs@{srs[0]}:{srs[1]} SIP/2.0\r\n"
        f"Via: SIP/2.0/UDP 127.0.0.1;branch=z9hG4bKrfloop{cseq}\r\n"
        "From: <sip:loopback@127.0.0.1>;tag=rfloop\r\n"
        f"To: <sip:srs@{srs[0]}>\r\n"
        "Call-ID: rf-loopback-call\r\n"
        f"CSeq: {cseq} {method}\r\n\r\n"
    ).encode()


def _rtp(seq: int, ts: int, payload: bytes, *, ssrc: int, marker: bool) -> bytes:
    b1 = (0x80 if marker else 0) | PT_PCMU
    return (
        bytes([0x80, b1])
        + (seq & 0xFFFF).to_bytes(2, "big")
        + (ts & 0xFFFFFFFF).to_bytes(4, "big")
        + ssrc.to_bytes(4, "big")
        + payload
    )


def _answer_ports(ok: bytes) -> list[int]:
    msg = parse_message(ok)
    return [
        int(line.split()[1])
        for line in msg.body.decode("utf-8", "replace").splitlines()
        if line.startswith("m=audio ")
    ]


def to_8k_mono(pcm: Int16, src_rate: int) -> Int16:
    if src_rate == _RATE:
        return np.asarray(pcm, dtype=np.int16)
    g = gcd(src_rate, _RATE)
    y = np.asarray(
        resample_poly(pcm.astype(np.float64), _RATE // g, src_rate // g), dtype=np.float64
    )
    return np.asarray(np.clip(np.round(y), -32768, 32767), dtype=np.int16)


async def play_call(
    srs_addr: tuple[str, int],
    far: Int16,
    near: Int16,
    *,
    caller_aor: str = "sip:caller@pstn.example",
    callee_aor: str = "sip:callee@ringfence.example",
    session_id: str = "loopback",
    speed: float = 1.0,
) -> None:
    """Drive one call at ``srs_addr``.  ``far`` / ``near`` are 8 kHz mono
    PCM16.  ``speed <= 0`` sends as fast as possible (tests)."""
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    try:
        await loop.sock_sendto(
            sock, _invite(srs_addr, session_id, caller_aor, callee_aor), srs_addr
        )
        ok, _ = await asyncio.wait_for(loop.sock_recvfrom(sock, 65535), timeout=5.0)
        ports = _answer_ports(ok)
        if len(ports) < 2 or 0 in ports[:2]:
            raise RuntimeError(f"SRS did not accept both streams: {ports}")
        await loop.sock_sendto(sock, _request("ACK", srs_addr, 1), srs_addr)

        n = max(len(far), len(near), 1)

        def _pad(a: Int16) -> Int16:
            return np.concatenate([a, np.zeros(n - len(a), dtype=np.int16)])

        legs = ((_pad(far), ports[0], 0x1111_1111), (_pad(near), ports[1], 0x2222_2222))
        step = _PTIME / speed if speed > 0 else 0.0
        for i, start in enumerate(range(0, n, _SAMPLES_PER_PKT)):
            for pcm, port, ssrc in legs:
                chunk = pcm[start : start + _SAMPLES_PER_PKT]
                pkt = _rtp(i, i * _SAMPLES_PER_PKT, ulaw_encode(chunk), ssrc=ssrc, marker=i == 0)
                await loop.sock_sendto(sock, pkt, ("127.0.0.1", port))
            if step:
                await asyncio.sleep(step)

        if not step:
            await asyncio.sleep(0.05)  # let the unpaced RTP burst land before BYE
        await loop.sock_sendto(sock, _request("BYE", srs_addr, 2), srs_addr)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(loop.sock_recvfrom(sock, 65535), timeout=2.0)
    finally:
        sock.close()


def _load_wav(path: Path) -> Int16:
    import soundfile as sf

    data, rate = sf.read(str(path), dtype="int16", always_2d=True)
    mono = data[:, 0] if data.shape[1] == 1 else data.mean(axis=1).astype(np.int16)
    return to_8k_mono(np.asarray(mono, dtype=np.int16), int(rate))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m packages.ingress.siprec.loopback")
    ap.add_argument("--srs", default="127.0.0.1:5060", help="SRS host:port")
    ap.add_argument("--far", type=Path, required=True, help="caller-leg wav")
    ap.add_argument("--near", type=Path, required=True, help="callee-leg wav")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--session", default="loopback")
    ap.add_argument("--caller-aor", default="sip:caller@pstn.example")
    args = ap.parse_args(argv)

    host, _, port = args.srs.rpartition(":")
    srs_addr = (host or "127.0.0.1", int(port))
    asyncio.run(
        play_call(
            srs_addr,
            _load_wav(args.far),
            _load_wav(args.near),
            caller_aor=args.caller_aor,
            session_id=args.session,
            speed=args.speed,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
