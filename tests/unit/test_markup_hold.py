"""Single-leg capture: fork the caller, don't bridge anyone.

``stream_and_dial`` needs two phones -- a caller and the person being
protected.  A Telnyx **trial** account cannot do that: it allows one
verified number at a time, accepts inbound only *from* it and places
outbound only *to* it, so caller and callee collapse into the same handset.

Hold mode drops the ``<Dial>``.  The call still arrives, both directions of
audio still fork, and the caller's speech still scores -- what is lost is
the second leg, so every turn is CALLER and role attribution has nothing to
compare against.  That is a real reduction in what the demo shows, and the
reason this is a separate function rather than a flag: the two markups mean
different things and should not be confused at a glance.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree

import pytest

from packages.ingress.mediastream.markup import stream_and_dial, stream_and_hold

_URL = "wss://demo.example/media"


def _root(xml: str) -> ElementTree.Element:
    return ElementTree.fromstring(xml)  # noqa: S314 - our own output


def test_hold_forks_both_tracks_exactly_as_the_bridged_markup_does() -> None:
    """The capture half must be identical, or hold mode would exercise a
    different audio path than the one we ship."""
    held = _root(stream_and_hold(stream_url=_URL))
    dialed = _root(stream_and_dial(stream_url=_URL, dial_to="+21612345678"))
    held_stream = held.find("./Start/Stream")
    dialed_stream = dialed.find("./Start/Stream")
    assert held_stream is not None and dialed_stream is not None
    assert held_stream.attrib == dialed_stream.attrib == {"url": _URL, "track": "both_tracks"}


def test_hold_bridges_no_one() -> None:
    assert _root(stream_and_hold(stream_url=_URL)).find("./Dial") is None


def test_hold_keeps_the_call_up_so_there_is_something_to_transcribe() -> None:
    """Without a verb after <Start> the call ends immediately and the fork
    carries a fraction of a second of audio."""
    pause = _root(stream_and_hold(stream_url=_URL, hold_s=600)).find("./Pause")
    assert pause is not None
    assert pause.attrib["length"] == "600"


def test_a_spoken_notice_comes_before_the_pause_not_after() -> None:
    """It is a recording notice. After ten minutes of hold it would be
    announced to someone who already hung up."""
    xml = stream_and_hold(stream_url=_URL, notice="Cet appel est analyse.", language="fr-FR")
    children = [child.tag for child in _root(xml)]
    assert children == ["Start", "Say", "Pause"]
    say = _root(xml).find("./Say")
    assert say is not None
    assert say.attrib["language"] == "fr-FR"
    assert say.text == "Cet appel est analyse."


def test_no_notice_means_no_say_element() -> None:
    assert _root(stream_and_hold(stream_url=_URL)).find("./Say") is None


def test_custom_parameters_ride_along_the_same_way() -> None:
    xml = stream_and_hold(stream_url=_URL, params={"user": "alice@corp"})
    param = _root(xml).find("./Start/Stream/Parameter")
    assert param is not None
    assert param.attrib == {"name": "user", "value": "alice@corp"}


@pytest.mark.parametrize("bad", [0, -1])
def test_a_nonpositive_hold_is_refused(bad: int) -> None:
    """A zero-length pause ends the call the instant it starts, which looks
    like a broken fork rather than a config mistake."""
    with pytest.raises(ValueError, match="hold_s"):
        stream_and_hold(stream_url=_URL, hold_s=bad)


def test_a_notice_with_markup_in_it_cannot_break_out_of_the_element() -> None:
    xml = stream_and_hold(stream_url=_URL, notice="</Say><Hangup/><Say>")
    assert _root(xml).find("./Hangup") is None
    say = _root(xml).find("./Say")
    assert say is not None and say.text == "</Say><Hangup/><Say>"


def test_the_url_is_attribute_quoted_not_concatenated() -> None:
    xml = stream_and_hold(stream_url='wss://x/media?a=1&b="2"')
    stream = _root(xml).find("./Start/Stream")
    assert stream is not None
    assert stream.attrib["url"] == 'wss://x/media?a=1&b="2"'


def test_output_is_a_single_well_formed_response_document() -> None:
    xml = stream_and_hold(stream_url=_URL, notice="hi", hold_s=30)
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert _root(xml).tag == "Response"
    assert not re.search(r"<Response>.*<Response>", xml)
