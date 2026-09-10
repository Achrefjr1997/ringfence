"""rs-metadata (RFC 7865) parsing and label -> leg resolution."""

from packages.ingress.siprec.metadata import parse_recording_metadata

# Adapted from RFC 7865 section 8.1.
_XML = """<?xml version="1.0" encoding="UTF-8"?>
<recording xmlns='urn:ietf:params:xml:ns:recording:1'>
  <datamode>complete</datamode>
  <session session_id="hVpd7YQGRW2nD22h7q60JQ==">
    <sipSessionID>ab30317f1a784dc48ff824d0d3715d86</sipSessionID>
  </session>
  <participant participant_id="srfBElmCRp2QB23b7Mpk0w==">
    <nameID aor="sip:bob@biloxi.com"><name xml:lang="it">Bob</name></nameID>
  </participant>
  <participant participant_id="zSfPoSvdSDCmU3A3TRDxAw==">
    <nameID aor="sip:paul@example.com"><name xml:lang="it">Paul</name></nameID>
  </participant>
  <stream stream_id="UAAMm5GRQKSCMVvLyl4rFw==" session_id="hVpd7YQGRW2nD22h7q60JQ==">
    <label>96</label>
  </stream>
  <stream stream_id="i1Pz3to5hGk8fuXl+PbwCw==" session_id="hVpd7YQGRW2nD22h7q60JQ==">
    <label>97</label>
  </stream>
  <participantstreamassoc participant_id="srfBElmCRp2QB23b7Mpk0w==">
    <send>UAAMm5GRQKSCMVvLyl4rFw==</send>
    <recv>i1Pz3to5hGk8fuXl+PbwCw==</recv>
  </participantstreamassoc>
  <participantstreamassoc participant_id="zSfPoSvdSDCmU3A3TRDxAw==">
    <send>i1Pz3to5hGk8fuXl+PbwCw==</send>
    <recv>UAAMm5GRQKSCMVvLyl4rFw==</recv>
  </participantstreamassoc>
</recording>
"""


def test_parses_session_participants_and_streams() -> None:
    md = parse_recording_metadata(_XML)
    assert md.session_id == "hVpd7YQGRW2nD22h7q60JQ=="
    assert md.sip_session_id == "ab30317f1a784dc48ff824d0d3715d86"
    assert {p.aor for p in md.participants} == {"sip:bob@biloxi.com", "sip:paul@example.com"}
    assert {p.name for p in md.participants} == {"Bob", "Paul"}
    assert {s.label for s in md.streams} == {"96", "97"}


def test_caller_label_from_send_association() -> None:
    md = parse_recording_metadata(_XML)
    assert md.caller_label(caller_aor="sip:bob@biloxi.com") == "96"
    assert md.caller_label(caller_participant_id="zSfPoSvdSDCmU3A3TRDxAw==") == "97"


def test_leg_for_label_maps_caller_to_far_callee_to_near() -> None:
    md = parse_recording_metadata(_XML)
    assert md.leg_for_label("96", caller_aor="bob@biloxi.com") == "far"
    assert md.leg_for_label("97", caller_aor="sip:bob@biloxi.com") == "near"


def test_leg_for_label_degrades_to_none_without_a_caller_hint() -> None:
    md = parse_recording_metadata(_XML)
    assert md.leg_for_label("96") is None


def test_leg_for_label_none_for_unknown_label() -> None:
    md = parse_recording_metadata(_XML)
    assert md.leg_for_label("999", caller_aor="sip:bob@biloxi.com") is None
