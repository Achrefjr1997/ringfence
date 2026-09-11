"""TwiML / TeXML generation.

Telnyx's TeXML is TwiML-compatible -- same ``<Start><Stream>``, same
``track="both_tracks"``, same ``<Parameter>`` children -- so one builder
serves both providers and the only per-provider code is the envelope parser
and the webhook signature scheme.
"""

from __future__ import annotations

from xml.sax.saxutils import quoteattr


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
