# External benchmark — Tier 1 vs a synthetic cross-domain scam corpus

**Status:** supplementary. Not a CI gate, not a policy target. It answers one
question — *do the eight hand-written fixture numbers generalise to scam
scripts Tier 1 was never tuned on?*

**Last re-run: 2026-09-10** against `main`. The previous version of this file
reported the 2026-09-07 numbers (recall 0.035) and was never refreshed after
`d7acf11` added 11 mined English terms the next day — that commit's own message
says *"OOD re-check pending the external-benchmark branch."* This is that
re-check, and it changes the conclusion in both directions.

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

## Results (2026-09-10, words model)

| metric | value | was 2026-09-07 |
|---|---|---|
| call recall (fraud → ALERT+) | **0.2725** | 0.035 |
| FPR ALERT+ (benign) | 0.000 | 0.000 |
| FPR INTERVENE (benign) | 0.000 | 0.000 |
| **benign peak-score margin to ALERT** | **−13.09** | +55.00 |

Per family (200 dialogues each):

| family | label | ALERT+ | INTERVENE | median peak | was ALERT+ |
|---|---|---|---|---|---|
| `external/appointment` | benign | 0.0% | 0.0% | 0.0 | 0.0% |
| `external/delivery` | benign | 0.0% | 0.0% | 0.0 | 0.0% |
| `external/insurance` | benign | 0.0% | 0.0% | 0.0 | 0.0% |
| `external/wrong` | benign | 0.0% | 0.0% | 0.0 | 0.0% |
| `external/refund` | fraud | 1.5% | 0.0% | 27.4 | 1.5% |
| `external/reward` | fraud | 1.5% | 0.0% | 27.4 | 1.0% |
| `external/ssn` | fraud | **99.0%** | **95.5%** | 86.4 | 8.0% |
| `external/support` | fraud | 13.5% | 1.5% | 36.2 | 6.0% |

## Reading it

- **The 11 mined terms were extremely effective — on one family.** Recall went
  7.8× overall, but essentially all of it is `ssn`, which went 8% → 99% ALERT+
  and 0% → 95.5% INTERVENE on the back of `+confirm your social security
  number` and `+social security administration`. That is memorisation of the
  family's two defining phrases, not generalisation.
- **`refund` and `reward` did not move** (1.5% each). They remain invisible to
  Tier 1, for the reason `docs/LEXICON_MINING.md` records: their giveaway
  phrases (`sweepstakes`, `processing fee`, `you've been selected`) were
  *rejected* during mining because RingFence has **no pretext signal** to hang
  them on.
- **The precision headroom is nearly gone, and nothing measured it.** The old
  file said *"Precision holds… Whatever we add for recall must not spend this."*
  The very next commit spent most of it:

  | benign peak score | items (of 800) |
  |---|---|
  | 0 | 636 |
  | 10–29 | 23 |
  | ~30 (WATCH) | 129 |
  | 40–59 | 11 |
  | **68.1** | **1** |

  164 benign calls now score above zero where previously **all 800 peaked at
  0.0**. The worst (`mas_train_0051`, an *appointment* call) reaches **68.1** —
  13 points above the ALERT threshold — and is held at WATCH only by the
  `sustain_turns: 2` hysteresis rule. Headline FPR is still 0.000, but it is now
  one unlucky turn away from not being.
- **`VERIF_INVERT` is the precision liability.** It fires **200 times across
  benign calls** — more than every other signal combined (`RAIL_UNUSUAL` 87,
  `URGENCY` 56, `OFFER_CALLBACK` 14, `CALLBACK_SUPPRESS` 4) — and carries the
  largest weight in the pack (+30). Benign drift is concentrated in
  `insurance` (67 items), `delivery` (56) and `appointment` (39).

**The lesson for the next recall change:** single high-weight lexical terms buy
recall on the family that uses them and spend precision everywhere. Recall that
generalises has to come from *corroboration* — several distinct signals, or the
same signal pressed repeatedly — which is what `ESCALATION` and the combos are
for. `ESCALATION` is currently declared in the pack and emitted by nothing.

## Caveats

- Synthetic data. Llama-3-70B scam scripts are more uniform and more
  on-the-nose than real calls; treat absolute numbers as a floor for "phrasing
  Tier 1 doesn't know", not a field estimate.
- English only. No open French / Derja fraud-call corpus exists.
- TTD is omitted — meaningless without per-turn signal ground truth and a real
  clock.
- Recall is sensitive to the timeline model (`--beat 8`, `--beat 5`); re-run
  those before drawing latency conclusions.

## Reproduce

```
python corpus/external/build_snapshot.py          # optional, re-fetch upstream
pytest -q -m needs_dataset                         # loader + smoke test
python -m packages.eval.run_external --markdown    # full run (~20 s)
python -m packages.eval.run_external --beat 8
```
