"""Mixed-path role accuracy — the headline number for §6.3 (T-6.3).

Two-leg mode has exact roles by construction (one ASR socket per leg), so
it is the ground truth.  This measures the **mixed single-stream** path:
build synthetic mixed audio from a fixture (CALLER turns telephone-band,
CALLEE turns full-band, on the fixture's timeline), run it through
:class:`AcousticRoleClassifier`, and score its per-turn guesses against
the fixture's true roles.

    python -m packages.eval.role_eval [--markdown]
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import numpy as np
import numpy.typing as npt
from scipy.signal import butter, sosfiltfilt

from packages.eval.fixtures import Fixture, iter_fixtures
from packages.media.acoustic_role import AcousticRoleClassifier

_RATE = 16_000


def _voice(
    seconds: float, *, bandlimited: bool, rng: np.random.Generator
) -> npt.NDArray[np.float64]:
    n = int(_RATE * seconds)
    t = np.arange(n) / _RATE
    sig = np.zeros(n)
    for f in range(150, 7900, 130):
        sig += (1.0 / (1 + f / 400)) * np.sin(2 * np.pi * f * t + rng.uniform(0, 2 * np.pi))
    sig += 0.05 * rng.standard_normal(n)
    if bandlimited:
        sig = sosfiltfilt(butter(8, 3400, btype="low", fs=_RATE, output="sos"), sig)
    rms = np.sqrt(np.mean(sig**2)) or 1.0
    return (sig * (8000.0 / rms)).astype(np.int16).astype(np.float64)


def synth_mixed_audio(fx: Fixture, *, seed: int = 0) -> npt.NDArray[np.int16]:
    """One int16 mono stream: each turn voiced on its timeline, CALLER
    telephone-band, everyone else full-band, silence in the gaps."""
    rng = np.random.default_rng(seed)
    end = max((t.t_end for t in fx.turns), default=1.0)
    out = np.zeros(int(_RATE * (end + 0.5)))
    for turn in fx.turns:
        seg = _voice(turn.t_end - turn.t_start, bandlimited=turn.role == "CALLER", rng=rng)
        a = int(_RATE * turn.t_start)
        out[a : a + len(seg)] = seg
    return out.astype(np.int16)


def evaluate_fixture(fx: Fixture, *, seed: int = 0) -> dict[str, Any]:
    clf = AcousticRoleClassifier()
    clf.observe(synth_mixed_audio(fx, seed=seed).tobytes())

    total = confident = correct = 0
    for turn in fx.turns:
        total += 1
        g = clf.classify(turn.t_start + 0.2, max(turn.t_start + 0.3, turn.t_end - 0.2))
        if g.role == "UNKNOWN":
            continue
        confident += 1
        correct += int(g.role == turn.role)
    return {
        "id": fx.id,
        "language": fx.language,
        "calibrated": clf.calibrated,
        "turns": total,
        "confident": confident,
        "correct": correct,
        "accuracy": round(correct / confident, 4) if confident else None,
        "coverage": round(confident / total, 4) if total else None,
    }


def _slice(rows: list[dict[str, Any]]) -> dict[str, Any]:
    conf = sum(r["confident"] for r in rows)
    corr = sum(r["correct"] for r in rows)
    turns = sum(r["turns"] for r in rows)
    return {
        "items": len(rows),
        "role_accuracy": round(corr / conf, 4) if conf else None,
        "coverage": round(conf / turns, 4) if turns else None,
        "unknown_rate": round(1 - conf / turns, 4) if turns else None,
    }


def run(*, seed: int = 0) -> dict[str, Any]:
    rows = [evaluate_fixture(fx, seed=seed) for fx in iter_fixtures()]
    langs = sorted({r["language"] for r in rows})
    return {
        "aggregate": _slice(rows),
        "per_language": {lg: _slice([r for r in rows if r["language"] == lg]) for lg in langs},
        "items": rows,
    }


def render_markdown(m: dict[str, Any]) -> str:
    langs = list(m["per_language"])
    lines = ["# Mixed-path role accuracy (two-leg = ground truth)", ""]
    lines += [
        "| slice | items | role accuracy | coverage | unknown rate |",
        "|---|---|---|---|---|",
    ]
    a = m["aggregate"]
    lines.append(
        f"| aggregate | {a['items']} | {a['role_accuracy']} | {a['coverage']} | {a['unknown_rate']} |"
    )
    for lg in langs:
        s = m["per_language"][lg]
        lines.append(
            f"| {lg} | {s['items']} | {s['role_accuracy']} | {s['coverage']} | {s['unknown_rate']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m packages.eval.role_eval")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    m = run(seed=args.seed)
    print(render_markdown(m) if args.markdown else json.dumps(m, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
