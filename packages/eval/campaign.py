"""Campaign correlator (T-6.5).

A scam campaign runs the *same script* across many calls.  Each call emits
a **fingerprint** — the set of CALLER signals it fired, plus language —
and an **offline pass** clusters fingerprints by Jaccard similarity
(single linkage) into campaigns with a shared signal core.

    python -m packages.eval.campaign reports/latest.json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packages.intervene.cases import Case


@dataclass(frozen=True, slots=True)
class Fingerprint:
    session_id: str
    tenant_id: str
    language: str
    signals: frozenset[str]
    peak_state: str


@dataclass
class Campaign:
    id: str
    members: list[str]
    core_signals: frozenset[str]
    languages: frozenset[str]


def fingerprint_case(case: Case, *, tenant_id: str, language: str = "unknown") -> Fingerprint:
    signals = frozenset(
        c.id
        for d in case.decisions
        for c in d.contributions
        if c.source == "signal" and c.role in ("CALLER", None)
    )
    return Fingerprint(case.session_id, tenant_id, language, signals, case.peak_state)


def fingerprint_report_item(item: dict[str, Any], *, tenant_id: str = "eval") -> Fingerprint:
    signals = frozenset(
        s for t in item["turns"] if t.get("role") in ("CALLER", None) for s in t["observed_signals"]
    )
    return Fingerprint(item["id"], tenant_id, item["language"], signals, item["peak_state"])


def similarity(a: Fingerprint, b: Fingerprint) -> float:
    if a.language != b.language or not (a.signals | b.signals):
        return 0.0
    return len(a.signals & b.signals) / len(a.signals | b.signals)


def correlate(fingerprints: list[Fingerprint], *, min_similarity: float = 0.6) -> list[Campaign]:
    n = len(fingerprints)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if similarity(fingerprints[i], fingerprints[j]) >= min_similarity:
                parent[find(i)] = find(j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    campaigns: list[Campaign] = []
    for k, (_, idxs) in enumerate(sorted(groups.items())):
        if len(idxs) < 2:
            continue  # a campaign needs at least two correlated calls
        fps = [fingerprints[i] for i in idxs]
        core = frozenset.intersection(*(f.signals for f in fps))
        campaigns.append(
            Campaign(
                id=f"campaign_{k:03d}",
                members=sorted(f.session_id for f in fps),
                core_signals=core,
                languages=frozenset(f.language for f in fps),
            )
        )
    return campaigns


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m packages.eval.campaign")
    ap.add_argument("report", type=Path)
    ap.add_argument("--min-similarity", type=float, default=0.6)
    args = ap.parse_args(argv)

    report = json.loads(args.report.read_text(encoding="utf-8"))
    fps = [fingerprint_report_item(i) for i in report["items"] if i["label"] == "fraud"]
    campaigns = correlate(fps, min_similarity=args.min_similarity)
    print(
        json.dumps(
            {
                "fingerprints": len(fps),
                "campaigns": [
                    {
                        "id": c.id,
                        "members": c.members,
                        "core_signals": sorted(c.core_signals),
                        "languages": sorted(c.languages),
                    }
                    for c in campaigns
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
