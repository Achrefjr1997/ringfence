"""SIPREC ingress process (``docs/SIPREC.md`` P3).

Runs a :class:`~packages.ingress.siprec.srs.SiprecSrs` and forwards each
recorded leg to the gateway's ``/ws/capture`` over a WebSocket — one per
leg, ``leg=far`` / ``leg=near`` as the SRS resolves them.  The gateway is
unchanged: to it this looks exactly like the two-socket browser SDK path.
"""

from apps.siprec.app import Config, SiprecUplink, run

__all__ = ["Config", "SiprecUplink", "run"]
