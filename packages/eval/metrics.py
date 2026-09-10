"""Evaluation metrics (T-5.3, MVP §13.1).

    python -m packages.eval.metrics reports/latest.json --markdown

Everything is reported **per language as well as aggregate** — an
aggregate number hides a shield that works in French and fails in Derja.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

Report = dict[str, Any]
Item = dict[str, Any]


def _first_signal_t(item: Item) -> float | None:
    ts = [t["t"] for t in item["turns"] if t["expected_signals"]]
    return min(ts) if ts else None


def _slice_metrics(items: list[Item], alert: float) -> dict[str, Any]:
    fraud = [i for i in items if i["label"] == "fraud"]
    benign = [i for i in items if i["label"] == "benign"]

    ttds = [
        i["first_alert_t"] - fs
        for i in fraud
        if i["first_alert_t"] is not None and (fs := _first_signal_t(i)) is not None
    ]
    in_time = [
        i
        for i in fraud
        if i["first_alert_t"] is not None
        and i["transfer_request_t"] is not None
        and i["first_alert_t"] <= i["transfer_request_t"]
    ]
    benign_peaks = [i["peak_score"] for i in benign]

    turns = [t for i in items for t in i["turns"]]
    role_ok = sum(1 for t in turns if t.get("role") == t.get("expected_role", t.get("role")))

    return {
        "n_fraud": len(fraud),
        "n_benign": len(benign),
        "ttd_median_s": round(statistics.median(ttds), 2) if ttds else None,
        "ttd_p90_s": round(_pct(ttds, 90), 2) if ttds else None,
        "call_recall": round(len(in_time) / len(fraud), 4) if fraud else None,
        "fpr_intervene": round(
            sum(1 for i in benign if i["peak_state"] == "INTERVENE") / len(benign), 4
        )
        if benign
        else None,
        "fpr_alert": round(
            sum(1 for i in benign if i["peak_state"] in ("ALERT", "INTERVENE")) / len(benign), 4
        )
        if benign
        else None,
        "benign_peak_margin": round(alert - max(benign_peaks), 2) if benign_peaks else None,
        # How many benign calls sit ABOVE the alert line without tripping it.
        # FPR cannot see these -- hysteresis (sustain_turns) is all that holds
        # them down -- and neither can the margin, which only tracks the max.
        "benign_over_alert": sum(1 for p in benign_peaks if p > alert) if benign_peaks else None,
        "role_accuracy": round(role_ok / len(turns), 4) if turns else None,
    }


def _pct(xs: list[float], p: float) -> float:
    s = sorted(xs)
    k = min(len(s) - 1, int(round((p / 100) * (len(s) - 1))))
    return s[k]


def _per_signal(items: list[Item]) -> dict[str, dict[str, float]]:
    tp: dict[str, int] = {}
    fp: dict[str, int] = {}
    fn: dict[str, int] = {}
    for item in items:
        for turn in item["turns"]:
            obs, exp = set(turn["observed_signals"]), set(turn["expected_signals"])
            for s in obs & exp:
                tp[s] = tp.get(s, 0) + 1
            for s in obs - exp:
                fp[s] = fp.get(s, 0) + 1
            for s in exp - obs:
                fn[s] = fn.get(s, 0) + 1
    out: dict[str, dict[str, float]] = {}
    for s in sorted(set(tp) | set(fp) | set(fn)):
        t, f, n = tp.get(s, 0), fp.get(s, 0), fn.get(s, 0)
        prec = t / (t + f) if t + f else 0.0
        rec = t / (t + n) if t + n else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        out[s] = {
            "tp": t,
            "fp": f,
            "fn": n,
            "precision": round(prec, 3),
            "recall": round(rec, 3),
            "f1": round(f1, 3),
        }
    return out


def compute_metrics(report: Report) -> dict[str, Any]:
    items: list[Item] = report["items"]
    alert = float(report.get("thresholds", {}).get("alert", 55.0))
    langs = sorted({i["language"] for i in items})
    return {
        "pack": report.get("pack"),
        "alert_threshold": alert,
        "aggregate": _slice_metrics(items, alert),
        "per_language": {
            lang: _slice_metrics([i for i in items if i["language"] == lang], alert)
            for lang in langs
        },
        "per_signal": _per_signal(items),
    }


# --------------------------------------------------------------------------


_HEADLINE = [
    ("ttd_median_s", "TTD median (s)"),
    ("ttd_p90_s", "TTD p90 (s)"),
    ("call_recall", "call recall"),
    ("fpr_intervene", "FPR (INTERVENE)"),
    ("fpr_alert", "FPR (ALERT+)"),
    ("benign_peak_margin", "benign peak margin"),
    ("role_accuracy", "role accuracy"),
]


def render_markdown(m: dict[str, Any]) -> str:
    lines = [f"# Evaluation — pack `{m['pack']}` (alert threshold {m['alert_threshold']:g})", ""]

    langs = list(m["per_language"])
    lines += ["## Headline", "", "| metric | aggregate | " + " | ".join(langs) + " |"]
    lines.append("|" + "---|" * (2 + len(langs)))
    for key, label in _HEADLINE:
        row = [str(m["aggregate"][key])] + [str(m["per_language"][lg][key]) for lg in langs]
        lines.append(f"| {label} | " + " | ".join(row) + " |")

    lines += ["", "## Counts", "", "| slice | fraud | benign |", "|---|---|---|"]
    lines.append(f"| aggregate | {m['aggregate']['n_fraud']} | {m['aggregate']['n_benign']} |")
    for lang in langs:
        s = m["per_language"][lang]
        lines.append(f"| {lang} | {s['n_fraud']} | {s['n_benign']} |")

    lines += [
        "",
        "## Per-signal precision / recall",
        "",
        "| signal | tp | fp | fn | precision | recall | f1 |",
        "|---|---|---|---|---|---|---|",
    ]
    for sig, s in m["per_signal"].items():
        lines.append(
            f"| {sig} | {s['tp']} | {s['fp']} | {s['fn']} | {s['precision']} | {s['recall']} | {s['f1']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m packages.eval.metrics")
    ap.add_argument("report", type=Path)
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args(argv)

    metrics = compute_metrics(json.loads(args.report.read_text(encoding="utf-8")))
    print(render_markdown(metrics) if args.markdown else json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
