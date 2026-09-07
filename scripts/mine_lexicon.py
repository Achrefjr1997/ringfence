"""Phrase-mine English lexicon candidates from scam vs benign call speech.

Offline dev tool — **not shipped, not imported by anything**.  It only
*surfaces candidates*; a human decides what (if anything) lands in
``packages/risk/lexicons/en.yaml``.  One over-broad term breaks invariant
#2 (see review note R-A), so nothing here is auto-applied.

    python scripts/mine_lexicon.py --out reports/lexicon_candidates.md

Sources (fetched at run time, cached under a temp dir):
  * ASsET ``clean_spam`` — 5 real scambaiter transcripts
    (github.com/abideenml/Detecting-SocialEngineering-Attacks)
  * ASsET ``clean_non_spam`` — real generic phone-call transcripts (benign
    baseline; a sample)
  * BothBosu ``multi-agent-scam-conversation`` — 1600 synthetic dialogues,
    CALLER (``Suspect``) turns split by label (bulk scam + bulk benign)

Method: rank word n-grams (1–4) by
``P(gram | scam CALLER) / P(gram | benign CALLER)`` with a raw-count and a
document-count floor; drop anything the current ``en.yaml`` already covers
(Aho-Corasick is substring, so substring either way counts as covered);
bucket each survivor under every existing signal it shares a content word
with.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import tempfile
import urllib.request
from collections import Counter
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[1]
_EN_YAML = _REPO / "packages" / "risk" / "lexicons" / "en.yaml"

_ASSET_API = "https://api.github.com/repos/abideenml/Detecting-SocialEngineering-Attacks/contents"
_BOTHBOSU = (
    "https://huggingface.co/datasets/BothBosu/multi-agent-scam-conversation/"
    "resolve/709db2b6c37f424c3070f29138abb33971e21ab9"
)
_SPEAKER = re.compile(r"\s*(Suspect|Innocent):\s*")
_WORD = re.compile(r"[a-z0-9']+")
_STOP = frozenset(
    """
    a an the this that these those is are was were be been being am to of in on at for and or
    but if so as it its it's you your yours i me my we us our they them their he she his her
    do does did have has had will would can could should may might must not no n't yes ok okay
    with from by about into out up down over under again then there here what which who whom
    when where why how all any both each few more most other some such only own same than too
    very s t just now get got go going one two also please thank thanks hello hi hey yeah
    """.split()
)


def _fetch(url: str, cache: Path) -> bytes:
    key = re.sub(r"[^A-Za-z0-9._-]", "_", url)[-180:]
    hit = cache / key
    if hit.exists():
        return hit.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": "ringfence-mine-lexicon"})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 - fixed hosts
        raw: bytes = resp.read()
    hit.write_bytes(raw)
    return raw


def _norm(text: str) -> list[str]:
    return _WORD.findall(text.lower().replace("’", "'"))


def _ngrams(tokens: list[str], nmax: int = 4) -> list[str]:
    out: list[str] = []
    for n in range(1, nmax + 1):
        for i in range(len(tokens) - n + 1):
            gram = tokens[i : i + n]
            if n == 1 and gram[0] in _STOP:
                continue
            if gram[0] in _STOP or gram[-1] in _STOP:  # no leading/trailing stopword
                continue
            out.append(" ".join(gram))
    return out


# --------------------------------------------------------------------------
# corpora
# --------------------------------------------------------------------------


def _asset_turns(cache: Path, path: str, limit: int | None = None) -> list[str]:
    listing = json.loads(_fetch(f"{_ASSET_API}/{path}", cache))
    turns: list[str] = []
    files = [f for f in listing if f["name"].endswith(".csv")]
    for f in files[:limit] if limit else files:
        raw = _fetch(f["download_url"], cache).decode("utf-8", "replace")
        for row in csv.reader(io.StringIO(raw)):
            if len(row) >= 2 and len(row[1].split()) >= 3:
                turns.append(row[1])
    return turns


def _bothbosu_caller_turns(cache: Path) -> tuple[list[str], list[str]]:
    scam: list[str] = []
    benign: list[str] = []
    for split in ("train", "test"):
        raw = _fetch(f"{_BOTHBOSU}/agent_conversation_{split}.csv", cache).decode("utf-8")
        for row in csv.DictReader(io.StringIO(raw)):
            parts = _SPEAKER.split(row["dialogue"].strip())
            caller = [t.strip() for spk, t in zip(parts[1::2], parts[2::2]) if spk == "Suspect"]
            (scam if row["labels"].strip() == "1" else benign).extend(caller)
    return scam, benign


# --------------------------------------------------------------------------
# mining
# --------------------------------------------------------------------------


def _counts(turns: list[str]) -> tuple[Counter[str], Counter[str], int]:
    tf: Counter[str] = Counter()
    df: Counter[str] = Counter()
    for t in turns:
        grams = _ngrams(_norm(t))
        tf.update(grams)
        df.update(set(grams))
    return tf, df, len(turns)


def _load_signal_terms() -> dict[str, list[str]]:
    data = yaml.safe_load(_EN_YAML.read_text(encoding="utf-8")) or {}
    return {str(sig): [str(t) for t in terms] for sig, terms in data.items()}


def _covered(gram: str, all_terms: list[str]) -> bool:
    return any(gram in term or term in gram for term in all_terms)


def _content(gram: str) -> set[str]:
    return {w for w in gram.split() if w not in _STOP}


def mine(args: argparse.Namespace) -> str:
    cache = Path(args.cache or tempfile.mkdtemp(prefix="mine_lexicon_"))
    cache.mkdir(parents=True, exist_ok=True)
    print(f"cache: {cache}", file=sys.stderr)

    scam_turns = _asset_turns(cache, "clean_spam")
    benign_turns = _asset_turns(cache, "clean_non_spam", limit=args.benign_baseline_files)
    bb_scam, bb_benign = _bothbosu_caller_turns(cache)
    scam_turns += bb_scam
    benign_turns += bb_benign
    print(
        f"scam CALLER turns: {len(scam_turns)}  benign CALLER turns: {len(benign_turns)}",
        file=sys.stderr,
    )

    s_tf, s_df, s_n = _counts(scam_turns)
    b_tf, b_df, b_n = _counts(benign_turns)
    eps = 1.0 / max(b_n, 1)

    signal_terms = _load_signal_terms()
    all_terms = [t for terms in signal_terms.values() for t in terms]

    scored: list[tuple[float, str, int, int, float]] = []
    for gram, sc in s_tf.items():
        if sc < args.min_support or s_df[gram] < args.min_docs:
            continue
        if _covered(gram, all_terms):
            continue
        p_scam = sc / s_n
        p_benign = b_tf.get(gram, 0) / b_n if b_n else 0.0
        ratio = (p_scam + eps) / (p_benign + eps)
        if ratio < args.ratio:
            continue
        scored.append((ratio, gram, sc, s_df[gram], p_benign))
    scored.sort(key=lambda r: (-r[0], -r[2]))

    buckets: dict[str, list[tuple[float, str, int, int, float]]] = {s: [] for s in signal_terms}
    unbucketed: list[tuple[float, str, int, int, float]] = []
    for row in scored:
        gram = row[1]
        gtok = _content(gram)
        placed = False
        for sig, terms in signal_terms.items():
            if any(gtok & _content(term) for term in terms):
                buckets[sig].append(row)
                placed = True
        if not placed:
            unbucketed.append(row)

    return _render(buckets, unbucketed, scam_n=s_n, benign_n=b_n, top=args.top)


def _render(
    buckets: dict[str, list[tuple[float, str, int, int, float]]],
    unbucketed: list[tuple[float, str, int, int, float]],
    *,
    scam_n: int,
    benign_n: int,
    top: int,
) -> str:
    out = [
        "# English lexicon candidates (mined, unreviewed)",
        "",
        f"scam CALLER turns: {scam_n} · benign CALLER turns: {benign_n}",
        "",
        "`ratio` = P(gram|scam) / P(gram|benign); `sc` = raw scam count; "
        "`docs` = scam turns containing it; `p_benign` = benign probability.",
        "",
        "Candidates already covered by an `en.yaml` term (substring either way) "
        "are removed. **Nothing here is validated — review against the corpus, "
        "not against a fixture, before adding.**",
        "",
    ]
    for sig, rows in buckets.items():
        if not rows:
            continue
        out += [
            f"## {sig}",
            "",
            "| ratio | sc | docs | p_benign | candidate |",
            "|--:|--:|--:|--:|---|",
        ]
        for ratio, gram, sc, docs, p_benign in rows[:top]:
            out.append(f"| {ratio:.1f} | {sc} | {docs} | {p_benign:.4f} | `{gram}` |")
        out.append("")
    if unbucketed:
        out += [
            "## (unbucketed — no shared word with any current signal)",
            "",
            "| ratio | sc | docs | p_benign | candidate |",
            "|--:|--:|--:|--:|---|",
        ]
        for ratio, gram, sc, docs, p_benign in unbucketed[:top]:
            out.append(f"| {ratio:.1f} | {sc} | {docs} | {p_benign:.4f} | `{gram}` |")
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python scripts/mine_lexicon.py")
    ap.add_argument("--out", type=Path, help="write the markdown report here (default: stdout)")
    ap.add_argument("--cache", type=Path, help="download cache dir (default: a fresh temp dir)")
    ap.add_argument("--min-support", type=int, default=4, help="min raw scam count")
    ap.add_argument("--min-docs", type=int, default=3, help="min scam turns containing the gram")
    ap.add_argument("--ratio", type=float, default=3.0, help="min scam/benign probability ratio")
    ap.add_argument("--top", type=int, default=25, help="max candidates listed per signal")
    ap.add_argument("--benign-baseline-files", type=int, default=80, help="ASsET non-spam files")
    args = ap.parse_args(argv)

    report = mine(args)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report + "\n", encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
