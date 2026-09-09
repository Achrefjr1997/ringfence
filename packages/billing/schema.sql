-- Billing schema (T-7.3).
-- Idempotent, same rules as the identity / case schemas.

CREATE TABLE IF NOT EXISTS org_billing (
    tenant             TEXT             PRIMARY KEY,
    plan               TEXT             NOT NULL DEFAULT 'pilot',
    stripe_customer_id TEXT,
    updated_at         DOUBLE PRECISION NOT NULL
);

-- One row per (tenant, billing month, metric); quantity is upserted with
-- an atomic += so concurrent gateway workers cannot lose a write.
CREATE TABLE IF NOT EXISTS usage_counters (
    tenant   TEXT             NOT NULL,
    period   TEXT             NOT NULL,   -- YYYY-MM, UTC
    metric   TEXT             NOT NULL,   -- call_minutes | calls | guardian_notifications
    quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant, period, metric)
);

CREATE INDEX IF NOT EXISTS usage_counters_tenant_period_idx
    ON usage_counters (tenant, period);

-- Same counters, attributed to the API key a session was admitted with, so
-- an admin can see which integration is spending minutes.  Visibility only --
-- billing still reads usage_counters.  Sessions admitted without a key
-- (dev mode) contribute nothing here.
CREATE TABLE IF NOT EXISTS usage_key_counters (
    tenant   TEXT             NOT NULL,
    key_id   TEXT             NOT NULL,
    period   TEXT             NOT NULL,   -- YYYY-MM, UTC
    metric   TEXT             NOT NULL,   -- call_minutes | calls | guardian_notifications
    quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant, key_id, period, metric)
);

CREATE INDEX IF NOT EXISTS usage_key_counters_tenant_period_idx
    ON usage_key_counters (tenant, period);
