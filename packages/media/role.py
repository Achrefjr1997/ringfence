"""Role attribution.

Turning "which leg" into "caller or callee" is a real problem (§6.3) —
band-limiting cues, diarisation, energy.  For now the stub trusts the
leg's ``role_hint`` from the session descriptor; T-6.x replaces it behind
this same interface.
"""

from __future__ import annotations

from typing import Protocol

from packages.contracts.audio import RoleHint, SessionDescriptor
from packages.contracts.transcript import Role

_HINT_TO_ROLE: dict[RoleHint, Role] = {
    RoleHint.CALLER: "CALLER",
    RoleHint.CALLEE: "CALLEE",
    RoleHint.MIXED: "UNKNOWN",
    RoleHint.UNKNOWN: "UNKNOWN",
}


class RoleAttributor(Protocol):
    def role(self, leg_id: str) -> Role: ...


class StubRoleAttributor:
    """Maps each leg to the role hinted in the descriptor."""

    def __init__(self, desc: SessionDescriptor) -> None:
        self._by_leg: dict[str, Role] = {
            leg.leg_id: _HINT_TO_ROLE.get(leg.role_hint, "UNKNOWN") for leg in desc.legs
        }

    def role(self, leg_id: str) -> Role:
        return self._by_leg.get(leg_id, "UNKNOWN")
