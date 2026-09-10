"""Release gate (T-5.4, production §11.5).

    python -m packages.eval.gate --baseline reports/baseline.json --candidate reports/latest.json

Exit non-zero on regression:

    recall_new              >= recall_base - 0.5pp
    fpr_intervene_new       <= min(fpr_base, 0.5%)
    fpr_alert_new           <= min(fpr_alert_base, 1.0%)
    benign_margin_new       >= benign_margin_base - 1.0 point
    per_language_recall_new >= per_language_recall_base - 2pp   # every language
    per_language_fpr_new    <= 0.7%                             # every language

The benign-margin row is the one that earns its keep.  On the external
corpus, headline FPR is 0.000 at every threshold -- and yet one benign call
peaks at 68.1, thirteen points *above* the ALERT line, held below ALERT only
by the ``sustain_turns: 2`` hysteresis rule, with 164 of 800 benign calls
scoring above zero.  FPR counts threshold crossings; it cannot see a corpus
creeping up to the line.  Gate the headroom too, so the next recall change
has to declare what it costs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from packages.eval.metrics import compute_metrics

_RECALL_DROP = 0.005  # 0.5 pp
_FPR_CEILING = 0.005  # 0.5 %
_FPR_ALERT_CEILING = 0.01  # 1.0 % -- an ALERT on a benign call is a real warning
_MARGIN_DROP = 1.0  # score points of headroom we will give away before failing
_LANG_RECALL_DROP = 0.02  # 2 pp
_LANG_FPR_CEILING = 0.007  # 0.7 %


def check_gate(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    """Return a list of gate failures; empty means the candidate ships."""
    fails: list[str] = []
    ba, ca = baseline["aggregate"], candidate["aggregate"]

    b_recall, c_recall = ba["call_recall"], ca["call_recall"]
    if b_recall is not None and c_recall is not None and c_recall < b_recall - _RECALL_DROP:
        fails.append(f"recall {c_recall:.4f} < baseline {b_recall:.4f} - {_RECALL_DROP} (0.5pp)")

    b_fpr, c_fpr = ba["fpr_intervene"], ca["fpr_intervene"]
    if c_fpr is not None:
        ceiling = min(b_fpr, _FPR_CEILING) if b_fpr is not None else _FPR_CEILING
        if c_fpr > ceiling:
            fails.append(f"FPR {c_fpr:.4f} > min(baseline, 0.5%) = {ceiling:.4f}")

    b_alert, c_alert = ba["fpr_alert"], ca["fpr_alert"]
    if c_alert is not None:
        ceiling = min(b_alert, _FPR_ALERT_CEILING) if b_alert is not None else _FPR_ALERT_CEILING
        if c_alert > ceiling:
            fails.append(f"FPR ALERT+ {c_alert:.4f} > min(baseline, 1.0%) = {ceiling:.4f}")

    b_margin, c_margin = ba["benign_peak_margin"], ca["benign_peak_margin"]
    if b_margin is not None and c_margin is not None and c_margin < b_margin - _MARGIN_DROP:
        fails.append(
            f"benign peak margin {c_margin:.2f} < baseline {b_margin:.2f} - {_MARGIN_DROP} "
            f"(headroom to ALERT shrank by {b_margin - c_margin:.2f} points)"
        )

    for lang, cl in candidate["per_language"].items():
        bl = baseline["per_language"].get(lang)
        if (
            bl is not None
            and bl["call_recall"] is not None
            and cl["call_recall"] is not None
            and cl["call_recall"] < bl["call_recall"] - _LANG_RECALL_DROP
        ):
            fails.append(
                f"{lang}: recall {cl['call_recall']:.4f} < baseline "
                f"{bl['call_recall']:.4f} - {_LANG_RECALL_DROP} (2pp)"
            )
        if cl["fpr_intervene"] is not None and cl["fpr_intervene"] > _LANG_FPR_CEILING:
            fails.append(f"{lang}: FPR {cl['fpr_intervene']:.4f} > {_LANG_FPR_CEILING} (0.7%)")

    return fails


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m packages.eval.gate")
    ap.add_argument("--baseline", type=Path, required=True)
    ap.add_argument("--candidate", type=Path, required=True)
    args = ap.parse_args(argv)

    baseline = compute_metrics(json.loads(args.baseline.read_text(encoding="utf-8")))
    candidate = compute_metrics(json.loads(args.candidate.read_text(encoding="utf-8")))
    fails = check_gate(baseline, candidate)

    if not fails:
        print("GATE PASS")
        return 0
    print("GATE FAIL")
    for f in fails:
        print(f"  - {f}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
