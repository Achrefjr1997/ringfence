# Lexicon phrase-mining — English (fraud base-knowledge §1)

A run of `scripts/mine_lexicon.py` and the **human review** of its output.
The script never edits `en.yaml`; this note records what was added and, as
importantly, what was rejected and why. One over-broad term breaks
invariant #2 (review note R-A) — the bar for adding is high.

## Method

`scripts/mine_lexicon.py` ranks word n-grams (1–4) from scam CALLER speech
against benign CALLER speech by
`P(gram | scam) / P(gram | benign)`, with a raw-count floor
(`--min-support 4`) and a document-count floor (`--min-docs 3`). Anything
the current `en.yaml` already covers (Aho-Corasick is substring, so a match
either direction counts) is dropped. Survivors are bucketed under every
existing signal they share a content word with.

Corpora (fetched at run time):

| corpus | rows | role |
|---|---|---|
| ASsET `clean_spam` | 5 real scambaiter transcripts (~30 utterances) | scam, real |
| BothBosu `multi-agent-scam-conversation`, `labels=1` | ~5 200 CALLER turns | scam, synthetic (bulk) |
| BothBosu `multi-agent-scam-conversation`, `labels=0` | ~20 000 CALLER turns | benign, synthetic (bulk) |
| ASsET `clean_non_spam` (80 files) | real generic phone calls | benign, real (baseline) |

Full unreviewed candidate table: `python scripts/mine_lexicon.py --out reports/lexicon_candidates.md`.

## Added (11 terms)

| signal | term | why it's safe |
|---|---|---|
| `AUTH_CLAIM` | `social security administration` | Impersonating a named government agency. Real SSA does not cold-call demanding this. |
| `URGENCY` | `act quickly`, `act fast` | Bare coercion verbs, domain-agnostic; a legitimate bank/courier/support call does not tell you to "act fast". |
| `RAIL_UNUSUAL` | `prepaid debit card`, `purchase a prepaid`, `purchase a gift card` | Directing the callee to *buy* a stored-value instrument. No legitimate caller does this. |
| `REMOTE_ACCESS` | `access your computer`, `access your laptop`, `remotely access` | Core remote-control language not already covered by the tool-name / "give me access to your computer" entries. |
| `VERIF_INVERT` | `confirm your social security number`, `card number and pin` | Asking the callee to recite a full secret / a card number *and* PIN together — nobody legitimate asks for both. |

### Validation

- **13 invariants** green (`pytest -m invariant`), including #2
  (AUTH_CLAIM + URGENCY alone never ALERT).
- **8-fixture eval** unchanged — every en/ar fixture lands on the same
  peak state, score and first-alert time as before (`reports/latest.json`
  vs `reports/baseline.json`; the one delta, `fx_real_bank_frauddesk_fr_001`
  28.9 → 0.0, is the already-merged R-A fix showing against a stale
  baseline, on a French fixture these terms cannot touch).
- **Benign safety**: across 800 synthetic benign dialogues, **0** contain
  any added term. Across 800 synthetic scam dialogues, **57 %** contain at
  least one — a large coverage gain at zero measured benign cost.
- The out-of-distribution re-check is `python -m packages.eval.run_external`
  once the external-benchmark branch (#12) lands.

## Rejected (and why)

- **Generic banking/identity terms** — `account number`, `debit card`,
  `routing number`, `social security number`, `security code`,
  `case number`, `badge number`, `verify your information`. All occur in
  legitimate calls; adding them to `AUTH_CLAIM` / `VERIF_INVERT` /
  `URGENCY` is exactly the invariant-#2 risk R-A warned about.
- **Tokenisation artifacts** — `time payment`, `time fee`,
  `time opportunity` (the miner's stop-list drops the leading "one" /
  "limited"), `card number expiration date` (drops "and").
- **`time sensitive` / `time sensitive matter`** — real fraud desks say
  "this is time-sensitive"; too close to the benign line.
- **`SECRECY` / `CALLBACK_SUPPRESS` candidates** — the bucketer matched on
  the word "don't", but the grams (`don't take immediate action`,
  `don't cooperate`, `if you hang up`) are *threats*, not requests for
  secrecy, and are context-dependent. No genuine new secrecy phrasing
  surfaced.
- **Protective signals** (`REFUSE_SECRETS`, `OFFER_CALLBACK`,
  `BRANCH_REFERRAL`) — mining *scam* speech cannot surface protective
  phrases; every candidate there was a bucketer misfire.
- **Pretext markers with no signal** — `malware`, `sweepstakes`,
  `you've been selected`, `winner`, `processing fee`, `verification fee`,
  `process the refund`, `account has been suspended`. Strong scam markers,
  but they describe scam *pretexts* (prize bait, fee-to-release, refund
  scam, suspension pretext) that RingFence has no signal for. Adding a
  signal is a policy-pack change with its own weight tuning and review —
  out of scope here. Noted as future work.
