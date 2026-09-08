"""T-7.3 -- billing plan config + tier math."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from packages.billing.plans import Plan, load_plans


def test_ships_the_documented_tiers() -> None:
    book = load_plans()
    assert book.default.id == "pilot"
    assert set(book.ids()) == {"pilot", "starter", "growth", "scale", "enterprise"}
    starter = book.get("starter")
    assert starter.base_cents == 4900
    assert starter.included_minutes == 10 * 300


def test_unknown_or_missing_id_falls_back_to_default() -> None:
    book = load_plans()
    assert book.get(None).id == "pilot"
    assert book.get("nope").id == "pilot"


def test_overage_and_month_cents() -> None:
    p = Plan(
        id="x",
        name="X",
        base_cents=4900,
        included_lines=10,
        minutes_per_line=300,  # 3000 included
        overage_cents_per_min=2.0,
        hard_cap=False,
    )
    assert p.overage_cents(2500) == 0  # under allowance
    assert p.overage_cents(3000) == 0  # exactly at allowance
    assert p.overage_cents(3500) == 1000  # 500 * 2.0c
    assert p.month_cents(3500) == 4900 + 1000


def test_a_hard_capped_plan_charges_no_overage() -> None:
    pilot = load_plans().get("pilot")
    assert pilot.hard_cap is True
    assert pilot.overage_cents(10_000) == 0


def test_bad_plans_file_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "plans.yaml"
    bad.write_text("default: ghost\nplans: {}\n", encoding="utf-8")
    with pytest.raises(Exception):  # noqa: B017 - ValidationError or ValueError
        load_plans(bad)

    negative = tmp_path / "neg.yaml"
    negative.write_text(
        textwrap.dedent(
            """
            default: a
            plans:
              a: {name: A, base_cents: -1, included_lines: 1, minutes_per_line: 1,
                  overage_cents_per_min: 0}
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(Exception):  # noqa: B017
        load_plans(negative)
