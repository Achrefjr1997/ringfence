"""Call-level derived signals (T-1.5b).

``NO_ACTION_ASKED`` — once a call has run past ``_MIN_ELAPSED_S`` and the
CALLER still has not asked for money on an unusual rail, a credential, or
remote access, that absence is itself protective (the pack weight is
negative).  It is folded into ``score_window`` *extras* exactly like
combos, so the offline harness and the streaming pipeline stay identical.
"""

from __future__ import annotations

from math import log2

from packages.contracts.risk import Contribution
from packages.policy.pack import PolicyPack
from packages.risk.scoring import EvidenceWindow

_TRANSFER_SIGNALS = frozenset({"RAIL_UNUSUAL", "VERIF_INVERT", "REMOTE_ACCESS"})
_MIN_ELAPSED_S = 30.0  # a call this long with no ask is meaningfully quiet

# ESCALATION tuning.  Both are chosen by measurement against the 800 benign
# items in the external corpus, not by argument -- see docs/ROADMAP.md 2.1.
_MIN_REPEATS = 3  # below this it is not a pattern, it is one ask
_SATURATE_AT = 4  # at this many repeats the contribution is already capped


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


def evaluate_escalation(window: EvidenceWindow, now: float, pack: PolicyPack) -> list[Contribution]:
    """Credit a CALLER who keeps pressing the same ask.

    Repetition is the one piece of evidence that gets *stronger* when the
    caller rewords: five differently phrased asks raise the count where they
    would dodge a lexicon match.  Three things keep it safe to add:

    * **CALLER-only, risk-signals-only.** A callee repeating themselves and a
      caller repeatedly *refusing* are both ignored, so invariant #1 cannot be
      touched from here.
    * **One contribution, ever.** The strongest repeat across all signals, not
      one per signal — so ESCALATION can move any call by at most the pack
      weight, whatever is repeated and however often.
    * **Saturating.** ``log2(n)`` against ``_SATURATE_AT``: fifty repeats is
      worth the same as four.  A stuck word cannot run away with the score.
    """
    spec = pack.signals.get("ESCALATION")
    if spec is None:
        return []

    worst_id, worst_n = "", 0
    for (signal_id, role), n in window.repeats(now).items():
        if role != "CALLER" or n < _MIN_REPEATS:
            continue
        signal = pack.signals.get(signal_id)
        if signal is None or signal.weight <= 0:  # protective, or not in the pack
            continue
        if n > worst_n:
            worst_id, worst_n = signal_id, n
    if not worst_id:
        return []

    saturation = min(1.0, log2(worst_n) / log2(_SATURATE_AT))
    return [
        Contribution(
            source="signal",
            id="ESCALATION",
            value=float(spec.weight) * saturation,
            role="CALLER",
            t=now,
            detail=f"{worst_id} pressed {worst_n}x by t={now:.0f}s",
        )
    ]
