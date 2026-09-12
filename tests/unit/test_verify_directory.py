"""The trusted directory: the only thing that decides who gets contacted.

The scammer controls the words the classifier sees. The directory is what
stops those words from also choosing who the verification agent talks to --
so its job is less "look up a name" than "make it structurally impossible to
be steered". These tests are written against that property, not just against
the lookup.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from packages.verify.directory import Directory, Institution, get_directory, load_directory

_VERIFY_PKG = Path(__file__).resolve().parents[2] / "packages" / "verify"


def _dir(*institutions: dict[str, object]) -> Directory:
    return Directory([Institution.model_validate(i) for i in institutions])


def _inst(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "amazon",
        "display_name": "Amazon",
        "desk_id": "amazon",
        "line_label": "Amazon account security",
        "aliases": ["amazon account security", "amazon"],
    }
    base.update(kw)
    return base


# -- resolution --------------------------------------------------------------


def test_an_institution_the_caller_names_is_resolved() -> None:
    d = _dir(_inst())
    got = d.resolve("hello, this is Daniel from Amazon account security")
    assert got is not None and got.id == "amazon"


def test_text_naming_nobody_resolves_to_nothing() -> None:
    """No match must mean no contact -- never a default, never a guess."""
    d = _dir(_inst())
    assert d.resolve("hello, this is your electricity supplier") is None


def test_resolution_ignores_case_and_punctuation() -> None:
    d = _dir(_inst())
    assert d.resolve("...AMAZON, Account Security!") is not None


def test_the_longest_alias_wins() -> None:
    """Two institutions can share a word. Matching longest-first is what
    makes the winner deterministic rather than a function of file order."""
    d = _dir(
        _inst(id="amazon", aliases=["amazon"]),
        _inst(
            id="amazon_pay",
            display_name="Amazon Pay",
            desk_id="amazon_pay",
            aliases=["amazon pay fraud team"],
        ),
    )
    got = d.resolve("calling from the amazon pay fraud team today")
    assert got is not None and got.id == "amazon_pay"


def test_resolution_is_independent_of_declaration_order() -> None:
    a = _inst(id="amazon", aliases=["amazon"])
    b = _inst(
        id="amazon_pay", display_name="Amazon Pay", desk_id="amazon_pay", aliases=["amazon pay"]
    )
    text = "this is amazon pay calling"
    assert _dir(a, b).resolve(text) == _dir(b, a).resolve(text)


# -- load-time validation ----------------------------------------------------


def test_a_duplicate_id_is_refused_at_load() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        _dir(_inst(id="amazon"), _inst(id="amazon", aliases=["something else"]))


def test_an_alias_claimed_by_two_institutions_is_refused_at_load() -> None:
    """A shared alias is a coin-flip about who gets telephoned. That must be
    a startup failure, not a runtime surprise mid-call."""
    with pytest.raises(ValueError, match="alias"):
        _dir(
            _inst(id="amazon", aliases=["fraud team"]),
            _inst(id="paypal", display_name="PayPal", desk_id="paypal", aliases=["fraud team"]),
        )


def test_an_institution_with_no_aliases_is_refused_at_load() -> None:
    with pytest.raises(ValueError):
        _dir(_inst(aliases=[]))


def test_a_blank_display_name_is_refused_at_load() -> None:
    with pytest.raises(ValueError):
        _dir(_inst(display_name="  "))


# -- the shipped file --------------------------------------------------------


def test_the_committed_directory_loads_and_is_self_consistent() -> None:
    d = load_directory()
    assert d.desk_ids(), "the shipped directory is empty"
    for inst in d.all():
        assert inst.desk_id in d.desk_ids()
        # Every alias must actually resolve back to its own institution, or
        # the file says one thing and the matcher does another.
        for alias in inst.aliases:
            assert d.resolve(alias) is inst


def test_get_directory_is_cached() -> None:
    assert get_directory() is get_directory()


# -- the structural property -------------------------------------------------


def test_the_verify_package_contains_no_number_parser() -> None:
    """The agent must never be steerable to a destination the caller spoke.

    The directory is the only source of contactable identities, and this is
    what keeps it that way: a future refactor that "helpfully" learns to read
    a phone number out of the transcript fails here rather than in
    production. Cheap to keep, and it guards a property that is otherwise
    only a convention.
    """
    digit_run = re.compile(r"\\d\{\d*,?\d*\}|\\d\+|\[0-9\]")
    offenders = []
    for path in _VERIFY_PKG.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if digit_run.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, "digit-matching regex found in packages/verify:\n" + "\n".join(offenders)
