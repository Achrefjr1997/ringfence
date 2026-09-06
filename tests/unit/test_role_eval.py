import json

import pytest

from packages.eval.fixtures import load_fixture
from packages.eval.role_eval import evaluate_fixture, main, render_markdown, run


@pytest.fixture(scope="module")
def report() -> dict:
    return run(seed=0)


def test_headline_over_all_fixtures(report: dict) -> None:
    assert report["aggregate"]["items"] == 8
    assert report["aggregate"]["role_accuracy"] >= 0.85  # §6.3 bar
    assert report["aggregate"]["coverage"] >= 0.8  # most turns confidently classified


def test_per_language_breakdown(report: dict) -> None:
    assert set(report["per_language"]) == {"en", "fr", "ar_tn"}
    for s in report["per_language"].values():
        assert s["items"] >= 1
        if s["role_accuracy"] is not None:
            assert s["role_accuracy"] >= 0.8


def test_single_fixture_scores_caller_and_callee() -> None:
    r = evaluate_fixture(load_fixture("fx_bank_impersonation_fr_001"), seed=1)
    assert r["calibrated"] is True
    assert r["confident"] >= r["turns"] // 2
    assert r["accuracy"] == 1.0  # clean synthetic separation


def test_deterministic_for_a_seed() -> None:
    assert evaluate_fixture(load_fixture("fx_tech_support_en_001"), seed=3) == evaluate_fixture(
        load_fixture("fx_tech_support_en_001"), seed=3
    )


def test_markdown_and_cli(report: dict, capsys: pytest.CaptureFixture[str]) -> None:
    md = render_markdown(report)
    assert "role accuracy" in md and "| en |" in md and "ground truth" in md
    assert main([]) == 0
    json.loads(capsys.readouterr().out)  # default output is valid JSON
