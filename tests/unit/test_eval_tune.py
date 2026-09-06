import pytest

from packages.eval.gate import check_gate
from packages.eval.metrics import compute_metrics
from packages.eval.run import run_report
from packages.eval.tune import dump_pack, search
from packages.policy.pack import load_pack

BASE = load_pack("config/policy/default.yaml")


def test_search_returns_a_feasible_pack() -> None:
    best = search(base_pack=BASE, trials=10, seed=1)
    t = best.pack.thresholds
    assert t.watch < t.alert < t.intervene
    assert best.recall == 1.0  # never worse than the base
    assert best.metrics["aggregate"]["fpr_intervene"] == 0.0


def test_result_passes_the_release_gate_against_the_current_baseline() -> None:
    baseline = compute_metrics(run_report(pack=BASE))
    best = search(base_pack=BASE, trials=12, seed=2)
    candidate = compute_metrics(run_report(pack=best.pack))
    assert check_gate(baseline, candidate) == []


def test_never_returns_a_pack_that_false_positives() -> None:
    # aggressive jitter — many candidates will be infeasible; the winner must not be
    best = search(base_pack=BASE, trials=20, seed=3, weight_jitter=0.6, thr_jitter=20.0)
    m = best.metrics
    assert (m["aggregate"]["fpr_intervene"] or 0.0) == 0.0
    assert all((s["fpr_intervene"] or 0.0) <= 0.007 for s in m["per_language"].values())


def test_deterministic_for_a_seed() -> None:
    a = search(base_pack=BASE, trials=8, seed=7)
    b = search(base_pack=BASE, trials=8, seed=7)
    assert a.pack.model_dump() == b.pack.model_dump()


def test_dump_pack_reloads(tmp_path) -> None:
    best = search(base_pack=BASE, trials=6, seed=0)
    out = tmp_path / "candidate.yaml"
    dump_pack(best.pack, out)
    reloaded = load_pack(out)
    assert reloaded.thresholds.alert == best.pack.thresholds.alert
    assert reloaded.signals["VERIF_INVERT"].weight == best.pack.signals["VERIF_INVERT"].weight


def test_main_writes_candidate_not_default(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    from packages.eval.tune import main

    default_before = load_pack("config/policy/default.yaml").model_dump()
    out = tmp_path / "cand.yaml"
    assert main(["--out", str(out), "--trials", "5", "--seed", "0"]) == 0
    assert out.exists()
    assert load_pack("config/policy/default.yaml").model_dump() == default_before  # untouched
    assert "NOT promoted" in capsys.readouterr().out
