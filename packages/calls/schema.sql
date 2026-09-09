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

CREATE INDEX IF NOT EXISTS call_ledger_user_started_idx
    ON call_ledger (tenant, user_ref, started_at DESC);

-- One point per decision: the escalation graph the console draws. Kept for
-- every call, not just ALERT+, so score/state only -- never call content.
CREATE TABLE IF NOT EXISTS call_scores (
    session_id  TEXT             NOT NULL REFERENCES call_ledger (session_id) ON DELETE CASCADE,
    t           DOUBLE PRECISION NOT NULL,    -- seconds into the call
    score       DOUBLE PRECISION NOT NULL,
    state       TEXT             NOT NULL,
    PRIMARY KEY (session_id, t)
);
