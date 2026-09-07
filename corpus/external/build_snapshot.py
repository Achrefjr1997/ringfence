"""Regenerate ``multi_agent_scam_conversation.jsonl`` from the upstream CSVs.

One-off, network-dependent, not imported by anything.  Kept in the tree so
the vendored snapshot is reproducible.

    python corpus/external/build_snapshot.py

Source: https://huggingface.co/datasets/BothBosu/multi-agent-scam-conversation
        commit 709db2b6c37f424c3070f29138abb33971e21ab9
        files  agent_conversation_{train,test}.csv

The upstream ``dialogue`` field is a single string with turns run together
and prefixed ``Innocent:`` (the called party) / ``Suspect:`` (the caller).
We segment on those prefixes, map ``Suspect -> CALLER`` and
``Innocent -> CALLEE``, and write one JSON object per dialogue:

    {"id", "split", "label", "source_type", "turns": [{"role", "text"}, ...]}

No timings are stored -- ``packages.eval.external`` synthesises them at load
time from a documented speaking-rate model.  ``labels`` 1 -> ``fraud``,
0 -> ``benign``.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
import sys
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent / "multi_agent_scam_conversation.jsonl.gz"
_BASE = (
    "https://huggingface.co/datasets/BothBosu/multi-agent-scam-conversation/"
    "resolve/709db2b6c37f424c3070f29138abb33971e21ab9"
)
_SPLIT_FILE = {"train": "agent_conversation_train.csv", "test": "agent_conversation_test.csv"}
_ROLE = {"Suspect": "CALLER", "Innocent": "CALLEE"}
_SPEAKER = re.compile(r"\s*(Suspect|Innocent):\s*")
_LABEL = {"0": "benign", "1": "fraud"}


def segment(dialogue: str) -> list[dict[str, str]]:
    """``"Innocent: Hi  Suspect: Hello"`` -> role/text turns, prefixes stripped."""
    parts = _SPEAKER.split(dialogue.strip())
    # parts == ["", speaker, text, speaker, text, ...] (leading "" if it starts
    # with a prefix, which every row does).
    turns: list[dict[str, str]] = []
    for speaker, text in zip(parts[1::2], parts[2::2], strict=False):
        body = text.strip()
        if body:
            turns.append({"role": _ROLE[speaker], "text": body})
    return turns


def _fetch_csv(split: str) -> list[dict[str, str]]:
    url = f"{_BASE}/{_SPLIT_FILE[split]}"
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 - fixed https host
        raw = resp.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(raw)))


def main() -> int:
    records: list[dict[str, object]] = []
    for split in ("train", "test"):
        rows = _fetch_csv(split)
        for i, row in enumerate(rows):
            turns = segment(row["dialogue"])
            if len(turns) < 2:
                continue
            records.append(
                {
                    "id": f"mas_{split}_{i:04d}",
                    "split": split,
                    "label": _LABEL[row["labels"].strip()],
                    "source_type": row["type"].strip(),
                    "turns": turns,
                }
            )
    body = "".join(json.dumps(rec, ensure_ascii=False) + "\n" for rec in records)
    # mtime=0 so the gzip bytes (and their sha256 in PROVENANCE.md) are
    # reproducible across runs.
    with gzip.GzipFile(filename=str(OUT), mode="wb", mtime=0) as fh:
        fh.write(body.encode("utf-8"))
    fraud = sum(1 for r in records if r["label"] == "fraud")
    print(f"wrote {OUT}  ({len(records)} dialogues: {fraud} fraud, {len(records) - fraud} benign)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
