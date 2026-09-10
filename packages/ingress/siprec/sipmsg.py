"""Minimal SIP (RFC 3261) message handling for a SIPREC SRS.

An SRS terminates exactly one dialog per call — ``INVITE`` / ``ACK`` /
``BYE`` — plus stateless ``OPTIONS`` keepalives.  It never originates a
request, so there is no transaction layer here: parse what the SBC sends,
copy the mandatory vias/ids back, done.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_COMPACT = {
    "i": "call-id",
    "m": "contact",
    "e": "content-encoding",
    "l": "content-length",
    "c": "content-type",
    "f": "from",
    "s": "subject",
    "k": "supported",
    "t": "to",
    "v": "via",
}
_CANON = {
    "call-id": "Call-ID",
    "cseq": "CSeq",
    "www-authenticate": "WWW-Authenticate",
    "content-length": "Content-Length",
    "content-type": "Content-Type",
    "max-forwards": "Max-Forwards",
    "record-route": "Record-Route",
}


def _canon(name: str) -> str:
    low = name.lower()
    low = _COMPACT.get(low, low)
    return _CANON.get(low, "-".join(w.capitalize() for w in low.split("-")))


def _split_head_body(data: bytes) -> tuple[str, bytes]:
    for sep in (b"\r\n\r\n", b"\n\n"):
        idx = data.find(sep)
        if idx != -1:
            return data[:idx].decode("utf-8", "replace"), data[idx + len(sep) :]
    return data.decode("utf-8", "replace"), b""


@dataclass(frozen=True, slots=True)
class MultipartPart:
    headers: dict[str, str]
    content: bytes

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()


@dataclass(frozen=True, slots=True)
class SipMessage:
    is_request: bool
    method: str | None
    uri: str | None
    status: int | None
    reason: str | None
    headers: dict[str, list[str]]  # lower-case name -> values in wire order
    body: bytes = b""
    raw: bytes = field(default=b"", repr=False)

    def header(self, name: str) -> str | None:
        vals = self.headers.get(name.lower())
        return vals[0] if vals else None

    def get_all(self, name: str) -> list[str]:
        return list(self.headers.get(name.lower(), ()))

    @property
    def call_id(self) -> str | None:
        return self.header("call-id")

    @property
    def cseq_method(self) -> str | None:
        cseq = self.header("cseq")
        return cseq.split()[1].upper() if cseq and len(cseq.split()) >= 2 else None

    def content_type(self) -> str:
        ct = self.header("content-type") or ""
        return ct.split(";")[0].strip().lower()

    def multipart_boundary(self) -> str | None:
        ct = self.header("content-type") or ""
        m = re.search(r'boundary="?([^";]+)"?', ct)
        return m.group(1) if m else None


def parse_message(data: bytes) -> SipMessage:
    head, body = _split_head_body(data)
    lines = head.split("\n")
    if not lines or not lines[0].strip():
        raise ValueError("empty SIP message")
    start = lines[0].strip()

    # Unfold continuation lines (leading WS) into the previous header.
    unfolded: list[str] = []
    for line in lines[1:]:
        line = line.rstrip("\r")
        if not line:
            continue
        if line[:1] in (" ", "\t") and unfolded:
            unfolded[-1] += " " + line.strip()
        else:
            unfolded.append(line)

    headers: dict[str, list[str]] = {}
    for line in unfolded:
        if ":" not in line:
            continue
        name, _, value = line.partition(":")
        key = name.strip().lower()
        key = _COMPACT.get(key, key)
        headers.setdefault(key, []).append(value.strip())

    parts = start.split(" ", 2)
    if start.upper().startswith("SIP/"):
        status = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else None
        reason = parts[2] if len(parts) >= 3 else ""
        return SipMessage(False, None, None, status, reason, headers, body, data)
    method = parts[0].upper()
    uri = parts[1] if len(parts) >= 2 else ""
    return SipMessage(True, method, uri, None, None, headers, body, data)


def split_multipart(body: bytes, boundary: str) -> list[MultipartPart]:
    delim = b"--" + boundary.encode()
    out: list[MultipartPart] = []
    for chunk in body.split(delim):
        chunk = chunk.strip(b"\r\n")
        if not chunk or chunk == b"--":
            continue
        sep = b"\r\n\r\n" if b"\r\n\r\n" in chunk else b"\n\n"
        head, _, content = chunk.partition(sep)
        hdrs: dict[str, str] = {}
        for line in head.decode("utf-8", "replace").splitlines():
            if ":" in line:
                n, _, v = line.partition(":")
                hdrs[n.strip().lower()] = v.strip()
        out.append(MultipartPart(headers=hdrs, content=content.strip(b"\r\n")))
    return out


def build_response(
    request: SipMessage,
    code: int,
    reason: str,
    *,
    extra_headers: list[tuple[str, str]] | None = None,
    body: bytes = b"",
    content_type: str | None = None,
    to_tag: str | None = None,
) -> bytes:
    if not request.is_request:
        raise ValueError("build_response needs a request")
    out: list[str] = [f"SIP/2.0 {code} {reason}"]

    for via in request.get_all("via"):
        out.append(f"Via: {via}")
    for rr in request.get_all("record-route"):
        out.append(f"Record-Route: {rr}")
    out.append(f"From: {request.header('from') or ''}")

    to = request.header("to") or ""
    if to_tag and ";tag=" not in to.lower():
        to = f"{to};tag={to_tag}"
    out.append(f"To: {to}")

    out.append(f"Call-ID: {request.header('call-id') or ''}")
    out.append(f"CSeq: {request.header('cseq') or ''}")
    for name, value in extra_headers or []:
        out.append(f"{_canon(name)}: {value}")
    if body and content_type:
        out.append(f"Content-Type: {content_type}")
    out.append(f"Content-Length: {len(body)}")

    return ("\r\n".join(out) + "\r\n\r\n").encode() + body
