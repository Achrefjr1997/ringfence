"""SIP message parsing, multipart split, and the SRS response builder."""

from packages.ingress.siprec.sipmsg import build_response, parse_message, split_multipart

_SDP = b"v=0\r\nm=audio 40000 RTP/AVP 0\r\na=sendonly\r\na=label:1\r\n"
_META = b'<?xml version="1.0"?><recording xmlns="urn:ietf:params:xml:ns:recording:1"></recording>'
_BOUNDARY = "boundary1"

_INVITE = (
    b"INVITE sip:srs@ringfence.example SIP/2.0\r\n"
    b"Via: SIP/2.0/UDP sbc1.example;branch=z9hG4bK1\r\n"
    b"Via: SIP/2.0/UDP core.example;branch=z9hG4bK0\r\n"
    b"Max-Forwards: 70\r\n"
    b"f: <sip:sbc@sbc1.example>;tag=abc\r\n"
    b"t: <sip:srs@ringfence.example>\r\n"
    b"i: call-xyz@sbc1.example\r\n"
    b"CSeq: 1 INVITE\r\n"
    b"Contact: <sip:sbc@sbc1.example>\r\n"
    b'c: multipart/mixed;boundary="' + _BOUNDARY.encode() + b'"\r\n'
    b"\r\n"
    b"--" + _BOUNDARY.encode() + b"\r\n"
    b"Content-Type: application/sdp\r\n\r\n" + _SDP + b"\r\n"
    b"--" + _BOUNDARY.encode() + b"\r\n"
    b"Content-Type: application/rs-metadata+xml\r\n\r\n" + _META + b"\r\n"
    b"--" + _BOUNDARY.encode() + b"--\r\n"
)


def test_parse_invite_headers_and_compact_forms() -> None:
    msg = parse_message(_INVITE)
    assert msg.is_request and msg.method == "INVITE"
    assert msg.uri == "sip:srs@ringfence.example"
    assert msg.header("from") == "<sip:sbc@sbc1.example>;tag=abc"  # compact f:
    assert msg.call_id == "call-xyz@sbc1.example"  # compact i:
    assert msg.cseq_method == "INVITE"
    assert msg.get_all("via") == [
        "SIP/2.0/UDP sbc1.example;branch=z9hG4bK1",
        "SIP/2.0/UDP core.example;branch=z9hG4bK0",
    ]
    assert msg.content_type() == "multipart/mixed"
    assert msg.multipart_boundary() == _BOUNDARY


def test_split_multipart_separates_sdp_from_metadata() -> None:
    msg = parse_message(_INVITE)
    parts = split_multipart(msg.body, _BOUNDARY)
    by_type = {p.content_type: p.content for p in parts}
    assert by_type["application/sdp"].startswith(b"v=0")
    assert b"urn:ietf:params:xml:ns:recording:1" in by_type["application/rs-metadata+xml"]


def test_header_unfolding() -> None:
    wire = b"OPTIONS sip:x SIP/2.0\r\nSubject: line one\r\n  line two\r\nCSeq: 1 OPTIONS\r\n\r\n"
    assert parse_message(wire).header("subject") == "line one line two"


def test_parse_status_line() -> None:
    msg = parse_message(b"SIP/2.0 200 OK\r\nCSeq: 1 INVITE\r\n\r\n")
    assert not msg.is_request
    assert msg.status == 200 and msg.reason == "OK"


def test_build_response_copies_dialog_ids_and_appends_to_tag() -> None:
    invite = parse_message(_INVITE)
    sdp = b"v=0\r\nm=audio 50000 RTP/AVP 0\r\na=recvonly\r\n"
    resp = build_response(
        invite, 200, "OK", body=sdp, content_type="application/sdp", to_tag="srs99"
    )
    text = resp.decode()
    assert text.startswith("SIP/2.0 200 OK\r\n")
    assert text.count("Via: ") == 2  # both vias echoed, in order
    assert "SIP/2.0/UDP sbc1.example;branch=z9hG4bK1" in text
    assert "From: <sip:sbc@sbc1.example>;tag=abc" in text
    assert "To: <sip:srs@ringfence.example>;tag=srs99" in text
    assert "Call-ID: call-xyz@sbc1.example" in text
    assert "CSeq: 1 INVITE" in text
    assert f"Content-Length: {len(sdp)}" in text
    assert resp.endswith(sdp)


def test_build_response_for_options_keepalive() -> None:
    opts = parse_message(
        b"OPTIONS sip:srs SIP/2.0\r\nVia: SIP/2.0/UDP s;branch=z\r\n"
        b"From: <sip:s>;tag=1\r\nTo: <sip:srs>\r\nCall-ID: k\r\nCSeq: 3 OPTIONS\r\n\r\n"
    )
    resp = build_response(opts, 200, "OK", to_tag="t").decode()
    assert resp.startswith("SIP/2.0 200 OK")
    assert "Content-Length: 0" in resp
