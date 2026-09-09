"""Who may see a call (oversight console P4).

Pure functions -- no I/O -- so the access matrix is unit-testable.

Model (drawn from Gong / Dialpad): a reviewer sees every call in the
tenant that is not marked ``private``. A ``private`` call is visible only
to its owner, to users it was explicitly shared with, to anyone
@mentioned on it, and to an ``admin`` (Dialpad's "Company Admin sees
all"). The manager tree (:func:`report_refs`) and ``scope`` are a
*filter* on top -- "my calls" / "my team's calls" -- not a hard wall,
since RingFence operators are a review team, not rank-and-file.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from packages.identity.models import User

Scope = str  # "mine" | "team" | "all"


def report_refs(users: Sequence[User], root_id: str) -> frozenset[str]:
    """Every ``user_ref`` in the report tree under ``root_id`` (transitive)."""
    children: dict[str, list[User]] = defaultdict(list)
    for u in users:
        if u.manager_id:
            children[u.manager_id].append(u)
    out: set[str] = set()
    seen: set[str] = {root_id}
    stack = [root_id]
    while stack:
        for kid in children.get(stack.pop(), ()):
            if kid.id in seen:
                continue
            seen.add(kid.id)
            if kid.user_ref:
                out.add(kid.user_ref)
            stack.append(kid.id)
    return frozenset(out)


def can_view_call(
    *,
    is_admin: bool,
    is_owner: bool,
    is_shared: bool,
    is_mentioned: bool,
    rec_private: bool,
) -> bool:
    return is_admin or is_owner or is_shared or is_mentioned or not rec_private


def in_scope(
    scope: Scope,
    *,
    is_owner: bool,
    owner_ref: str | None,
    team_refs: frozenset[str],
) -> bool:
    """Narrow a viewable call to the requested ``scope``."""
    if scope == "mine":
        return is_owner
    if scope == "team":
        return is_owner or (owner_ref is not None and owner_ref in team_refs)
    return True  # "all"
