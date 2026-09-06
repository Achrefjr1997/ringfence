"""Weight-tuning harness (T-5.5).

Random search over signal weights and the state-machine thresholds against
the fixture corpus (``--asr=null``, deterministic).  Recall is the
objective; the FPR ceiling is a **hard constraint** — a candidate that
lets any benign call reach INTERVENE, aggregate or per-language, is simply
rejected, never traded off.

Writes a candidate pack.  It **never** auto-promotes it.
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from packages.eval.metrics import compute_metrics
from packages.eval.run import run_report
from packages.policy.pack import PolicyPack, load_pack

_LANG_FPR_CEILING = 0.007  # matches the release gate


@dataclass
class Trial:
    pack: PolicyPack
    recall: float
    ttd_median: float
    metrics: dict[str, Any]


def _perturb(
    base: PolicyPack, rng: random.Random, *, weight_jitter: float, thr_jitter: float
) -> PolicyPack | None:
    data = base.model_dump()
    for spec in data["signals"].values():
        spec["weight"] = round(
            spec["weight"] * rng.uniform(1 - weight_jitter, 1 + weight_jitter), 1
        )
    t = data["thresholds"]
    for key in ("watch", "alert", "intervene"):
        t[key] = round(t[key] + rng.uniform(-thr_jitter, thr_jitter), 1)
    if not (t["watch"] < t["alert"] < t["intervene"]):
        return None
    try:
        return PolicyPack.model_validate(data)
    except ValueError:
        return None


def _evaluate(pack: PolicyPack, *, fpr_ceiling: float) -> Trial | None:
    m = compute_metrics(run_report(pack=pack))
    agg = m["aggregate"]
    if (agg["fpr_intervene"] or 0.0) > fpr_ceiling:
        return None
    if any((s["fpr_intervene"] or 0.0) > _LANG_FPR_CEILING for s in m["per_language"].values()):
        return None
    return Trial(pack, agg["call_recall"] or 0.0, agg["ttd_median_s"] or 1e9, m)


def search(
    *,
    base_pack: PolicyPack | None = None,
    trials: int = 40,
    seed: int = 0,
    fpr_ceiling: float = 0.0,
    weight_jitter: float = 0.3,
    thr_jitter: float = 8.0,
) -> Trial:
    base = base_pack or load_pack("config/policy/default.yaml")
    rng = random.Random(seed)

    best = _evaluate(base, fpr_ceiling=fpr_ceiling)
    if best is None:
        raise RuntimeError("the base pack itself violates the FPR ceiling")

    for _ in range(trials):
        cand = _perturb(base, rng, weight_jitter=weight_jitter, thr_jitter=thr_jitter)
        if cand is None:
            continue
        t = _evaluate(cand, fpr_ceiling=fpr_ceiling)
        if t is None:
            continue
        if (t.recall, -t.ttd_median) > (best.recall, -best.ttd_median):
            best = t
    return best


def dump_pack(pack: PolicyPack, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = pack.model_dump(mode="json", exclude_none=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m packages.eval.tune")
    ap.add_argument("--base", type=Path, default=Path("config/policy/default.yaml"))
    ap.add_argument("--out", type=Path, default=Path("config/policy/candidate.yaml"))
    ap.add_argument("--trials", type=int, default=40)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--fpr-ceiling", type=float, default=0.0)
    args = ap.parse_args(argv)

    best = search(
        base_pack=load_pack(args.base),
        trials=args.trials,
        seed=args.seed,
        fpr_ceiling=args.fpr_ceiling,
    )
    dump_pack(best.pack, args.out)
    agg = best.metrics["aggregate"]
    print(f"wrote candidate {args.out} (NOT promoted)")
    print(
        f"  recall {agg['call_recall']}  fpr {agg['fpr_intervene']}  ttd_median {agg['ttd_median_s']}s"
    )
    print(
        f"  thresholds {best.pack.thresholds.watch}/{best.pack.thresholds.alert}/{best.pack.thresholds.intervene}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
