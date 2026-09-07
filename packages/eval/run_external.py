"""External benchmark runner — Tier-1 rule engine vs a synthetic scam corpus.

    python -m packages.eval.run_external --report reports/external.json --markdown
    python -m packages.eval.run_external --split test --limit 200

Replays every vendored dialogue (``eval.external``) through ``replay_fixture``
— the same offline path the eight hand-written fixtures use — and reports
call-level recall / false-positive rate, plus a per-family breakdown.

The corpus is **synthetic** (Llama-3-70B, ``BothBosu/multi-agent-scam-
conversation``) and English-only.  These numbers are a generalisation check,
not a policy target and not a CI gate.  See ``docs/EXTERNAL_BENCHMARK.md``.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from packages.eval.external import DATASET_NAME, iter_external
from packages.eval.fixtures import Fixture
from packages.eval.harness import replay_fixture
from packages.eval.metrics import compute_metrics, render_markdown
from packages.policy.pack import PolicyPack, load_pack

_ALERT_STATES = {"ALERT", "INTERVENE"}


def _item(fx: Fixture, pack: PolicyPack) -> dict[str, Any]:
    r = replay_fixture(fx, pack=pack)
    turns = [
        {
            "t": tr.t,
            "role": tr.role,
            "score": round(tr.score, 3),
            "state": tr.state,
            "observed_signals": list(tr.signals),
            "expected_signals": [],
        }
        for tr in r.traces
    ]
    return {
        "id": fx.id,
        "label": fx.label,
        "language": fx.language,
        "scam_family": fx.scam_family,
        "transfer_request_t": fx.transfer_request_t,
        "peak_state": r.peak_state,
        "peak_score": round(r.peak_score, 3),
        "first_alert_t": r.first_alert_t,
        "first_intervene_t": r.first_intervene_t,
        "decisions": [
            {"t": d.t, "state": d.state, "score": round(d.score, 3)} for d in r.decisions
        ],
        "turns": turns,
    }


def run_report(
    *,
    pack: PolicyPack,
    split: str | None = None,
    limit: int | None = None,
    beat: float | None = None,
) -> dict[str, Any]:
    items = [
        _item(fx, pack) for fx in iter_external(split=split, limit=limit, seconds_per_turn=beat)
    ]
    t = pack.thresholds
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "dataset": DATASET_NAME,
        "dataset_kind": "synthetic (Llama-3-70B); English; supplementary — not a gate",
        "timeline": f"beat={beat}s" if beat is not None else "words model",
        "pack": f"{pack.metadata.tenant}@{pack.metadata.version}",
        "asr": "null",
        "split": split or "all",
        "filter_label": None,
        "thresholds": {"watch": t.watch, "alert": t.alert, "intervene": t.intervene},
        "items": items,
    }


def _family_breakdown(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_fam: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for it in items:
        by_fam[it["scam_family"]].append(it)
    out: dict[str, dict[str, Any]] = {}
    for fam, its in sorted(by_fam.items()):
        n = len(its)
        alerted = sum(1 for it in its if it["peak_state"] in _ALERT_STATES)
        intervened = sum(1 for it in its if it["peak_state"] == "INTERVENE")
        label = its[0]["label"]
        out[fam] = {
            "label": label,
            "n": n,
            "alert_rate": round(alerted / n, 4),
            "intervene_rate": round(intervened / n, 4),
            "median_peak_score": round(sorted(it["peak_score"] for it in its)[n // 2], 2),
        }
    return out


def _print_summary(report: dict[str, Any]) -> None:
    items = report["items"]
    m = compute_metrics(report)["aggregate"]
    fraud = [i for i in items if i["label"] == "fraud"]
    benign = [i for i in items if i["label"] == "benign"]
    print(f"\ndataset : {report['dataset']}  ({report['dataset_kind']})")
    print(f"pack    : {report['pack']}   split: {report['split']}   timeline: {report['timeline']}")
    print(f"items   : {len(items)}  ({len(fraud)} fraud, {len(benign)} benign)\n")
    print(f"  call recall (fraud reaches ALERT+ by end) : {m['call_recall']}")
    print(f"  FPR  ALERT+   (benign reaches ALERT+)     : {m['fpr_alert']}")
    print(f"  FPR  INTERVENE(benign reaches INTERVENE)  : {m['fpr_intervene']}")
    print(f"  benign peak-score margin to ALERT        : {m['benign_peak_margin']}")
    print("\n  per family:")
    print(
        f"    {'family':24s} {'label':7s} {'n':>4s} {'alert%':>8s} {'interv%':>8s} {'medPeak':>8s}"
    )
    for fam, s in _family_breakdown(items).items():
        print(
            f"    {fam:24s} {s['label']:7s} {s['n']:4d} "
            f"{s['alert_rate'] * 100:7.1f}% {s['intervene_rate'] * 100:7.1f}% {s['median_peak_score']:8.1f}"
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m packages.eval.run_external")
    ap.add_argument("--pack", type=Path, default=Path("config/policy/default.yaml"))
    ap.add_argument("--split", choices=["train", "test", "all"], default="all")
    ap.add_argument("--limit", type=int, default=None, help="cap dialogues (after split filter)")
    ap.add_argument(
        "--beat",
        type=float,
        default=None,
        help="fixed seconds/turn instead of the words timeline model (e.g. 8)",
    )
    ap.add_argument("--report", type=Path, default=Path("reports/external.json"))
    ap.add_argument("--markdown", action="store_true", help="also print the metrics.py table")
    args = ap.parse_args(argv)

    pack = load_pack(args.pack)
    split = None if args.split == "all" else args.split
    report = run_report(pack=pack, split=split, limit=args.limit, beat=args.beat)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote {args.report}  ({len(report['items'])} items)")

    _print_summary(report)
    if args.markdown:
        print("\n" + render_markdown(compute_metrics(report)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
