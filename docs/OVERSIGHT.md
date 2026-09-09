# Oversight console — plan

An admin oversight view: for every call an org has run, see it in a list,
open its escalation graph / transcript / audio, discuss it in timestamped
threads, and slice all of it by the **employee** and the **API key** that
drove it — with role-scoped visibility (own calls → team → whole org).

Built in phases; each phase is one PR. The shape is drawn from how
comparable products do it (see *Prior art* at the end).

| Phase | Delivers | Gate | Status |
|---|---|---|---|
| **1** | Call ledger + escalation graph | — | **shipped** (`feat/call-oversight`) |
| **2** | Per-employee attribution + "by employee" roll-up | — | **shipped** (`feat/oversight-p2-users`) |
| **3** | Threaded review (timestamped comments, @mention, visibility) | — | **shipped** (`feat/oversight-p3-comments`) |
| **4** | RBAC hierarchy (own → team → org) + private calls + shares | — | **shipped** (`feat/oversight-p4-rbac`) |
| **5** | Access audit log (who viewed / played / exported which call) | — | **shipped** (`feat/oversight-p5-audit`) |
| **6** | Transcript retained for *every* call, not just ALERT+ cases | `RF_RETAIN_TRANSCRIPTS` (invariant #5) | planned |
| **7** | Full-conversation audio: capture, storage, playback, retention | **T-7.0 legal basis + DPA** | planned |
| **8** | Live wall (supervisor sees every active call) | — | planned |
| **9** | Employee timeline + per-tenant retention controls | — | planned |

Only **Phase 7** is legally gated. Phases 2–6 and 8 can ship without
counsel.

---

## Design principles (from the prior art)

1. **A call belongs to a person, not an integration.** "My calls" vs
   "my team's calls" vs "everything" is a *role* decision, not a filter
   (Gong's permission-profile model; Dialpad Company Admin sees all).
2. **Threads = timestamped comments anchored to a moment in the call**,
   each with a visibility (`org` / `private` / `mentions`), replies, and
   `@mention` that grants that person access to the call (Gong Comments
   tab). Not a chat.
3. **Anything with audio needs an access audit log** — who played back
   which recording, when. Every compliance vendor (NICE, Verint, Theta
   Lake) treats this as table stakes; regulators ask for it.
4. **Live floor view is a separate surface from history** (Balto / Cresta /
   Observe.AI supervisor view) — the `LiveWall`.
5. **Retention is per-tenant and enforced by a sweep**, per data class
   (ledger / transcript / audio).

---

## Phase 1 — call ledger *(shipped)*

`packages/calls/` — `CallLedger` sync Protocol (in-memory default,
`PgCallLedger` when `RF_DATABASE_URL` is set).

* **`call_ledger`** — one row per capture session: `session_id`, `tenant`,
  `api_key_id` (NULL in dev mode), `started_at` / `ended_at`,
  `peak_state`, `peak_score`, `leg_count`. Opened on admit in
  [`capture`](../apps/gateway/app.py), closed on last-leg drop.
* **`call_scores`** — one point per decision (`t`, `score`, `state`),
  appended by `CallLedgerRecorder` watching `rf.*.decision`. Kept for
  every call so the graph exists even for calls that never opened a Case.
  `ON DELETE CASCADE`. Score + state only, never call content.
* `GET /calls?key_id=&from=&to=&state=&limit=` and `GET /calls/{id}`
  (row + score series + transcript iff a retained Case exists).
* Console **Calls** tab: filter by API key, canvas score-vs-time chart
  banded CALM / WATCH / ALERT / INTERVENE, transcript when available.

---

## Phase 2 — per-employee attribution *(shipped)*

**Goal:** every call is attributed to the employee who was on it, so an
admin can answer "show me Alice's calls" and see a per-employee roll-up.

RingFence does **not** manage employee accounts — the employer's IdP
does. The integration supplies an opaque identifier when it opens the
socket.

* **Wire:** `/ws/capture?user=<ref>&user_label=<name>` **or** header
  `X-RingFence-User: <ref>`. `ref` is the employer's own id (email,
  employee number, …), opaque to us and capped at 200 chars. No signature
  is needed: the tenant comes from the API key, so a client can only
  mis-label calls *within its own tenant's data*.
* **Store:** `call_ledger.user_ref` / `call_ledger.user_label` (added via
  `ALTER TABLE … ADD COLUMN IF NOT EXISTS`, so an already-migrated
  `call_ledger` is patched in place).
* **Endpoints:** `GET /calls?user=<ref>` filter; `GET /calls/users` —
  per-employee roll-up (`calls`, `alerts`, `interventions`, `peak_state`,
  `last_at`), newest activity first. Both admin/operator.
* **Console:** the Calls tab gets an **All calls / By employee** switch,
  an employee-id filter box, and an `employee` column; a row in the
  by-employee view drills into that person's calls.
* **Deferred to Phase 4:** resolving "*my* calls" for a non-admin token
  (needs identity accounts linked to `user_ref`). Until then `/calls` is
  admin/operator only, as in Phase 1.

## Phase 3 — threaded review *(shipped)*

**Goal:** discuss a call at a specific moment, like Gong's Comments tab.

* **`call_comments`**: `id`, `session_id`, `tenant`, `author_id` /
  `author_email`, `body`, `visibility` (`org` | `private` | `mentions`),
  `t_seconds` (NULL = general), `parent_id` (NULL = top-level),
  `created_at`, `edited_at`, `resolved_at` / `resolved_by`. Cascades with
  its call and with its parent.
* **`call_comment_mentions`**: `(comment_id, mentioned_user_id)` — mention
  emails are resolved server-side to same-org identity users. The
  access-grant this implies is inert until Phase 4 (every reviewer already
  sees every call).
* **`CommentStore`** sync Protocol — `InMemoryCommentStore` /
  `PgCommentStore`; tables live in `packages/calls/schema.sql`.
* **Endpoints** (`case_access`; write not allowed for `guardian`):
  `GET /calls/{sid}/comments`, `POST` (`body`, `t_seconds`, `visibility`,
  `parent_id`, `mentions[]`), `PATCH`/`DELETE
  /calls/{sid}/comments/{id}` (author or admin),
  `POST /calls/{sid}/comments/{id}/resolve` `{resolved}`.
* **Read rule:** `visibility=org` OR you're the author OR you're
  mentioned; admin (and `dev_mode`) see all.
* **Console:** a **Review thread** panel on the call detail — threaded
  replies, an `@Ns` time chip, visibility selector, comma-separated
  mention emails, resolve/reopen, edit/delete for the author or an admin.
* **Deferred:** `@` autocomplete and moving the score-chart playhead to a
  comment's `t_seconds` (needs the transcript/audio timeline from P6/P7).

## Phase 4 — RBAC hierarchy + private calls *(shipped)*

**Goal:** "own → team → org" visibility, matching Gong / Dialpad.

* **`User.manager_id`** (nullable, same-org, `ON DELETE SET NULL`) +
  **`User.user_ref`** — links a login to the opaque id integrations send
  on `/ws/capture`. Both patched onto `users` with `ADD COLUMN IF NOT
  EXISTS`. `IdentityStore.set_manager` (rejects self / cross-org) /
  `set_user_ref`.
* **`packages/calls/access.py`** (pure, unit-tested):
  `report_refs(users, root_id)` — the `user_ref`s in a manager's report
  tree (cycle-safe); `can_view_call(...)` — visible unless `private`,
  where only owner / shared / `@mentioned` / admin get in (RingFence
  operators are a review team, so a *public* call is visible org-wide —
  the tree is a filter, not a wall); `in_scope(scope, …)` narrows to
  `mine` / `team` / `all`.
* **`call_ledger.private BOOL`** (default false) + **`call_shares`**
  `(session_id, shared_with_user, shared_by, shared_at)`, cascading with
  the call. `CallLedger` gains `set_private` / `share` / `unshare` /
  `shares`.
* **Endpoints:** `PATCH /calls/{sid} {private}` and
  `POST /calls/{sid}/share {email}` / `DELETE /calls/{sid}/share/{uid}`
  (owner or admin); `GET /calls?scope=mine|team|all` (default `all`);
  `GET /calls/{sid}` now 404s unless you may see it, and returns
  `private` / `shared_with` / `can_manage`. `PATCH /orgs/users/{id}
  {manager_id?, user_ref?}` (admin); `GET /orgs/users` carries both.
  Comment access (P3) is gated by the same rule.
* **Console:** an **Everyone / My team / Mine** scope switch on the Calls
  list; a lock badge + Make-private / Share panel on the call detail
  (when `can_manage`); an admin **Team** tab to set each user's manager
  and employee ref.
* **Tests:** `test_call_access.py` parametrises the matrix;
  `test_calls_gateway.py` covers private-hide/share/unshare, `scope=mine`
  / `team`, owner-or-admin-only management, and the `/orgs/users` fields.

## Phase 5 — access audit log *(shipped)*

**Goal:** an immutable record of who looked at what. A hard prerequisite
for Phase 7.

* **`packages/calls/audit.py`** — `AuditLog` sync Protocol
  (`InMemoryAuditLog` + `PgAuditLog`). **`call_access_log`**: `id`,
  `session_id` (`""` for a tenant-wide `list`), `tenant`, `actor_id` /
  `actor_email`, `action` (`list` | `view` | `play` | `download` |
  `comment` | `share` | `set_private`), `at`, `ip`. **Append-only** — the
  Protocol has no update or delete method, and the table has **no FK to
  `call_ledger`**, so the trail outlives the call.
* Written best-effort (never breaks the request) by `list_calls`,
  `get_call`, `add_comment`, `share_call` / `unshare_call`,
  `set_call_private`. Audio `play` / `download` join in Phase 7.
* **Endpoints** (admin only, 403 for others): `GET /calls/{sid}/access-log`
  and `GET /audit?actor=&action=&from=&to=&limit=` (tenant-wide).
* **Console:** an **Access log** panel on the call detail for admins.
* **Deferred to Phase 9:** a retention sweep (kept longer than call data).
* **Tests:** `test_call_audit.py` (record / for_call / query);
  `test_calls_gateway.py` — every read + mutation writes a row, admin-only,
  no delete route, `list` carries no session.

## Phase 6 — transcript for every call

**Goal:** the transcript is available for calls that never reached ALERT
(today only ALERT+ calls open a Case, and only then is the transcript
kept).

* **`call_transcript`**: `(session_id, seq, role, text, t)`. Written once
  per session on close, **only when `RF_RETAIN_TRANSCRIPTS=true`**
  (invariant #5 unchanged — the flag still governs disk).
* `GET /calls/{sid}` returns the transcript whether or not a Case exists;
  the Case transcript remains the source when there is one.
* **Console:** transcript inline with the score chart on the call detail;
  each turn's timestamp moves the playhead.
* **Tests:** flag off → table stays empty, `/calls/{sid}` transcript is
  `[]`, invariant suite green; flag on → round-trips.

## Phase 7 — full-conversation audio  *(T-7.0 legal gate)*

**Goal:** play back the actual call for verification.

RingFence is observer-only and stores no audio today. Recording a
two-party call is regulated (two-party-consent jurisdictions, GDPR,
wiretap); RingFence retaining it is a processing purpose separate from
the customer's own call flow and needs a legal basis + a DPA. Ships
opt-in and off by default.

* **`packages/storage/`** — `ObjectStore` Protocol; `LocalFsObjectStore`
  (default, a mounted volume) + `S3ObjectStore` (MinIO / S3), swapped by
  `RF_OBJECT_STORE=fs://… | s3://…`, same pattern as the other stores.
* **Capture:** in `capture()`, when `RF_RETAIN_AUDIO=true` **and** the
  tenant has `retain_audio: true` in `config/tenants.yaml`, tee each
  leg's PCM to a per-session temp WAV; on close, mix → encode Opus → put
  at `tenant/<yyyy-mm>/<session_id>.opus`; set `call_ledger.audio_key` +
  `audio_retain_until`.
* **Endpoint:** `GET /calls/{sid}/audio` — Range-capable stream,
  access-checked (Phase 4), **audit-logged** (Phase 5). `?download=1` →
  `Content-Disposition`.
* **Retention:** an hourly sweep in the app lifespan deletes objects past
  `audio_retain_until` and nulls `audio_key`.
* **Compose:** `minio` service — internal in prod, `:9000/:9001`
  published locally.
* **Config:** `RF_RETAIN_AUDIO`, `RF_OBJECT_STORE`,
  `RF_AUDIO_RETENTION_DAYS`; per-tenant `retain_audio`,
  `audio_retention_days`.
* **Console:** `<audio controls>` on the call detail sourced from the
  endpoint; playhead synced to the score chart and transcript.
* **Docs:** `docs/LEGAL_AUDIO.md` — the checklist counsel signs off
  (consent basis per region, DPA, retention max, data-subject
  access/erasure, sub-processor disclosure).
* **Tests:** store round-trip (both impls); capture writes nothing when
  the flag or the tenant opt-in is off; Range requests; access denied →
  403 + audit row; retention sweep deletes and nulls the key; an
  invariant test that no audio is persisted with `RF_RETAIN_AUDIO` unset.

## Phase 8 — live wall

**Goal:** a supervisor sees every call happening right now.

* **`GET /sessions`** — tenant-scoped: `session_id`, `api_key_id`,
  `user_ref`, `started_at`, current `state`/`score` (from the live
  `_Live` + last ledger score point).
* **`GET /events?tenant=`** — SSE with no `session_id`; the handler
  already subscribes to `rf.<tenant>.*` and then filters to one session,
  so this is dropping the filter for the no-id form.
* **Console:** a **Wall** view — a grid of live call cards, each a mini
  score gauge + state pill, linking to the live call detail.
* **Tests:** `/sessions` scoping; the tenant SSE streams multiple
  sessions; auth.

## Phase 9 — employee timeline + retention controls

* **Console:** an employee page — all their calls on one timeline, a
  peak-state histogram, their alert rate over time, their comment
  activity (Chorus / Dialpad "conversation history" model).
* **Per-tenant retention config** for ledger / transcript / audio, each
  enforced by its sweep; surfaced in an admin **Settings** view.
* **Tests:** each retention class is swept independently; timeline
  aggregation.

---

## Prior art

| Category | Products | What we take |
|---|---|---|
| Conversation intelligence | Gong, Chorus | Comments tab (timestamped, visibility, @mention-grants-access); permission profiles by org hierarchy; "set call private"; the person timeline |
| Real-time contact-center assist | Balto, Cresta, Observe.AI | Supervisor floor view = live status per agent + escalations at a glance (our live wall) |
| Compliance recording | NICE, Verint, Theta Lake | RBAC + full audit trail of reviewer actions; retention controls; annotatable review workspace |
| Enterprise call protection | Hiya Protect | Executive analytics: call trends, spam/fraud rates, adoption |
| VoIP / UCaaS | Dialpad, Aircall | Admin + Supervisor roles get the Call History dashboard; Company Admin sees all; role-based access + audit logs + retention |
