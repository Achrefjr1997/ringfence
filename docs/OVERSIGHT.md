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
| **3** | Threaded review (timestamped comments, @mention, visibility) | — | planned |
| **4** | RBAC hierarchy (own → team → org) + private calls + shares | — | planned |
| **5** | Access audit log (who viewed / played / exported which call) | — | planned — **precedes 7** |
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

## Phase 3 — threaded review

**Goal:** discuss a call at a specific moment, like Gong's Comments tab.

* **`call_comments`**: `id`, `session_id`, `tenant`, `t_seconds` (NULL =
  general), `author_user_id`, `body`, `visibility` (`org` | `private` |
  `mentions`), `parent_id` (NULL = top-level), `created_at`, `edited_at`,
  `resolved_at`.
* **`call_comment_mentions`**: `(comment_id, mentioned_user_id)`. A
  mention grants that user read access to the call (Gong behavior).
* **Endpoints:** `GET /calls/{sid}/comments`, `POST` (body + `t` +
  `visibility` + `parent_id`), `PATCH`/`DELETE /calls/{sid}/comments/{id}`
  (author or admin), `POST /calls/{sid}/comments/{id}/resolve`.
* **Read rule:** you see a comment if you can see the call (Phase 4) AND
  (`visibility=org` OR you're the author OR you're mentioned).
* **Console:** a comment rail on the call detail; clicking a comment moves
  the score-chart playhead to `t_seconds`; inline reply; visibility
  selector; `@` autocomplete over the tenant's known `user_ref`s.
* **Tests:** visibility matrix; mention-grants-access; resolve; edit/delete
  authz; comment on a call you can't see → 404.

## Phase 4 — RBAC hierarchy + private calls

**Goal:** "own → team → org" visibility, matching Gong / Dialpad.

* **`User.manager_id`** (nullable, same-org FK). A *team* is the
  transitive closure under a manager. A `User.user_ref` column links an
  identity account to the opaque ref integrations send in Phase 2 (set by
  the admin, or self-claimed via an invite).
* **Access rule** — a user may see call `c`:
  `role == admin`
  OR `c.user_ref == viewer.user_ref` (own)
  OR `c.user_ref` is in the viewer's report tree (manager)
  OR viewer is `@mentioned` on `c`
  OR `c` was explicitly shared with the viewer.
* **`call_ledger.private BOOL`** (default false): when true, managers are
  excluded — only owner + mentions + explicit shares. **Admin always sees**
  (Dialpad Company Admin). Marking private is the owner or an admin.
* **`call_shares`**: `(session_id, shared_with_user_id, shared_by, at)`.
* **Endpoints:** `POST /calls/{sid}/share {user_id}`, `PATCH /calls/{sid}
  {private: bool}`. `/calls` gains `scope=mine|team|all` (default `mine`
  for employees, `all` for admin).
* **Console:** scope switcher on the Calls list; a share dialog; a private
  toggle + lock icon on the call detail.
* **Tests:** one parametrized case per (viewer role, relationship,
  private?) cell of the access matrix — this is the whole test surface.

## Phase 5 — access audit log

**Goal:** an immutable record of who looked at what. Must land **before
Phase 7**.

* **`call_access_log`**: `id`, `session_id`, `tenant`, `actor_user_id`,
  `action` (`list` | `view` | `play` | `download` | `comment` |
  `share` | `set_private`), `at`, `ip`, `user_agent`. Append-only; no
  update/delete path.
* Written by `/calls`, `/calls/{sid}`, every comment/share/private change,
  and (Phase 7) every audio `play`/`download`.
* **Endpoints:** `GET /calls/{sid}/access-log` (admin), `GET /audit?from=
  &to=&actor=&action=` (admin, tenant-wide).
* **Retention:** kept longer than call data (default 2y, configurable) —
  compliance norm.
* **Tests:** every read/mutation writes exactly one row; log is not
  itself listable by non-admins; no delete route exists.

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
