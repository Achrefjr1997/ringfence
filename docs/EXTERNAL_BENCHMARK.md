# External benchmark — Tier 1 vs a synthetic cross-domain scam corpus

**Status:** supplementary. Not a CI gate, not a policy target. It answers one
question — *do the eight hand-written fixture numbers generalise to scam
scripts Tier 1 was never tuned on?* — and the answer is **largely no**, which
is the motivation for the lexicon phrase-mining and the RAG judge knowledge
base (see the fraud base-knowledge plan).

## What was run

- **Corpus:** `BothBosu/multi-agent-scam-conversation`, vendored offline at
  `corpus/external/multi_agent_scam_conversation.jsonl.gz` — 1 600 **synthetic**
  (Llama-3-70B) English dialogues, balanced 800 scam / 800 non-scam, across
  eight scenarios. Full provenance in `corpus/external/PROVENANCE.md`.
- **Engine:** Tier 1 only — `packages.eval.harness.replay_fixture`, the exact
  offline path the eight fixtures and the invariant suite use. No judge, no
  ASR. `config/policy/default.yaml`, alert threshold 55.
- **Command:** `python -m packages.eval.run_external --report reports/external.json --markdown`

### Synthetic timeline

The upstream dialogues have no timestamps and their "turns" are LLM
paragraphs, not measured speech. `packages/eval/external.py` imposes a clock
so the 90 s evidence half-life and the combo windows behave. Two models:

- **words** (default): `dur = clip(words / 3.0, 1.5 s, 12 s)` + 1 s gap.
- **beat** (`--beat N`): every turn exactly `N` seconds.

Recall is sensitive to this choice — reported at three settings below — which
is itself a finding: on untimed text there is no single "true" latency.

## Results

| timeline | call recall (fraud → ALERT+) | FPR ALERT+ (benign) | FPR INTERVENE |
|---|---|---|---|
| words model (default) | **0.035** | 0.000 | 0.000 |
| beat = 8 s | 0.058 | 0.000 | 0.000 |
| beat = 5 s | 0.115 | 0.001 | 0.000 |

Per family (words model, 200 dialogues each):

| family | label | ALERT+ rate | INTERVENE rate | median peak score |
|---|---|---|---|---|
| `external/appointment` | benign | 0.0% | 0.0% | 0.0 |
| `external/delivery` | benign | 0.0% | 0.0% | 0.0 |
| `external/insurance` | benign | 0.0% | 0.0% | 0.0 |
| `external/wrong` | benign | 0.0% | 0.0% | 0.0 |
| `external/refund` | fraud | 1.5% | 0.0% | 27.4 |
| `external/reward` | fraud | 1.0% | 0.0% | 27.4 |
| `external/support` | fraud | 6.0% | 1.0% | 31.9 |
| `external/ssn` | fraud | 8.0% | 1.0% | 33.5 |

## Reading it

- **Recall collapses out of distribution.** The lexicons (`en/fr/ar_tn`,
  hand-tuned to bank-impersonation, gift-card, tech-support and
  family-emergency) fire on only a few percent of these dialogues hard enough
  to reach ALERT. Median fraud peak score sits at ~27–38, below WATCH (30) as
  often as not. The eight fixtures still pass — this is a coverage gap, not a
  regression.
- **Precision holds.** FPR is ~0 at every setting: when the lexicons do not
  match, they genuinely do not match, and no benign scenario is dragged up.
  Whatever we add for recall must not spend this.
- **Domain matters.** `ssn` (government impersonation) and `support`
  (tech-support) do best — they share `REMOTE_ACCESS` / `CALLBACK_SUPPRESS` /
  `VERIF_INVERT` vocabulary with existing fixtures. `refund` and `reward`
  (prize / sweepstakes) are almost invisible to Tier 1 today.
- Signals *do* fire per-turn (`VERIF_INVERT` ~1 000 times, `URGENCY` ~880),
  but spread thin across long turns they decay before they can stack. The
  `--markdown` per-signal table counts every fire as a "fp" only because this
  corpus has no per-turn signal ground truth (`expect_signals = []`); read
  that column as "fire count", not "false positive".

## Caveats

- Synthetic data. Llama-3-70B scam scripts are more uniform and more
  on-the-nose than real calls; treat the absolute numbers as a floor for
  "phrasing Tier 1 doesn't know", not a field estimate.
- English only. No open French / Derja fraud-call corpus exists; those stay
  on the hand-authored fixtures + recorded corpus.
- TTD is omitted — it is meaningless without per-turn signal ground truth and
  a real clock.

## Reproduce

```
python corpus/external/build_snapshot.py          # optional, re-fetch upstream
pytest -q -m needs_dataset                         # loader + smoke test
python -m packages.eval.run_external --markdown    # full run (~20 s)
python -m packages.eval.run_external --beat 8
```
