# Evaluation corpus

RingFence has two corpora. One is done; the other is a **known gap** —
there are no real scam-call recordings yet, only synthetic fixtures.

## 1. Fixtures — synthetic, transcript-carrying (done)

`corpus/fixtures/*.json` — hand-written scam scenarios: each turn has
`text`, `role`, `t_start`/`t_end`, `expect_signals`, and the whole item
has an `expect_final_state`. They drive the **offline detection engine**
through `NullASR` (the text is replayed, no audio):

    python -m packages.eval.run --all --report reports/latest.json
    python -m packages.eval.role_eval           # mixed-stream role accuracy

Eight items across en / fr / ar_tn. This is what the invariant suite and
`make eval` exercise. It proves the **rules, combos, scoring and state
machine** — not transcription, not acoustics, not real language.

## 2. Recordings — real audio (the gap)

`corpus/labels/*.json` + a 16 kHz mono `.wav` under `corpus/`. The label
carries ground-truth **turns with `signals`** (never transcript text —
that comes from ASR at replay time), `transfer_line_t`, language, family.
Schema and loader: `packages/eval/corpus.py`. Harness:
`packages/eval/audio_eval.py` —

    python -m packages.eval.audio_eval --asr assemblyai --markdown
    python -m packages.eval.audio_eval --asr assemblyai --gate     # CI

It runs each recording through the **live `Pipeline`** (real ASR →
acoustic role attribution → detection) and reports detection rate,
detection-before-transfer, false-alarm rate, median alert time, and
per-item signal precision/recall against the label.

**Today there is exactly one placeholder item** (`example_delivery_en_001`,
a silent stand-in wav). The harness runs; the number is meaningless until
real recordings exist.

### Why this matters

Everything above the fixtures is tuned and tested against *hand-written*
language. Real callers hedge, mumble, code-switch, and talk over each
other; real ASR mis-transcribes. Until this corpus exists we do **not**
know RingFence's real-world false-positive or false-negative rate. This is
the last thing between "demoable" and "trustworthy on a stranger's call".

### Adding a recording

1. **Source it legally.** Options, roughly in order of safety:
   - staged calls between consenting colleagues acting from a script;
   - volunteers who record their own scam calls and consent to donate them;
   - public scam-baiting / awareness recordings **with a provenance note**
     (who published it, licence, date) in the label's `notes`.
   Never a real customer's call without that customer's explicit,
   documented consent. See `docs/SAAS_ROADMAP.md` §0 and `RUNBOOKS.md`.
2. **Convert** to 16 kHz mono WAV:
   `ffmpeg -i in.m4a -ac 1 -ar 16000 corpus/audio/<id>_16k.wav`
3. **Write the label** `corpus/labels/<id>.json` (see
   `corpus/labels/example_delivery_en_001.json`):
   `id`, `file` (path relative to `corpus/`), `language`, `label`
   (`fraud` | `benign`), `scam_family`, `transfer_line_t`, and `turns`
   with `t_start`/`t_end`/`role`/`signals` (signal ids must be known —
   `KNOWN_SIGNAL_IDS`).
4. **Validate**: `python -c "from packages.eval.corpus import validate_corpus; print(validate_corpus() or 'ok')"`
5. **Score**: `python -m packages.eval.audio_eval --asr assemblyai --markdown`

Real recordings are **not committed to git** (privacy + size) — keep them
in a private store the eval box can reach, or a git-annex / DVC remote.
Only the label JSON is committed.
