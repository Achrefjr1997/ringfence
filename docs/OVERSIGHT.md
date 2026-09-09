# Oversight console

An admin oversight view: for every call an org has run, see it in a list,
open its escalation graph and transcript, and slice all of it by the API
key that drove it. Built in phases.

| Phase | Delivers | Status |
|---|---|---|
| **P1** | Call ledger + score/alert graph | **shipped** |
| P2 | Transcript retained for every call (not just ALERT+ cases) | planned |
| P3 | Full-conversation audio: capture, storage, playback, retention | planned — **legal-gated** |
| P4 | Per-API-key alert dashboard (alerts / interventions over time) | planned |

## P1 — call ledger (shipped)

`packages/calls/` — a `CallLedger` sync Protocol (in-memory default,
`PgCallLedger` when `RF_DATABASE_URL` is set), same shape as the identity /
case / billing stores.

* **`call_ledger`** — one row per capture session: `session_id`, `tenant`,
  `api_key_id` (the key it was admitted with; NULL in dev mode),
  `started_at` / `ended_at`, `peak_state`, `peak_score`, `leg_count`. The
  gateway opens the row on admit ([`capture`](../apps/gateway/app.py)) and
  closes it when the last leg drops, next to the billing write.
* **`call_scores`** — one point per decision (`t`, `score`, `state`),
  appended by `CallLedgerRecorder`, which watches `rf.*.decision` on the
  bus (same pattern as the guardian dispatcher). Kept for **every** call,
  so the console can draw the escalation graph even for calls that never
  reached ALERT and so never opened a Case. `ON DELETE CASCADE` — the
  series dies with its call. Score and state only; never call content.

### Endpoints (admin / operator; `dev_mode` opens them)

* `GET /calls?key_id=&from=&to=&state=&limit=` — ledger rows, newest
  first, tenant-scoped. `state` is a *minimum* peak state.
* `GET /calls/{session_id}` — the row plus its score series, plus the
  transcript **iff** a Case exists for it and transcripts are retained.

### Console

A **Calls** tab: filterable list (by API key), and a detail view with a
canvas score-vs-time chart banded CALM / WATCH / ALERT / INTERVENE, the
transcript when available, and a link to the Case if one opened.

## P3 — audio (planned, legal-gated)

RingFence is observer-only and today stores **no audio**. Recording a
two-party call is regulated (two-party-consent jurisdictions, GDPR,
wiretap) and RingFence retaining it is a processing purpose separate from
the customer's own call flow — it needs a legal basis and a DPA (**T-7.0**).
The planned design keeps it opt-in: `RF_RETAIN_AUDIO` global (default
off) plus a per-tenant flag, short retention TTL, object storage
(`packages/storage/`, LocalFs default or S3/MinIO), an audited
`GET /calls/{id}/audio` (Range) endpoint, and a TTL sweep.
