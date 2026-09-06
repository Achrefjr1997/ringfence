import copy
import json

import pytest

from packages.eval.gate import check_gate, main
from packages.eval.metrics import compute_metrics
from packages.eval.run import run_report

BASE = run_report()


def _metrics(report: dict) -> dict:
    return compute_metrics(report)


def _worsen(mutate) -> dict:  # noqa: ANN001
    r = copy.deepcopy(BASE)
    mutate(r)
    return r


def test_passes_against_itself() -> None:
    assert check_gate(_metrics(BASE), _metrics(BASE)) == []


def test_fails_on_aggregate_recall_regression() -> None:
    def drop_an_alert(r: dict) -> None:
        for it in r["items"]:
            if it["label"] == "fraud":
                it["first_alert_t"] = None
                break

    fails = check_gate(_metrics(BASE), _metrics(_worsen(drop_an_alert)))
    assert any("recall" in f for f in fails)


def test_fails_on_fpr_regression() -> None:
    def false_positive(r: dict) -> None:
        for it in r["items"]:
            if it["label"] == "benign":
                it["peak_state"] = "INTERVENE"
                break

    fails = check_gate(_metrics(BASE), _metrics(_worsen(false_positive)))
    assert any("FPR" in f for f in fails)


def test_fails_on_per_language_recall_regression() -> None:
    def break_fr(r: dict) -> None:
        for it in r["items"]:
            if it["label"] == "fraud" and it["language"] == "fr":
                it["first_alert_t"] = None

    fails = check_gate(_metrics(BASE), _metrics(_worsen(break_fr)))
    assert any(f.startswith("fr:") and "recall" in f for f in fails)


def test_fails_on_per_language_fpr_ceiling() -> None:
    def fp_en(r: dict) -> None:
        for it in r["items"]:
            if it["label"] == "benign" and it["language"] == "en":
                it["peak_state"] = "INTERVENE"
                break

    fails = check_gate(_metrics(BASE), _metrics(_worsen(fp_en)))
    assert any(f.startswith("en:") and "FPR" in f for f in fails)


def test_recall_within_half_a_point_still_passes() -> None:
    # baseline recall is 1.0; a 0.5pp drop is exactly the bound -> still ok
    b = _metrics(BASE)
    c = copy.deepcopy(b)
    c["aggregate"]["call_recall"] = b["aggregate"]["call_recall"] - 0.005
    assert check_gate(b, c) == []


@pytest.mark.parametrize("worsen", [False, True])
def test_cli(tmp_path, capsys: pytest.CaptureFixture[str], worsen: bool) -> None:
    base_p = tmp_path / "base.json"
    cand_p = tmp_path / "cand.json"
    base_p.write_text(json.dumps(BASE))

    cand = copy.deepcopy(BASE)
    if worsen:
        for it in cand["items"]:
            if it["label"] == "benign":
                it["peak_state"] = "INTERVENE"
    cand_p.write_text(json.dumps(cand))

    rc = main(["--baseline", str(base_p), "--candidate", str(cand_p)])
    out = capsys.readouterr().out
    if worsen:
        assert rc == 1 and "GATE FAIL" in out
    else:
        assert rc == 0 and "GATE PASS" in out
