# Ringfence as a SaaS — launch roadmap

*Prepared September 2026. Assumes this starts after the hackathon submission, building on the existing self-hostable architecture (Docker Compose, tenant scoping T-6.4, event bus, policy packs) rather than replacing it. Task numbers below continue the implementation plan's convention (T-7.x) so this slots after Phase 6 if you want to hand it to the same build agent later.*

## 0. Read this first: the legal gate comes before the infrastructure gate

Every phase below assumes you can legally do the thing Ringfence does — listen to a phone conversation between two other people and analyze its content. That is not automatically true, and it's the one item on this whole roadmap that isn't solvable by writing code. Two separate legal questions stack on top of each other:

**Call-recording/interception consent** is jurisdiction-by-jurisdiction and falls into two regimes: one-party consent (only one person on the call needs to know it's being recorded/analyzed — UK, Canada, most of the US) and all-party/two-party consent (everyone on the call has to know and agree — France explicitly, most of the EU, and many US states). France's penalty for recording without all-party consent is up to a year in prison and a €45,000 fine — that's not a hypothetical risk, it's a stated criminal penalty. [RecapMyCalls — Call Recording Laws by Country](https://recapmycalls.com/call-recording-laws-international/) Since Ringfence's own architecture never records or retains the transcript (transcripts-never-persisted-by-default is already an ADR you committed to), the honest framing to a lawyer is "real-time transient analysis, not recording" — but that distinction has to be confirmed by counsel in every jurisdiction you sell into, not assumed. A scam caller obviously never consents to being analyzed, which is precisely the edge case consent law wasn't written for — this needs a real legal opinion, not a design-doc paragraph.

**GDPR (and equivalents)** treats any voice data touching an EU resident as personal data regardless of who consented to what — it requires a documented lawful basis under GDPR Article 6 (consent, legitimate interest, etc.), and "calls may be monitored" boilerplate is explicitly called out as insufficient. [GDPR guide via recapmycalls](https://recapmycalls.com/call-recording-laws-international/) Given Ringfence's own privacy-by-design posture (no transcript retention, verdict-only webhooks), "legitimate interest — fraud prevention for the call recipient, minimal necessary processing, no retention" is a defensible legal basis to bring to counsel, but again: bring it to counsel, don't self-certify.

**Practical next step, not a legal conclusion:** before onboarding a single real paying customer whose calls Ringfence will process, get one hour with a lawyer (Tunisian counsel for local operation, plus counsel in whichever first market you sell into — likely France given the language/fixture work already done) to confirm: (a) real-time transient analysis without storage is treated differently from "recording" under local law, (b) what if any notice/consent mechanism is required for the call recipient (the person who installed Ringfence — straightforward, they consent by using it) versus the caller (the scammer — this is the actually unresolved question), and (c) what GDPR lawful basis to document. This gates T-7.1 onward for any jurisdiction where you plan to have a paying user.

## Phase-by-phase roadmap

### T-7.0 — Legal foundation (parallel with T-7.1, gates going live)
- Engage counsel per the section above. Get a written opinion, even a short one, covering consent and GDPR basis for your first target market(s).
- Draft a privacy policy that actually describes what happens: audio processed in memory, transcript never persisted by default, only a verdict (score/signals/rationale) may be stored for the case-review feature, guardian notifications carry no transcript. This is a genuinely strong privacy story if it's told plainly — lead with it rather than burying it in boilerplate.
- Draft Terms of Service covering acceptable use (this product cannot be marketed or used as a surveillance/eavesdropping tool against a call recipient — that's the line between "protecting someone from a scammer" and "spying on someone," and your ToS needs to make that boundary explicit and enforced).
- If operating out of Tunisia, look into the **Startup Act** label (startup.gov.tn) — it's a real, purpose-built legal/tax framework for exactly this situation (Tunisian tech startup, likely selling internationally), with a Fund-of-Funds investment vehicle (Startup Invest) and simplified structures. Worth the application regardless of timeline, since the benefits (tax treatment, easier capital raising, streamlined registration) compound the earlier you're in it. [Startup Tunisia](https://startup.gov.tn/en/home)

### T-7.1 — Authentication and tenant onboarding
This is the actual biggest engineering gap between what exists and a SaaS. T-6.4 gave you tenant *scoping* — subjects namespaced by tenant, per-tenant quotas, isolation in the event bus — but nothing that exists lets a real customer sign up, log in, or get an API key. Build:
- Account/org model: a signup flow, email verification, password (or OAuth — Google/Microsoft login lowers signup friction) auth.
- Role separation: org admin (billing, user management), operator/analyst (console + case view access), guardian (notification-only, per the mobile relay concept discussed earlier — read access to verdicts, never transcripts).
- API key issuance per tenant, replacing the current dev-mode "consent stubbed to accept" admission step in the gateway with a real check against issued credentials.
- This sits directly in front of the existing `apps/gateway` admission chain (tenant → consent → quota → capacity) — you're replacing the stub, not rearchitecting the chain.

### T-7.2 — Production deployment infrastructure
- Provision a real server. A VPS (Hetzner, OVH, Scaleway — all have EU presence, relevant given your likely first market) sized for expected concurrent-call load is sufficient to start; you do not need to abandon the self-hostable-everything ADR for managed cloud services. The production design already scoped ~100 concurrent calls/node as a baseline — start with hardware sized for that, not for the 10k/100k tier.
- Reverse proxy + TLS: Caddy or Traefik in front of the existing `docker-compose` stack — both auto-provision Let's Encrypt certs, minimal config given your compose file already exists (T-6.2).
- Real domain, DNS, and a staging environment separate from production — deploy to staging first, always.
- Secrets management: move off `.env` files sitting on a server disk. At minimum, encrypted secrets injected at deploy time (SOPS + age, or your CI provider's secret store); a dedicated vault (HashiCorp Vault, or a managed equivalent) if budget allows.
- Extend T-0.5's existing CI (currently "run tests on push") into CD: build images, push to a registry, deploy to staging automatically on merge to main, deploy to production on a manual approval gate.

### T-7.3 — Billing and usage metering
- Decide the pricing model before building metering — likely candidates: per-minute-of-audio-processed, per-concurrent-call seat, or flat tenant tiers with usage caps. Per-minute most directly maps to your actual cost driver (ASR streaming cost).
- Integrate Stripe (or equivalent) for subscription management and metered billing — don't build payment infrastructure yourself.
- Instrument usage tracking per tenant: minutes processed, calls handled, guardian notifications sent. This can piggyback on the existing event bus (`rf.<tenant>.*` subjects already carry tenant-scoped events) rather than needing a separate tracking system.
- Compute your actual unit economics before setting a price: AssemblyAI streaming (~$0.15/hr per your earlier figures) + LLM judge calls per session + infra amortized per concurrent-call capacity. Know your cost per call before you know your price per call.

### T-7.4 — Security hardening
- Encryption at rest for whatever is stored (case records, feedback labels, guardian device tokens) — never transcripts, by design, but signal-level data about a real person's call is still sensitive and needs the same protection.
- Per-tenant secrets: the guardian webhook HMAC secret and any per-tenant API credentials should be tenant-scoped, not one global value — a leak in one tenant shouldn't compromise another.
- Rate limiting and abuse protection at the ingress layer, beyond the admission chain's existing quota/capacity checks.
- A written incident response plan — who gets paged, what gets disclosed, to whom, and on what timeline, if something does leak. Having this written before an incident, not during one, is the difference between a controlled response and a scramble.
- A basic external security review before your first real customer — doesn't need to be a full pen test at this stage, but at minimum a checklist review of the auth flow, the webhook signing, and the admission chain.

### T-7.5 — Observability and on-call
- Metrics: uptime, ASR circuit-breaker state (you already have `CircuitBreaker` instrumented — expose its state), per-tenant queue depth, error rates. Prometheus + Grafana is the standard self-hosted stack and fits the self-hostable ADR; a managed alternative (Grafana Cloud free tier, e.g.) is fine too if you'd rather not run it yourself yet.
- Alerting wired to something a human actually sees at 3am if it matters that much at your current scale — even a simple webhook into a phone notification is better than nothing. (Note this reuses the exact push-relay pattern discussed for the Guardian app — you may end up building "notify a human when something's wrong" twice, once for customers' guardians and once for your own on-call, and it's worth recognizing they're the same primitive.)
- Structured logging (T-0.3's gap, flagged in the last review round, and now genuinely blocking here) — you can't debug a production incident against `logging.getLogger` output at scale the way you can against structured, queryable logs.

### T-7.6 — Go-to-market basics
- Landing page distinct from the operator console — the console is a tool for logged-in customers, the landing page is what a stranger sees when deciding whether to sign up.
- Published pricing.
- The privacy policy and ToS from T-7.0, live and linked, not just drafted.
- A path for the first design-partner customers — likely easier to start with a handful of hand-picked users (family members of the person building this, or contacts in Tunisia's startup ecosystem) who'll give real feedback before opening broader signup, rather than a cold public launch.

## Sequencing and what blocks what

T-7.0 (legal) doesn't block *building* T-7.1–T-7.5 — you can build all of the engineering work in parallel with getting the legal opinion. It blocks **going live with real customer calls**. Practically: get the legal conversation started now, in parallel with finishing the hackathon submission, since lawyers take longer to schedule than code takes to write, and you don't want engineering to finish before the legal answer does.

T-7.1 (auth/tenancy) blocks everything after it — there's no point building billing (T-7.3) against tenants that don't have real accounts yet, and no point hardening security (T-7.4) around an auth flow that doesn't exist. It's the correct first engineering task.

T-7.2 (deployment infra) can run in parallel with T-7.1 — provisioning a server and setting up TLS doesn't depend on the app having real auth yet.

T-7.3–T-7.6 are genuinely sequential after T-7.1/T-7.2 land, in roughly the order listed, though T-7.5 (observability) is cheap enough that doing it earlier rather than later is generally worth it — you want visibility into the system before you have paying customers generating incidents you can't see.

## What this roadmap deliberately does not cover

This is the SaaS operational shell — accounts, billing, deployment, security posture. It does not cover the product-quality work still open from the earlier review (the real 22-recording audio corpus, the T-1.5b/T-1.5c/T-1.9b fixes, resolving the manual T-3.4/T-4.3 verification steps) — that work determines whether the product is actually good before it determines whether it's sellable, and per the earlier discussion should be finished first regardless of how this roadmap gets sequenced.

## Sources

- [RecapMyCalls — Call Recording Laws by Country (2026)](https://recapmycalls.com/call-recording-laws-international/)
- [Startup Tunisia — official Startup Act program](https://startup.gov.tn/en/home)

---

## Appendix — status vs. the repo (not part of the original roadmap)

*Added at commit `2ae461a`, 2026-09-07. Corrections and additions from the code review; the roadmap body above is preserved as authored.*

### Claims to tighten before they go in a customer-facing doc

- **"transcripts-never-persisted is an ADR you committed to"** — it is invariant #5 in `AGENTS.md`, but there is **no test** asserting it. Principle, not enforced guarantee. Needs a test that fails if a transcript reaches disk before it's a privacy-policy claim.
- **Guardian webhook "one global HMAC secret"** — `GuardianWebhook` takes a single `secret`, but the gateway does not wire up `GuardianWebhook` at all yet. Tenant-scoping it (T-7.4) is building the wiring, not fixing existing wiring.
- **"~$0.15/hr AssemblyAI per your earlier figures"** — not a figure established anywhere in the build. Pull the real number from AssemblyAI's current streaming pricing before it feeds unit economics.
- **"~100 concurrent calls/node — the production design already scoped"** — verify against `docs/DESIGN_PRODUCTION.md` before sizing hardware to it.
- **"T-1.5b / T-1.5c / T-1.9b"** — only **T-1.5b** (the `NO_ACTION_ASKED` deriver) is a defined task. The other two are shorthand for review items R-A (lexicon cleanup) / R-B (`fx_family_emergency` fixture-vs-invariant honesty) — not yet scoped.
- **LLM judge status** — T-2.6b (commit `2ae461a`) wired the judge into `/ws/capture`; it now calls Ollama Cloud `gpt-oss:120b`. Note the 800 ms `BoundedJudge` deadline: verdicts only land with `RF_JUDGE_TIMEOUT_S` raised (~2 s), else the request bills but contributes 0.

### Gaps to add to the roadmap

1. **Role attribution is closer to a product gate than a quality item.** Single-stream speakerphone currently tags all speech CALLEE (`capture.js` sends `leg=mixed`), so nothing scores unless a side channel forces `leg=far`. T-6.3 is "partial" and its accuracy number is on synthetic audio. Belongs near T-7.0 as "does the core loop work in the field."
2. **EU data transfer, not just lawful basis.** AssemblyAI is US-hosted; streaming an EU resident's voice to a US ASR provider is a GDPR international-transfer question (SCCs / adequacy). Name it explicitly in the legal section for a France-first launch.
3. **LLM judge as a cost + dependency risk.** `gpt-oss:120b` on Ollama Cloud, per session, in the scoring path. Needs a per-call cost number and a decision on keeping a third-party model in a paid product's critical path.
4. **Key rotation is item zero.** The AssemblyAI and Ollama keys were pasted into a chat transcript during the build and still need rotating — independent of "move off `.env`" (T-7.2).
5. **Language coverage ↔ target market.** Validated lexicons are en/fr/ar_tn; the zero-shot semantic tier was retired as miscalibrated, so paraphrase/dialect coverage now rests on the Tier-2 judge plus lexicon phrase-mining — and phrase-mining has only been run for English so far. France-first makes French lexicon depth a launch dependency.
6. **Misuse can't be fully designed out.** Monitoring a family member's calls is the *same capability* as the legitimate use. The control is legal/marketing (ToS + not marketing it that way), not technical — state that plainly.
