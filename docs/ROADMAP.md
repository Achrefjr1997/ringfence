# RingFence roadmap — what we build next, and in what order

Written 2026-09-10, after a market pass and a full codebase audit. Companion
docs: `DESIGN_PRODUCTION.md` (the architecture this implements),
`EXTERNAL_BENCHMARK.md` (the accuracy numbers this is trying to move),
`OVERSIGHT.md` and `SIPREC.md` (the two phase-plans that shipped before it).

Four phases. Phase 1 is things that are **broken**, not missing. Phase 2 is
detection accuracy — and it argues for **moving weight off vocabulary onto
structural signal**, because a lexicon is a snapshot of phrasing and the
adversary edits phrasing for free. Phase 3 is what makes it sellable. Phase 4 is
the mobile SDK.

## Decisions this plan encodes

1. **Detection accuracy leads.** Nothing else matters if the engine only works
   on the scripts it was tuned on.
2. **Structure over vocabulary.** The fix for `refund`/`reward` at 1.5% is not a
   bigger lexicon — `d7acf11` tried that and moved one family. It is signals
   that survive rewording: repetition counts, dialogue acts, numeric structure.
3. **Carrier + enterprise is the wedge.** SIPREC (PRs #54–#57) is the revenue
   path; the console is the workflow around it.
4. **The mobile SDK is back in scope**, with eyes open — see §4.0 for what that
   costs and what it can never do.

## Already shipped

- **Stage 0 — the gate** (PR #59). `packages/eval/gate.py` now gates three
  numbers instead of one: recall, FPR, **and benign peak-score margin**. FPR
  counts threshold crossings and cannot see a corpus creeping up to the line —
  today one benign call peaks at 68.1, thirteen points *above* ALERT, held down
  only by `sustain_turns: 2`. Every accuracy change below is measured against
  this gate.

---

# Phase 1 — Broken now

Four items. None is large; all of them block something real.

### 1.1 The French lexicon never runs *(1 PR, small)*

**Problem.** `apps/gateway/app.py:1167` hard-codes `language="en"` on every live
`SessionDescriptor`. `packages/pipeline/pipeline.py:112` therefore always calls
`load_lexicons("en")`. The 99-term French lexicon and the French warning
templates are unreachable on the live path — French is not "untested in
production", it does not execute.

**Change.**

- Resolve language in this order: SIPREC `rs-metadata` / capture query param →
  `TenantConfig.languages[0]` (already parsed, currently unused) → `"en"`.
- Thread it into the `SessionDescriptor(language=...)` that `capture()` builds.
- Record the resolved language on the call ledger row so it is auditable.

**Verify.** A French fixture driven through the **live gateway path** (not the
offline harness) produces French `SignalHit`s and a French warning template.
Add that as an integration test — none exists today.

### 1.2 `POST /replay/{fixture_id}` is unauthenticated *(1 PR, small)*

**Problem.** `apps/gateway/app.py:533` has no auth check of any kind and takes
`tenant_id` from `?tenant=`. An anonymous caller can publish fabricated
decisions onto **any tenant's** `rf.<tenant>.*` subjects and inject cases into
their store. The guardian dispatcher's replay guard only skips the literal
tenant `"replay"` (`guardian.py:84`) and then calls
`hook.notify(..., mode=Mode.SDK)` hard-coded (`guardian.py:110`) — so choosing
`?tenant=acme` bypasses the guard and can drive a customer's guardian webhook.

**Change.** Require an API key (reuse `read_tenant` / `admit`), derive the
tenant from the key and ignore `?tenant=` entirely, and refuse the route
outright unless `RF_DEV_MODE=1`. Force `Mode.REPLAY` end to end so the existing
dry-run invariant covers it.

**Verify.** `POST /replay/x` without a key returns 401. With a key for tenant A,
`?tenant=B` still publishes to A. Invariant #4 (no outbound in replay) extended
to cover the route.

### 1.3 ASR cost guard *(1 PR, small)*

**Problem.** AssemblyAI bills the **full wall-clock time a WebSocket stays
open, including idle time**; an unclosed session can bill up to three hours. We
open one socket per leg per call, and `apps/siprec` now opens them on behalf of
a remote SBC whose hangup we do not control. One stuck leg is a silent,
unbounded cost leak with no metric on it.

**Change.**

- Hard per-session wall-clock cap (`RF_ASR_MAX_SESSION_S`, default ~2 h) in
  `packages/asr/assemblyai.py`, forcing `close()` past it.
- A media-timeout close: no frames for N seconds tears the leg down (the §4.3
  lifecycle diagram already specifies 8 s of silence and no RTP).
- `rf_asr_session_seconds` and `rf_asr_forced_close_total` in
  `packages/obs/metrics.py`, plus an alert rule.

### 1.4 Wire the VAD in *(1 PR, small-medium)*

**Problem.** `packages/media/vad.py` is fully built and tested and imported by
nothing. It is the hook for two separate things we need.

**Change.** Gate the ASR uplink on voiced frames in the capture path, so we stop
paying to transcribe silence (batch is 40–50% cheaper than streaming, and
silence is 100% waste). Then use the same signal for the §4.4 shed ladder's
bottom rung — drop unvoiced frames above 98% queue pressure.

**Verify.** A fixture with long silences produces materially fewer ASR bytes
with identical decisions. The shed ladder gets its first real rung.

---

# Phase 2 — Detection accuracy

The numbers to beat, measured on `main` over 1,600 external dialogues:
**recall 0.2725, FPR 0.000, benign margin −13.09.** Almost all the recall is one
family — `ssn` at 99% — while `refund` and `reward` sit at **1.5%**.

## The framing: the problem is vocabulary *dependence*, not vocabulary *size*

`ssn` at 99% is not generalisation, it is two memorised phrases. `refund` and
`reward` at 1.5% is the same fact seen from the other side. The 11 terms added
in `d7acf11` moved exactly one family and left every other one where it was.

We have 396 lexicon terms across three languages, against a paraphrase space
that is *growing* — scam syndicates now generate scripts with agentic AI, so the
surface language gets more fluent and more varied every month. A lexicon is a
snapshot of phrasing; the adversary edits phrasing for free.

Two non-answers, both already tested:

- **A bigger lexicon.** That is what `d7acf11` was, and it bought one family.
- **Lexical retrieval (BM25).** `packages/risk/kb.py` tolerates word order and
  inflection, not synonymy — *"you've been selected for a cash prize"* and
  *"congratulations, you're our winner"* share almost no terms. It is useful for
  assembling judge context and it is **not** a paraphrase defence.

**What generalises is signal that is not vocabulary.** We already have proof
this works: `NumericExtractor` detects OTP / PAN / Luhn / IBAN / spelled-digit
runs. A six-digit code read aloud is **paraphrase-proof and language-independent**
— no rewording changes the fact that a card number was spoken. That is the model
for this whole phase.

| Layer | Owns | Survives paraphrase? |
|---|---|---|
| **Structural** — numeric, dialogue acts, escalation, turn dynamics | the *act* | **Yes.** The load-bearing tier. |
| **Lexical** | high-precision anchors only | No. Accept it; keep it small. |
| **LLM (bounded ±30)** | the residual — novel shape, novel pretext | Yes, but slow, costly, and currently unmeasured (§2.0) |

The strategic move is **shifting weight off vocabulary onto structure**, not
growing the vocabulary and not replacing it with a model.

### 2.0 Make the judge observable *(1 PR, small — do it first)*

We cannot give Tier 2 responsibility for anything while we cannot see whether it
answers. Today `_TIMEOUT_S = 0.8` (`packages/risk/judge.py:31`), a timeout
returns `verdict="unclear", adjustment=0` **silently** (`judge.py:86`), and
**no metric anywhere counts misses or latency**. `docker-compose.prod.yml`
already carries the discovery in a comment — *"Ollama Cloud p99 ~2s; 0.8s
default times out"* — and prod runs 2.5 s, which is itself above the p95 ≤ 2.0 s
warn-latency SLO. The live integration test uses `timeout_s=20.0`, 25× the
production budget.

**Change.** `rf_judge_calls_total`, `rf_judge_timeouts_total`,
`rf_judge_latency_seconds` in `packages/obs/metrics.py`, plus an alert when the
miss rate crosses a threshold. Then set the timeout from data instead of a guess.

### 2.1 Make `ESCALATION` real *(1 PR — the highest-leverage item in this plan)*

`ESCALATION` (+10) is in `KNOWN_SIGNAL_IDS` and weighted in the pack, and
**emitted by nothing**. `scoring.py`'s docstring says why it should exist:
*"Repetition is ESCALATION's job, not summation's."* Today `EvidenceWindow.add`
dedups on `(signal_id, role)`, so a scammer repeating an ask ten times scores
exactly the same as once.

**Why it leads this phase:** it is the first signal that gets *stronger* under
paraphrase rather than weaker. If the scammer asks five times in five different
phrasings, the count of asks is the signal — rewording **increases** it. That is
the exact opposite of a lexicon's failure mode.

**Change.** `EvidenceWindow` keeps `hits` untouched — so decay semantics and
every combo call site are unaffected — and gains an occurrence log:

```python
occurrences: dict[tuple[str, Role], list[float]]
```

pruned on the same `span_s` cutoff, read via `repeats(now)`. A new
`evaluate_escalation(window, now, pack)` in `packages/risk/derived.py`, the
shape `NO_ACTION_ASKED` already established, returns **at most one**
`Contribution`:

```text
n     = max repeat count over (signal, CALLER) pairs with pack weight > 0
value = spec.weight * min(1.0, log2(n) / log2(_SATURATE_AT))
```

Three safety properties, each a test:

1. **CALLER-only** — protective signals and CALLEE/UNKNOWN never contribute, so
   invariant #1 cannot be touched.
2. **One contribution ever** — the max across signals, not one per signal. So
   `ESCALATION` can add **at most +10 to any call, ever**.
3. **Saturating** — 2 repeats gives +5, 4 or more gives +10. Fifty repeats is
   worth the same as four.

**The one real risk, and it is empirical.** Today's worst benign call peaks at
68.1; +10 would be 78.1, over INTERVENE (75). Under CALLER-only counting that
call has zero repeats and would not fire — but that is one sample.
`_MIN_REPEATS` and `_SATURATE_AT` are to be **chosen by measurement against the
800 benign items**, starting at 2 and 4. This is exactly the loop the Phase-0
gate exists to make cheap.

### 2.2 `DialogueActExtractor` — the main new build *(2–3 PRs)*

`DESIGN_PRODUCTION` §6.2 lists it as [P2] and it does not exist. The pipeline
runs exactly two extractors (`packages/pipeline/pipeline.py:181`).

**The idea.** *"Read me the code"*, *"Tell me those numbers"*, *"What does the
text say"*, *"Give me that six-digit code"* are **one act** with unbounded
surface forms. Classify the act, not the words.

**Why it survives paraphrase.** An act is `(act type) × (target)`, and both
parts are stable where vocabulary is not:

- **Act type** comes from *function words and syntax* — a closed, slow-changing
  class. Sentence-initial bare verb → imperative. Wh-word or auxiliary inversion
  → interrogative. Negation plus a first-person modal (*"I won't…"*, *"I'm not
  going to…"*) → refusal. Content words, which are what an adversary edits, do
  not participate.
- **Target** comes from the structural extractors we already have —
  `NumericExtractor` for codes / PAN / IBAN, plus a small closed entity list for
  accounts, transfers and remote-access software.

The pair is the signal: **an imperative or interrogative directed at the callee
whose target is a secret or a money rail.** That composition is what "read me
the code" and "just tell me what the message says" have in common, and it is
what no rewording removes.

**New signals:** `ACT_DEMAND_SECRET`, `ACT_DEMAND_RAIL`, `ACT_REFUSAL`
(protective, negative weight).

**No new dependencies.** This is pattern matching over function words composed
with the extractors we already run — not a parser, not a model.

**The acceptance test is the whole point:** build a paraphrase set — the same
scam acts rewritten with disjoint content vocabulary — and require that
`DialogueActExtractor` holds recall where `LexicalExtractor` collapses. If it
does not beat the lexicon on paraphrased text, it has not earned its place.

### 2.3 Pretext signals — small, and honestly limited *(1 PR)*

Demoted from where this plan first put it. Pretext phrases (`sweepstakes`,
`processing fee`, `you've been selected`, `account has been suspended`) were
mined and rejected because `docs/LEXICON_MINING.md` records they *"describe scam
pretexts that RingFence has no signal for."* Adding the signal is worth doing —
but it is still vocabulary, so it will help the `refund` and `reward` families
on the phrasings we anticipated and not much beyond them.

**Change.** `PRETEXT_WINDFALL`, `PRETEXT_REFUND`, `PRETEXT_ACCOUNT_ISSUE` at a
**low weight (+8 to +12)** — context, not coercion, never enough to carry a lone
call to ALERT. Then a combo: pretext ∧ (`ACT_DEMAND_SECRET` ∨
`ACT_DEMAND_RAIL`). The pretext sets up the ask; it is the **pair** that scores.
Note the combo pairs with a *structural* signal from 2.2, not a lexical one —
that pairing is what keeps it working when the pretext itself is reworded.

### 2.4 Turn-offset awareness *(1 PR, contract change)*

Research (arXiv 2606.16052) finds phase and turn-offset modelling beats both
keywords and few-shot LLMs — 84.4% vs 70.9% tactic accuracy — and that **one
turn after first evidence is worth +11–16 accuracy points**. Our evidence window
is purely time-based.

**Change.** Thread `Turn.turn_order` into `SignalHit`
(`packages/contracts/risk.py:10` has no turn index today), then let
`score_window` weight by turns-since-first-evidence alongside age decay.

Do this **last and alone** — it touches a frozen contract and every
construction site, and if it rides along with 2.1–2.3 a regression cannot be
attributed to any of them.

## Phase 2 success criteria

Measured on the external corpus, against the Phase-0 gate:

- **Recall ≥ 0.45**, and — the part that matters — driven by `support`,
  `refund` and `reward` rather than by more `ssn`.
- **Benign margin no worse than −13.09**, benign items scoring above zero no
  higher than today's 164, and no benign peak above 55.
- **FPR 0.000** at both ALERT and INTERVENE; ten invariants green.
- **Paraphrase holds:** on the rewritten-vocabulary set from 2.2, structural
  signals retain recall where lexical recall collapses. This is the number that
  says whether we solved the problem or just moved the snapshot.

---

# Phase 3 — Sellable

### 3.1 Evidence pack export *(1–2 PRs)*

The commercial wedge. UK PSR mandatory reimbursement splits APP-fraud liability
50/50 between sending and receiving banks, so a bank's question is not "warn
me", it is "prove it". We store the ledger, scored timeline, transcript, audio
and access audit — and package none of it. There is **zero CSV or PDF anywhere
in the repo**, and `/calls` caps at 500 rows.

**Change.** `GET /calls/{id}/evidence` returning a signed bundle: call metadata,
the full score series with contributions and counterfactual, transcript, a
reference (not a copy) to the audio object, and the access-log entries. Plus a
date-ranged `GET /reports/calls.csv`. Sign the manifest so a third party can
verify it was not edited after the fact — that is the whole point.

### 3.2 Human alerting *(1–2 PRs)*

Today the only outbound customer channel is **one webhook URL in a server-side
YAML file** (`config/tenants.yaml`), fire-and-forget, capped at 5/hour, with no
retry and no delivery log. "How do I get paged when a live call hits
INTERVENE?" has no answer but "write a webhook receiver."

**Change.** Email and Slack transports behind the existing
`packages/intervene/http_transport.py` seam, self-serve webhook management
(`POST /orgs/webhooks`, a test-fire button, a delivery log), and at-least-once
delivery with backoff per §8.3.

### 3.3 Email delivery and onboarding *(1 PR)*

There is **no SMTP, SES or Postmark anywhere**. Invite, password-reset and
verify tokens are returned in API response bodies for an admin to hand-carry,
and the SPA has no `#/accept` route for an invitee to land on. A fraud desk
cannot onboard a team without curl.

**Change.** An email transport, tokens delivered by email rather than returned
in the response, `#/accept` and `#/reset` routes, and an Invite button in the
Team tab.

---

# Phase 4 — The mobile SDK

## 4.0 Read this before planning any of it

This lane was previously scoped out and is now back in. It can work, but four
constraints are physics rather than engineering, and every one of them has to
survive into the product copy.

| Constraint | Consequence |
|---|---|
| **iOS does not expose call audio to third-party apps. At all.** | There is no iOS live protection and there never will be. iOS is CallKit-adjacent only: number reputation, post-call review, guardian alerts. `DESIGN_PRODUCTION` §3.3 says it outright — *"Do not promise iOS live protection."* Being caught overpromising it is worse than not having it. |
| **Android banned the Accessibility API for call recording in May 2022.** | No third-party app taps the call stream. `AudioSource.MIC` **speakerphone capture** is the only supported path — which means the user must put the call on speaker for protection to work at all. |
| **Speakerphone capture is one mixed stream.** | `RoleHint.MIXED`, so role comes from the acoustic classifier — whose measured 1.0 accuracy is on *synthesised* audio band-limited with the exact cue it looks for. SDK-mode accuracy will be materially worse than carrier mode, and §11.6 requires that difference be measured and reported, not hidden. |
| **Google ships Scam Detection free on Pixel 9+ and Galaxy S26.** | We are not first on the handset. The SDK's reason to exist is that it is *the bank's or operator's own app* — tied to their accounts, their guardian relationships and their fraud desk — not a better scam detector than Google's. |

Also: continuous capture plus streaming costs roughly **4–7% of battery per
hour** on a mid-range Android device.

## 4.1 Server prerequisites *(do these first — the SDK cannot ship without them)*

**There is no push infrastructure in the repo at all** — no FCM, no APNs, no
device tokens, nothing.

- **Device registration**: `POST /devices` (token, platform, app version, the
  `user_ref` it protects) and `DELETE /devices/{id}`, stored per tenant. Needs a
  new table in `packages/identity/`.
- **Push transport**: FCM for Android and APNs for iOS, behind the existing
  `Transport` protocol in `packages/intervene/http_transport.py`. **New
  dependency — needs sign-off.** Raw FCM/APNs HTTP v1 plus a signed JWT avoids
  adding `firebase-admin` at all, and that is my recommendation.
- **Wire `InterventionService` in.** It renders the actual multilingual warning
  from `packages/intervene/templates.yaml` for the `in_ear`, `app_banner` and
  `guardian_push` channels — and it is **imported by no application code, only
  by tests**. The policy pack lists those three channels and none of them has a
  delivery path. This is the single biggest gap between what the pack claims and
  what runs, and the SDK depends on it entirely.
- **Signed device tokens** — §3.6 admission requires a signed device token for
  SDK mode, not an API key baked into an APK. Reuse
  `apps/gateway/purpose_tokens.py`.

The good news: `/ws/capture` already accepts `mode=` and defaults to `Mode.SDK`
(`app.py:1129`), and the warning copy already exists in en, fr and ar_tn keyed
by signal. The server seam is largely built.

## 4.2 Android capture app *(the core build — months, not weeks)*

- **Foreground service** with a persistent notification, required for mic access
  during a call, started from a CallKit-equivalent hook or manually by the user.
- **Capture**: `AudioSource.MIC`, 16 kHz mono PCM16. **Explicitly disable AGC,
  AEC and noise suppression.** `packages/media/normalise.py` documents why at
  length: AEC is designed to remove loudspeaker audio, which in speakerphone
  capture *is the adversary's voice*. This single misconfiguration makes the
  whole system appear to work while detecting nothing.
- **Uplink**: Opus 16 kHz / 20 ms frames at roughly 24 kbps, WSS to
  `/ws/capture` with `mode=sdk&leg=mixed`, QUIC later. Resumable with a 30 s
  session token so a network handover does not lose the call.
- **Warning UI**: full-screen banner plus haptic, text from `templates.yaml` via
  the server. Active warnings that interrupt and require a decision measurably
  outperform passive dismissible ones.

## 4.3 Tier-0 on-device gating *(what makes it economically viable)*

The SDK must not stream every call. A small on-device model — roughly 3 MB
quantised, keyword spotting plus a light classifier — watches locally and opens
the uplink only when local suspicion crosses a low threshold.

- The median call costs **zero cloud audio-seconds**, which is the difference
  between a viable and an unviable unit economic on a consumer app.
- The privacy story improves enormously: most calls never leave the device.
- Offline degradation is graceful — tier-0 still warns on the highest-confidence
  patterns with no network.
- **Cost: recall drops for slow-burn scams that never trip the gate.** Measure
  this explicitly as **gate recall** and treat a regression as a release
  blocker. It is a new number in `packages/eval/` and it does not exist yet.

## 4.4 iOS app *(narrow by necessity)*

Number reputation, post-call review, guardian push and account-linked alerts.
**No live audio, no live protection.** Ship it as the guardian and companion
half of the product, and say so plainly in every deck.

## 4.5 Phasing

1. **4.1 server prerequisites** — push, device registry, `InterventionService`
   wired in. Independently useful: it lights up `guardian_push` for carrier and
   enterprise customers too, with no app at all.
2. **iOS and Android guardian app** — push, verdict-only case view, one-tap
   call-back. No capture. Ships on both platforms and validates the push path.
3. **Android capture, cloud-only** — no tier-0. Prove the audio path and measure
   SDK-versus-carrier accuracy honestly.
4. **Tier-0 gating** — the economics, gated on a gate-recall metric.

Do not start 4.2 before 4.1 is done; the app would have nothing to talk to.

---

# Sequencing

Phase 1 is four small PRs and should go first — 1.1 and 1.2 are a broken feature
and a security hole. Phase 2 is the highest-value work, and its PRs must land one
at a time, each measured against the Phase-0 gate. Phase 3 unblocks selling.
Phase 4 is the largest single investment in this document, and its first step is
useful on its own.

Rough shape: Phase 1 days, Phase 2 weeks, Phase 3 weeks, Phase 4 months.

# Standing constraints

- **No new dependencies without asking.** The only one this plan needs is a push
  transport (§4.1), and raw FCM/APNs HTTP avoids even that.
- `mypy --strict`, `ruff check` **and** `ruff format --check` green.
- The ten invariants in `tests/invariants/` never regress.
- Every accuracy change reports recall, FPR **and** benign margin, before and
  after.
- **Never build voice-emotion scoring.** EU AI Act Article 5(1)(f) prohibits
  emotion recognition in the workplace — enforceable since February 2025, up to
  €35m or 7% of worldwide turnover, and our contact-centre market is squarely in
  scope. We detect tactics in language, not emotion in voice. That is a moat;
  keep it.
