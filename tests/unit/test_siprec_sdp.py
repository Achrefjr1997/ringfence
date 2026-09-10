"""SDP offer parsing and the recvonly answer an SRS writes back."""

import pytest

from packages.ingress.siprec.sdp import build_answer, parse_offer

_OFFER = (
    "v=0\r\n"
    "o=SBC 2890844526 1 IN IP4 10.0.0.1\r\n"
    "s=SIPREC\r\n"
    "c=IN IP4 10.0.0.1\r\n"
    "t=0 0\r\n"
    "m=audio 40000 RTP/AVP 0 8 101\r\n"
    "a=rtpmap:0 PCMU/8000\r\n"
    "a=rtpmap:8 PCMA/8000\r\n"
    "a=rtpmap:101 telephone-event/8000\r\n"
    "a=sendonly\r\n"
    "a=label:1\r\n"
    "m=audio 40002 RTP/AVP 8 0\r\n"
    "a=rtpmap:8 PCMA/8000\r\n"
    "a=sendonly\r\n"
    "a=label:2\r\n"
)


def test_parse_offer_reads_both_audio_streams() -> None:
    offer = parse_offer(_OFFER)
    assert offer.connection_ip == "10.0.0.1"
    a = offer.audio()
    assert len(a) == 2
    assert a[0].port == 40000 and a[0].payload_types == (0, 8, 101)
    assert a[0].direction == "sendonly" and a[0].label == "1"
    assert a[0].rtpmap[0] == "PCMU/8000"
    assert a[1].first_supported_pt() == 8  # offer lists 8 before 0 here


def test_build_answer_is_recvonly_and_keeps_labels() -> None:
    offer = parse_offer(_OFFER)
    ans = build_answer(offer, local_ip="192.0.2.9", ports=[50000, 50002])
    assert "c=IN IP4 192.0.2.9" in ans
    assert "m=audio 50000 RTP/AVP 0" in ans
    assert "m=audio 50002 RTP/AVP 8" in ans
    assert ans.count("a=recvonly") == 2
    assert "a=label:1" in ans and "a=label:2" in ans
    assert "a=sendonly" not in ans


def test_stream_with_no_supported_codec_is_declined_with_port_zero() -> None:
    offer = parse_offer(
        "v=0\r\nc=IN IP4 10.0.0.1\r\n"
        "m=audio 40000 RTP/AVP 96\r\na=rtpmap:96 opus/48000/2\r\na=label:1\r\n"
    )
    ans = build_answer(offer, local_ip="192.0.2.9", ports=[50000])
    assert "m=audio 0 RTP/AVP 96" in ans
    assert "a=recvonly" not in ans


def test_build_answer_rejects_wrong_port_count() -> None:
    offer = parse_offer(_OFFER)
    with pytest.raises(ValueError, match="need 2 ports"):
        build_answer(offer, local_ip="192.0.2.9", ports=[50000])
