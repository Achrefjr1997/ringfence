import json

import pytest

from packages.eval.metrics import compute_metrics, main, render_markdown
from packages.eval.run import run_report


@pytest.fixture(scope="module")
def metrics() -> dict:
    return compute_metrics(run_report())


def test_aggregate_headline(metrics: dict) -> None:
    agg = metrics["aggregate"]
    assert agg["n_fraud"] == 4 and agg["n_benign"] == 4
    # every fraud fixture alerts before its transfer line
    assert agg["call_recall"] == 1.0
    # no benign fixture reaches INTERVENE
    assert agg["fpr_intervene"] == 0.0
    # frauddesk peaks ~28.9, alert is 55 -> ~26 points of headroom
    assert agg["benign_peak_margin"] >= 15.0
    assert agg["ttd_median_s"] is not None and agg["ttd_median_s"] >= 0
    assert agg["role_accuracy"] == 1.0  # fixtures carry ground-truth roles


def test_per_language_breakdown_present(metrics: dict) -> None:
    assert set(metrics["per_language"]) == {"en", "fr", "ar_tn"}
    for lang, s in metrics["per_language"].items():
        assert s["n_fraud"] + s["n_benign"] >= 1
        if s["n_fraud"]:
            assert s["call_recall"] == 1.0
        if s["n_benign"]:
            assert s["fpr_intervene"] == 0.0


def test_per_signal_has_precision_and_recall(metrics: dict) -> None:
    ps = metrics["per_signal"]
    assert "AUTH_CLAIM" in ps and "VERIF_INVERT" in ps
    for s in ps.values():
        assert 0.0 <= s["precision"] <= 1.0 and 0.0 <= s["recall"] <= 1.0
        assert s["tp"] + s["fn"] >= 0


def test_markdown_has_a_per_language_table(metrics: dict) -> None:
    md = render_markdown(metrics)
    assert "## Headline" in md
    assert "| metric | aggregate | ar_tn | en | fr |" in md
    assert "Per-signal precision / recall" in md
    assert "call recall" in md


def test_cli_markdown(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    rep = tmp_path / "r.json"
    rep.write_text(json.dumps(run_report()), encoding="utf-8")
    assert main([str(rep), "--markdown"]) == 0
    out = capsys.readouterr().out
    assert "| metric | aggregate |" in out and "FPR (INTERVENE)" in out
