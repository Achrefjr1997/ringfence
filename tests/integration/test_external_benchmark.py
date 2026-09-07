"""External benchmark smoke test (Tier 1 vs the vendored synthetic corpus).

Gated behind ``needs_dataset`` so the default suite and CI stay lean:

    pytest tests/integration/test_external_benchmark.py -q -m needs_dataset

These assertions pin the loader contract and a loose sanity band on the
engine output — not the benchmark numbers themselves, which live in
``docs/EXTERNAL_BENCHMARK.md``.
"""

from __future__ import annotations

import pytest

from packages.eval.external import SNAPSHOT, iter_external
from packages.eval.harness import replay_fixture
from packages.eval.run_external import run_report
from packages.policy.pack import load_pack

pytestmark = pytest.mark.needs_dataset

if not SNAPSHOT.exists():  # pragma: no cover - skip cleanly if the snapshot is absent
    pytest.skip(f"missing {SNAPSHOT}", allow_module_level=True)


def test_snapshot_loads_balanced_and_typed() -> None:
    fx = list(iter_external())
    assert len(fx) == 1600
    assert sum(f.label == "fraud" for f in fx) == 800
    assert sum(f.label == "benign" for f in fx) == 800
    assert {f.language for f in fx} == {"en"}
    assert all(f.scam_family.startswith("external/") for f in fx)
    assert all(len(f.turns) >= 2 for f in fx)


def test_filters() -> None:
    assert len(list(iter_external(split="test"))) == 320
    assert len(list(iter_external(split="train"))) == 1280
    assert len(list(iter_external(label="fraud", limit=10))) == 10
    assert all(f.label == "fraud" for f in iter_external(label="fraud", limit=50))


def test_segmentation_and_roles() -> None:
    first = next(iter_external())
    # Every dialogue opens "Innocent: Hello." then the caller.
    assert first.turns[0].role == "CALLEE"
    assert first.turns[0].text == "Hello."
    assert first.turns[1].role == "CALLER"
    assert "Innocent:" not in first.turns[1].text and "Suspect:" not in first.turns[1].text


def test_timeline_is_monotonic_and_sets_transfer_t() -> None:
    fx = next(iter_external(label="fraud"))
    ends = [t.t_end for t in fx.turns]
    starts = [t.t_start for t in fx.turns]
    assert all(a <= b for a, b in zip(starts, starts[1:], strict=False))
    assert all(t.t_end > t.t_start for t in fx.turns)
    last_caller_end = max(t.t_end for t in fx.turns if t.role == "CALLER")
    assert fx.transfer_request_t == last_caller_end
    assert ends == sorted(ends)


def test_beat_model_overrides_durations() -> None:
    fx = next(iter_external(label="fraud", seconds_per_turn=8.0))
    assert all(round(t.t_end - t.t_start, 3) == 8.0 for t in fx.turns)
    assert [t.t_start for t in fx.turns] == [8.0 * i for i in range(len(fx.turns))]


def test_one_dialogue_replays_cleanly() -> None:
    fx = next(iter_external(label="fraud"))
    r = replay_fixture(fx)
    assert r.fixture_id == fx.id
    assert len(r.traces) == len(fx.turns)
    assert r.peak_state in {"CALM", "WATCH", "ALERT", "INTERVENE"}
    assert r.peak_score >= 0.0


def test_small_run_precision_and_shape() -> None:
    pack = load_pack("config/policy/default.yaml")
    report = run_report(pack=pack, split="test", limit=120)
    items = report["items"]
    assert 0 < len(items) <= 120
    benign = [i for i in items if i["label"] == "benign"]
    # Tier 1 is precise even out of distribution: no benign dialogue should
    # reach INTERVENE, and ALERT+ should be rare.
    assert all(i["peak_state"] != "INTERVENE" for i in benign)
    assert sum(i["peak_state"] in {"ALERT", "INTERVENE"} for i in benign) <= max(
        1, len(benign) // 20
    )
