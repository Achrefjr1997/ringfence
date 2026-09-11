"""Provider-neutral media-stream ingress (``docs/`` plan, 2026-09-11).

A call's audio cannot be taken off a handset -- iOS never exposed it and
Android closed the Accessibility-API workaround in May 2022 -- so it has to
come from the network.  Every CPaaS that forks call media does it the same
way: a verb or API call starts the fork, then JSON frames carrying base64
audio arrive over a WebSocket.

Twilio, Plivo and Telnyx all speak that shape with different key names, so
the envelope parsing lives here per provider and everything downstream sees
one :class:`MediaEvent`.  Swapping provider is then a config line rather
than a rewrite -- which matters, because number availability (notably
Tunisian +216) may well force the swap.

Pure parsing only: no sockets, no I/O, no ``await``.  The server that owns
the WebSocket lives in ``apps/twilio``.
"""

from __future__ import annotations

from packages.ingress.mediastream.events import (
    MediaEvent,
    MediaFormatError,
    Track,
    leg_for_track,
)

__all__ = ["MediaEvent", "MediaFormatError", "Track", "leg_for_track"]
