-- Call ledger schema (oversight console P1).
-- Idempotent, same rules as the identity / case / billing schemas.
-- Metadata only -- no transcript, no audio.

CREATE TABLE IF NOT EXISTS call_ledger (
    session_id  TEXT             PRIMARY KEY,
    tenant      TEXT             NOT NULL,
    api_key_id  TEXT,                         -- NULL for dev-mode sessions
    user_ref    TEXT,                         -- employee id the integration passed (opaque)
    user_label  TEXT,                         -- optional display name for user_ref
    started_at  DOUBLE PRECISION NOT NULL,
    ended_at    DOUBLE PRECISION,             -- NULL while the call is live
    peak_state  TEXT             NOT NULL DEFAULT 'CALM',
    peak_score  DOUBLE PRECISION NOT NULL DEFAULT 0,
    leg_count   INTEGER          NOT NULL DEFAULT 0,
    turn_count  INTEGER          NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS call_ledger_tenant_started_idx
    ON call_ledger (tenant, started_at DESC);
CREATE INDEX IF NOT EXISTS call_ledger_key_started_idx
    ON call_ledger (tenant, api_key_id, started_at DESC);
-- P2: added after call_ledger shipped, so patch an existing table too.
ALTER TABLE call_ledger ADD COLUMN IF NOT EXISTS user_ref TEXT;
ALTER TABLE call_ledger ADD COLUMN IF NOT EXISTS user_label TEXT;
-- P4: a private call is hidden from managers (owner + shares + admin only).
ALTER TABLE call_ledger ADD COLUMN IF NOT EXISTS private BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS call_ledger_user_started_idx
    ON call_ledger (tenant, user_ref, started_at DESC);

-- P4: explicit per-call grants -- who a call was shared with, beyond the
-- role/hierarchy rule.
CREATE TABLE IF NOT EXISTS call_shares (
    session_id       TEXT             NOT NULL REFERENCES call_ledger (session_id) ON DELETE CASCADE,
    shared_with_user TEXT             NOT NULL,
    shared_by        TEXT             NOT NULL,
    shared_at        DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (session_id, shared_with_user)
);

-- One point per decision: the escalation graph the console draws. Kept for
-- every call, not just ALERT+, so score/state only -- never call content.
CREATE TABLE IF NOT EXISTS call_scores (
    session_id  TEXT             NOT NULL REFERENCES call_ledger (session_id) ON DELETE CASCADE,
    t           DOUBLE PRECISION NOT NULL,    -- seconds into the call
    score       DOUBLE PRECISION NOT NULL,
    state       TEXT             NOT NULL,
    PRIMARY KEY (session_id, t)
);

-- P3: threaded review comments. A reviewer's words, not call content --
-- retained regardless of RF_RETAIN_TRANSCRIPTS.
CREATE TABLE IF NOT EXISTS call_comments (
    id           TEXT             PRIMARY KEY,
    session_id   TEXT             NOT NULL REFERENCES call_ledger (session_id) ON DELETE CASCADE,
    tenant       TEXT             NOT NULL,
    author_id    TEXT             NOT NULL,
    author_email TEXT             NOT NULL,
    body         TEXT             NOT NULL,
    visibility   TEXT             NOT NULL DEFAULT 'org',   -- org | private | mentions
    t_seconds    DOUBLE PRECISION,                          -- NULL = general, else a moment
    parent_id    TEXT             REFERENCES call_comments (id) ON DELETE CASCADE,
    created_at   DOUBLE PRECISION NOT NULL,
    edited_at    DOUBLE PRECISION,
    resolved_at  DOUBLE PRECISION,
    resolved_by  TEXT
);

CREATE INDEX IF NOT EXISTS call_comments_session_idx
    ON call_comments (session_id, created_at);

CREATE TABLE IF NOT EXISTS call_comment_mentions (
    comment_id        TEXT NOT NULL REFERENCES call_comments (id) ON DELETE CASCADE,
    mentioned_user_id TEXT NOT NULL,
    PRIMARY KEY (comment_id, mentioned_user_id)
);

-- P5: append-only access audit -- who viewed / discussed / re-shared a
-- call. No update or delete path (kept longer than the call itself), and
-- NOT ON DELETE CASCADE: the trail outlives the ledger row.
CREATE TABLE IF NOT EXISTS call_access_log (
    id           TEXT             PRIMARY KEY,
    session_id   TEXT             NOT NULL,   -- "" for a tenant-wide action (list)
    tenant       TEXT             NOT NULL,
    actor_id     TEXT             NOT NULL,
    actor_email  TEXT             NOT NULL,
    action       TEXT             NOT NULL,   -- list|view|play|download|comment|share|set_private
    at           DOUBLE PRECISION NOT NULL,
    ip           TEXT
);

CREATE INDEX IF NOT EXISTS call_access_log_session_idx ON call_access_log (session_id, at DESC);
CREATE INDEX IF NOT EXISTS call_access_log_tenant_idx ON call_access_log (tenant, at DESC);
