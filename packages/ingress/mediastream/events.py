"""The provider-neutral event every CPaaS parser produces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import numpy.typing as npt

Int16 = npt.NDArray[np.int16]

#: Which side of the call a media frame came from.  Providers name these
#: consistently: the *inbound* track is audio arriving at the platform, i.e.
#: the far party who dialled in; *outbound* is what the platform sends to
#: the party we are protecting.
Track = Literal["inbound", "outbound"]

EventKind = Literal["connected", "start", "media", "stop", "other"]


class MediaFormatError(ValueError):
    """The stream is not the codec/rate we can decode.

    Raised loudly rather than tolerated: silently mis-decoding µ-law as
    something else produces audio that sounds like noise and scores like
    silence, which looks exactly like "the detector found nothing".
    """


@dataclass(frozen=True, slots=True)
class MediaEvent:
    kind: EventKind
    stream_id: str = ""
    call_id: str = ""
    track: Track | None = None
    pcm: Int16 | None = None  # decoded linear PCM16 at the source rate
    sample_rate: int = 8_000
    params: dict[str, str] = field(default_factory=dict)  # provider custom params


def leg_for_track(track: Track | None) -> str:
    """Map a provider track onto a RingFence leg id.

    ``far`` and ``near`` are the two ids ``/ws/capture`` turns into
    ``RoleHint.CALLER`` / ``RoleHint.CALLEE``; anything else degrades to
    ``MIXED`` and the acoustic classifier.  On a forked call we *know* which
    side is which, so failing to map here would throw away exact role
    attribution for nothing.
    """
    if track == "inbound":
        return "far"  # the party who called in -- the potential scammer
    if track == "outbound":
        return "near"  # the party we are protecting
    return "mixed"
