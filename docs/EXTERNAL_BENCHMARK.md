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
| **benign peak-score margin to ALERT** | **−13.09** | +55.00 *(never reproducible — see below)* |

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
- **The old `+55.00` margin was wrong, not regressed.** Re-running this
  benchmark in a worktree at `d743c3c` — the exact commit that wrote the
  previous version of this file — reproduces recall `0.035` exactly and gives
  margin **−13.09**, not `+55.00`. `+55.00` is the *eight-fixture* margin
  (`alert 55 − max benign peak 0.0`, see `reports/latest.json`); it was pasted
  into the external-benchmark table by mistake. `d7acf11` bought recall and did
  **not** spend precision — margin is −13.09 both before and after it.
- **But the headroom really is gone, and always was here.** FPR is 0.000 at
  every threshold, which is why nothing noticed:

  | benign peak score | items (of 800) |
  |---|---|
  | 0 | 636 |
  | 10–29 | 23 |
  | ~30 (WATCH) | 129 |
  | 40–59 | 11 |
  | **68.1** | **1** |

  164 of 800 benign calls score above zero. The worst (`mas_train_0051`, an
  *appointment* call) reaches **68.1** — 13 points above the ALERT threshold —
  and is held at WATCH only by the `sustain_turns: 2` hysteresis rule. Headline
  FPR is 0.000 and is one unlucky turn away from not being. **FPR counts
  threshold crossings; it cannot see a corpus creeping up to the line.** That is
  why `packages/eval/gate.py` now gates the margin as well.
- **`VERIF_INVERT` is the precision liability.** It fires **200 times across
  benign calls** — more than every other signal combined (`RAIL_UNUSUAL` 87,
  `URGENCY` 56, `OFFER_CALLBACK` 14, `CALLBACK_SUPPRESS` 4) — and carries the
  largest weight in the pack (+30). Benign drift is concentrated in
  `insurance` (67 items), `delivery` (56) and `appointment` (39).

**The lesson for the next recall change:** single high-weight lexical terms buy
recall on the family that uses them — `ssn` 8% → 99% — and leave every other
family where it was. Recall that generalises has to come from *corroboration*:
several distinct signals, or the same signal pressed repeatedly. That is what
`ESCALATION` and the combos are for, and `ESCALATION` is declared in the pack
and emitted by nothing. Whatever comes next, the margin is now gated, so it has
to declare what it costs.

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
