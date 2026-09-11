"""Decision <-> ``rf.*.decision`` bus-event mapping.

Shared by every dispatcher that consumes the decision stream from outside
the pipeline that produced it -- today ``guardian.py`` (signed webhooks)
and ``intervene_dispatch.py`` (live coaching). One mapping, so the two
dispatchers can never quietly disagree about what a bus payload means.
"""

from __future__ import annotations

from typing import Any

from packages.contracts.risk import Contribution, Decision


def decision_from_event(p: dict[str, Any]) -> Decision:
    return Decision(
        decision_id=str(p.get("decision_id", "")),
        session_id=str(p.get("session_id", "")),
        t=float(p.get("t", 0.0)),
        state=p.get("state", "CALM"),
        score=float(p.get("score", 0.0)),
        policy_pack="",
        contributions=tuple(
            Contribution(
                source=c["source"],
                id=c["id"],
                value=float(c.get("value", 0.0)),
                role=c.get("role"),
            )
            for c in p.get("contributions", [])
        ),
        counterfactual=p.get("counterfactual"),
    )
