"""Oversight console P4 -- the call-access matrix (pure functions)."""

from __future__ import annotations

import pytest

from packages.calls.access import can_view_call, in_scope, report_refs
from packages.identity.models import User


def _u(uid: str, *, manager: str | None = None, ref: str | None = None) -> User:
    return User(
        id=uid,
        org_id="o1",
        email=f"{uid}@o1.co",
        password_hash="x",
        manager_id=manager,
        user_ref=ref,
    )


def test_report_refs_walks_the_tree() -> None:
    boss = _u("boss")
    lead = _u("lead", manager="boss", ref="lead@corp")
    rep1 = _u("rep1", manager="lead", ref="rep1@corp")
    rep2 = _u("rep2", manager="lead")  # no user_ref -> contributes nothing
    outside = _u("outside", ref="outside@corp")
    users = [boss, lead, rep1, rep2, outside]

    assert report_refs(users, "boss") == frozenset({"lead@corp", "rep1@corp"})
    assert report_refs(users, "lead") == frozenset({"rep1@corp"})
    assert report_refs(users, "rep1") == frozenset()


def test_report_refs_tolerates_a_cycle() -> None:
    a = _u("a", manager="b", ref="a@c")
    b = _u("b", manager="a", ref="b@c")
    assert report_refs([a, b], "a") == frozenset({"b@c"})


@pytest.mark.parametrize(
    ("is_admin", "is_owner", "is_shared", "is_mentioned", "private", "expected"),
    [
        (False, False, False, False, False, True),  # public -> anyone
        (False, False, False, False, True, False),  # private -> nobody
        (True, False, False, False, True, True),  # admin always
        (False, True, False, False, True, True),  # owner
        (False, False, True, False, True, True),  # shared
        (False, False, False, True, True, True),  # mentioned
    ],
)
def test_can_view_call(
    is_admin: bool,
    is_owner: bool,
    is_shared: bool,
    is_mentioned: bool,
    private: bool,
    expected: bool,
) -> None:
    assert (
        can_view_call(
            is_admin=is_admin,
            is_owner=is_owner,
            is_shared=is_shared,
            is_mentioned=is_mentioned,
            rec_private=private,
        )
        is expected
    )


def test_in_scope() -> None:
    team = frozenset({"rep@corp"})
    assert in_scope("all", is_owner=False, owner_ref="x", team_refs=team) is True
    assert in_scope("mine", is_owner=True, owner_ref="me", team_refs=team) is True
    assert in_scope("mine", is_owner=False, owner_ref="rep@corp", team_refs=team) is False
    assert in_scope("team", is_owner=False, owner_ref="rep@corp", team_refs=team) is True
    assert in_scope("team", is_owner=False, owner_ref="other", team_refs=team) is False
