"""Evaluation runner (T-5.2).

    python -m packages.eval.run --all --report reports/latest.json
    python -m packages.eval.run --label benign --pack config/policy/default.yaml

Replays every corpus item (the hand-written fixtures) through the offline
engine, collects the decision chain and per-turn signal traces, and writes
a JSON report for the metrics step (T-5.3).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from packages.eval.fixtures import iter_fixtures
from packages.eval.harness import run_fixture
from packages.policy.pack import PolicyPack, load_pack


def _item_report(fixture_id: str, pack: PolicyPack) -> dict[str, Any]:
    fx = next(f for f in iter_fixtures() if f.id == fixture_id)
    r = run_fixture(fixture_id, pack=pack)
    turns = [
        {
            "t": tr.t,
            "role": tr.role,
            "score": round(tr.score, 3),
            "state": tr.state,
            "observed_signals": list(tr.signals),
            "expected_signals": list(ft.expect_signals),
        }
        for tr, ft in zip(r.traces, fx.turns, strict=True)
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


def run_report(*, label: str | None = None, pack: PolicyPack | None = None) -> dict[str, Any]:
    the_pack = pack or load_pack("config/policy/default.yaml")
    ids = [fx.id for fx in iter_fixtures(label)]
    t = the_pack.thresholds
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "pack": f"{the_pack.metadata.tenant}@{the_pack.metadata.version}",
        "asr": "null",
        "filter_label": label,
        "thresholds": {"watch": t.watch, "alert": t.alert, "intervene": t.intervene},
        "items": [_item_report(i, the_pack) for i in ids],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m packages.eval.run")
    ap.add_argument("--all", action="store_true", help="every corpus item (the default)")
    ap.add_argument("--label", choices=["fraud", "benign"], default=None)
    ap.add_argument("--pack", type=Path, default=Path("config/policy/default.yaml"))
    ap.add_argument("--asr", choices=["null", "assemblyai"], default="null")
    ap.add_argument(
        "--fast", action="store_true", help="skip real-time pacing (already the default)"
    )
    ap.add_argument("--report", type=Path, default=Path("reports/latest.json"))
    args = ap.parse_args(argv)

    report = run_report(label=args.label, pack=load_pack(args.pack))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")

    items = report["items"]
    fraud = [i for i in items if i["label"] == "fraud"]
    benign = [i for i in items if i["label"] == "benign"]
    print(f"wrote {args.report}  ({len(items)} items: {len(fraud)} fraud, {len(benign)} benign)")
    for i in items:
        print(
            f"  {i['id']:36s} {i['label']:7s} {i['language']:5s} peak={i['peak_state']:9s} {i['peak_score']:6.1f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
