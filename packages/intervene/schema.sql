-- Case store schema (T-7.2b).
--
-- Idempotent, same rules as packages/identity/schema.sql: every statement
-- is IF NOT EXISTS, no down-migration, a breaking change is a new file.
--
-- Invariant #5 (transcripts never on disk unless RF_RETAIN_TRANSCRIPTS=true):
--   * cases.transcript      -- JSONB, left NULL unless retention is on
--   * case_decisions.contributions -- verdict-level only (source/id/value/
--     role/t); the per-contribution `evidence` span and judge `detail`
--     text are dropped before write, never persisted.

CREATE TABLE IF NOT EXISTS cases (
    session_id    TEXT        PRIMARY KEY,
    tenant        TEXT        NOT NULL DEFAULT '',
    opened_at     DOUBLE PRECISION NOT NULL,
    feedback      TEXT,
    feedback_note TEXT        NOT NULL DEFAULT '',
    transcript    JSONB
);

CREATE INDEX IF NOT EXISTS cases_opened_at_idx ON cases (opened_at);

CREATE TABLE IF NOT EXISTS case_decisions (
    decision_id   TEXT        PRIMARY KEY,
    session_id    TEXT        NOT NULL REFERENCES cases (session_id) ON DELETE CASCADE,
    seq           BIGINT      NOT NULL,
    t             DOUBLE PRECISION NOT NULL,
    state         TEXT        NOT NULL,
    score         DOUBLE PRECISION NOT NULL,
    policy_pack   TEXT        NOT NULL,
    counterfactual TEXT,
    contributions JSONB       NOT NULL
);

CREATE INDEX IF NOT EXISTS case_decisions_session_seq_idx ON case_decisions (session_id, seq);
