"""Call-level derived signals (T-1.5b).

``NO_ACTION_ASKED`` — once a call has run past ``_MIN_ELAPSED_S`` and the
CALLER still has not asked for money on an unusual rail, a credential, or
remote access, that absence is itself protective (the pack weight is
negative).  It is folded into ``score_window`` *extras* exactly like
combos, so the offline harness and the streaming pipeline stay identical.
"""

from __future__ import annotations

from packages.contracts.risk import Contribution
from packages.policy.pack import PolicyPack
from packages.risk.scoring import EvidenceWindow

_TRANSFER_SIGNALS = frozenset({"RAIL_UNUSUAL", "VERIF_INVERT", "REMOTE_ACCESS"})
_MIN_ELAPSED_S = 30.0  # a call this long with no ask is meaningfully quiet


def evaluate_derived(window: EvidenceWindow, now: float, pack: PolicyPack) -> list[Contribution]:
    spec = pack.signals.get("NO_ACTION_ASKED")
    if spec is None or now < _MIN_ELAPSED_S:
        return []
    for hit in window.active(now):
        if hit.role == "CALLER" and hit.signal_id in _TRANSFER_SIGNALS:
            return []  # the caller did ask for something — not a quiet call
    return [
        Contribution(
            source="enrichment",
            id="NO_ACTION_ASKED",
            value=float(spec.weight),
            detail=f"no CALLER transfer-ask signal by t={now:.0f}s",
        )
    ]
