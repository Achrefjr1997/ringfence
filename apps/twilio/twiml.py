"""TwiML generation and Twilio request-signature verification.

Both are small and both are security-relevant, so they live here as pure
functions with no I/O and get tested directly.

The signature check is not optional.  ``POST /voice`` starts a billable
call and opens a media stream; an unauthenticated one is somebody else's
phone bill and somebody else's audio arriving in our pipeline.  We have
been here before — ``POST /replay`` shipped with no auth at all and took
``?tenant=`` from the query string (fixed in #62).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from xml.sax.saxutils import quoteattr

_MEDIA_ENC = "audio/x-mulaw"


def verify_signature(*, auth_token: str, url: str, params: dict[str, str], signature: str) -> bool:
    """Validate ``X-Twilio-Signature`` (HMAC-SHA1, base64).

    Twilio signs ``url`` with every POST parameter appended as ``key+value``
    in **lexicographic key order**.  Compared with ``compare_digest`` so the
    check is not a timing oracle.
    """
    if not auth_token or not signature:
        return False
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode("utf-8"), hashlib.sha1).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


def stream_and_dial(*, stream_url: str, dial_to: str, params: dict[str, str] | None = None) -> str:
    """TwiML that forks both tracks to us, then bridges the call onward.

    ``<Start>`` rather than ``<Connect>``: it starts the fork and *continues*
    to the next verb, so the call still reaches the person.  ``<Connect>``
    would block there and the phone would never ring.

    ``track="both_tracks"`` is what makes exact role attribution possible —
    one socket carrying inbound and outbound separately, which the parser
    maps to ``far`` and ``near``.  Without it every turn lands as MIXED and
    the acoustic classifier has to guess something we already know.
    """
    extra = "".join(
        f"<Parameter name={quoteattr(k)} value={quoteattr(v)}/>" for k, v in (params or {}).items()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Start><Stream url={quoteattr(stream_url)} track="both_tracks">{extra}</Stream></Start>'
        f"<Dial><Number>{_escape_text(dial_to)}</Number></Dial>"
        "</Response>"
    )


def reject(reason: str = "rejected") -> str:
    """TwiML for a call we will not take — hang up rather than bill for it."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><!-- {_escape_text(reason)} --><Hangup/></Response>"
    )


def _escape_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
