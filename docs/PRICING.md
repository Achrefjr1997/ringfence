# Pricing (T-7.3)

The model, the market it's anchored to, and the cost assumptions behind
it. Plans are data in `config/billing/plans.yaml`; the gateway meters
usage and enforces the pilot hard cap. Wiring an external biller (Stripe)
is a follow-up — the `BillingProvider` seam is in place, `NullBilling` is
the default.

## Model

**Per protected line, with a pooled monthly call-minute allowance and
metered overage.** RingFence is B2B (an org with operators / guardians),
but the value anchor is consumer scam protection, which is priced
per user.

| Plan | Base / mo | Included lines | Min / line / mo | Overage / min | Hard cap |
|------|-----------|----------------|-----------------|---------------|----------|
| **Pilot** | $0 | 3 | 200 | — | **yes** (stops admitting at the allowance) |
| **Starter** | $49 | 10 | 300 | $0.020 | no |
| **Growth** | $249 | 60 | 400 | $0.015 | no |
| **Scale** | $999 | 300 | 500 | $0.010 | no |
| **Enterprise** | custom | — | — | — | no |

Effective per-line: Starter **$4.90**, Growth **$4.15**, Scale **$3.33** —
inside the consumer band and cheaper with volume, which is the right shape
for a call desk buying many lines.

The allowance is **pooled** across a plan's included lines (Starter =
10 x 300 = 3 000 min/mo), so a few heavy lines don't push a tenant into
overage while others sit idle. Only audio RingFence actually processes
counts — most calls never reach it.

## Market anchors (Sept 2026)

Consumer scam / spam-call protection, per user / month:

| Product | Price |
|---------|-------|
| Nomorobo | ~$1.99 / line |
| Hiya Premium | $3.99 (Hiya AI Phone $9.99) |
| Truecaller Premium | ~$4.99 (Gold higher) |
| Robokiller Premium | $4.99 |

B2B voice-fraud (Pindrop, Modulate) is enterprise-only, sold per
call-center contract on call volume, no public pricing — so the
Enterprise tier here is "call us", same as the incumbents.

## Cost basis — **verify before launch**

Per call-minute processed:

| Component | Estimate | Note |
|-----------|----------|------|
| Streaming ASR | **$0.0025 – $0.0092** | AssemblyAI Universal-2 streaming $0.15/hr .. Universal-3.5 Pro Realtime $0.45/hr; Deepgram Nova-3 ~$0.46/hr. Diarisation +$0.02/hr, keyterms +$0.05/hr. |
| Tier-2 judge | **~$0.001 – $0.003** | Ollama Cloud `gpt-oss:120b`, fires on trigger not per minute, amortised. **Ollama Cloud rate not yet pinned — TODO.** |
| Infra | **< $0.0001** | ~100 concurrent calls / node on a ~€45/mo VPS. |
| **All-in marginal** | **~$0.006 – $0.012 / min** | |

Overage rates above are **~1.5 – 2x** the midpoint. Included minutes are
priced thin on paper but rarely fully consumed. Before real customers:
pull the exact AssemblyAI and Ollama Cloud numbers from live contracts and
re-check the table — this is flagged in `SAAS_ROADMAP.md` too.

## What the gateway does now (this PR)

- **Metering** — on every `/ws/capture` session close: `call_minutes`
  (wall-clock duration) and `calls` are recorded per tenant per billing
  month (`BillingStore`; in-memory by default, Postgres when
  `RF_DATABASE_URL` is set — `usage_counters` table, atomic `+=`).
- **`GET /usage`** (admin) — current-period snapshot: minutes used vs
  included, projected overage cents, month total, `over_hard_cap`.
- **`POST /orgs/plan`** `{plan}` (admin) — switch tier.
- **Admission** — a hard-capped plan (Pilot) that has spent its allowance
  is rejected at `/ws/capture` with reason **`BILLING`**, counted in
  `/metrics` like every other rejection. Metered plans just accrue.

## Next (follow-up PR)

`StripeBilling` implementing `BillingProvider` over the Stripe **Meters
API** (v2; legacy usage records are gone as of `2025-03-31.basil`): one
Billing Meter per usage metric, a recurring Price per plan, a customer +
subscription created on org signup, `report_usage` streaming meter events,
and a webhook handler for `invoice.*` / `customer.subscription.*`. Gated
on `RF_STRIPE_SECRET_KEY`; a new `billing` extra for the `stripe` SDK.

## Sources

- [Hiya price guide 2026](https://techradar.info/how-much-does-a-hiya-spam-blocker-cost-full-2026-price-guide/)
- [Truecaller vs Hiya vs RoboKiller 2026](https://unstar.app/blog/truecaller-hiya-robokiller-nomorobo-call-control-spam-call-blocker-apps-ranked-2026)
- [AssemblyAI speech-to-text pricing](https://www.assemblyai.com/blog/speech-to-text-api-pricing)
- [Deepgram pricing 2026](https://deepgram.com/learn/best-speech-to-text-apis-2026)
- [Stripe Meter Events API](https://docs.stripe.com/api/billing/meter-event)
- [Stripe usage-based billing guide 2026](https://www.buildmvpfast.com/blog/stripe-metered-billing-implementation-guide-saas-2026)
