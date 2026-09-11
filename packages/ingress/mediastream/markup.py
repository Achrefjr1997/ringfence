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
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Start><Stream url={quoteattr(stream_url)} track="both_tracks">'
        f"{_parameters(params)}</Stream></Start>"
        f"<Dial><Number>{_escape_text(dial_to)}</Number></Dial>"
        "</Response>"
    )


def _parameters(params: dict[str, str] | None) -> str:
    return "".join(
        f"<Parameter name={quoteattr(k)} value={quoteattr(v)}/>" for k, v in (params or {}).items()
    )


def stream_and_hold(
    *,
    stream_url: str,
    hold_s: int = 600,
    notice: str | None = None,
    language: str | None = None,
    params: dict[str, str] | None = None,
) -> str:
    """Fork both tracks and hold the line, bridging no one.

    For accounts that cannot place the second leg.  A Telnyx trial allows
    one verified number at a time and restricts inbound to *from* it and
    outbound to *to* it, so ``stream_and_dial`` would have to bridge the
    caller to themselves.

    What this still demonstrates: a real PSTN call, forked at the network,
    transcribed and scored live.  What it loses: the second leg.  Every turn
    arrives as CALLER, so the role weighting that makes ``far``/``near``
    worth having has nothing to compare against.  Use ``stream_and_dial``
    the moment the account can bridge.

    ``hold_s`` bounds how long the line stays open with nothing else to do;
    a trial caps calls at ten minutes anyway.
    """
    if hold_s <= 0:
        raise ValueError(f"hold_s must be positive (got {hold_s}); a zero pause ends the call")
    say = ""
    if notice is not None:
        lang = f" language={quoteattr(language)}" if language else ""
        say = f"<Say{lang}>{_escape_text(notice)}</Say>"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Start><Stream url={quoteattr(stream_url)} track="both_tracks">'
        f"{_parameters(params)}</Stream></Start>"
        f'{say}<Pause length="{hold_s}"/>'
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
