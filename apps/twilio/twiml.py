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

from packages.ingress.mediastream.markup import reject, stream_and_dial, stream_and_hold


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


__all__ = ["reject", "stream_and_dial", "stream_and_hold", "verify_signature"]
