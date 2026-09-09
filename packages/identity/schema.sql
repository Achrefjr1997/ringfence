-- Identity store schema (T-7.2a).
--
-- Idempotent: every statement is IF NOT EXISTS, so applying it to an
-- already-migrated database is a no-op.  There is no down-migration and no
-- version table yet -- when the schema needs to change incompatibly, that
-- is a new numbered file and a real migration runner, not an edit here.
--
-- Column shapes mirror packages/identity/models.py exactly.  The plaintext
-- of a password or an API key is never stored: only scrypt / sha256 digests.

CREATE TABLE IF NOT EXISTS orgs (
    id         TEXT        PRIMARY KEY,
    name       TEXT        NOT NULL,
    tenant     TEXT        NOT NULL,
    created_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id            TEXT        PRIMARY KEY,
    org_id        TEXT        NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    email         TEXT        NOT NULL UNIQUE,
    password_hash TEXT        NOT NULL,
    role          TEXT        NOT NULL,
    verified      BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at    DOUBLE PRECISION NOT NULL
);

-- call-oversight P4: manager tree + the employee ref an integration passes
ALTER TABLE users ADD COLUMN IF NOT EXISTS manager_id TEXT REFERENCES users (id) ON DELETE SET NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS user_ref TEXT;

CREATE INDEX IF NOT EXISTS users_org_id_idx ON users (org_id);
CREATE INDEX IF NOT EXISTS users_manager_id_idx ON users (manager_id);

CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT        PRIMARY KEY,
    org_id       TEXT        NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    name         TEXT        NOT NULL,
    prefix       TEXT        NOT NULL,
    key_hash     TEXT        NOT NULL UNIQUE,
    created_at   DOUBLE PRECISION NOT NULL,
    last_used_at DOUBLE PRECISION,
    revoked      BOOLEAN     NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS api_keys_org_id_idx ON api_keys (org_id);
