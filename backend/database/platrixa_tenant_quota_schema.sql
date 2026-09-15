-- Platrixa Phase 16 — Metered Developer Gate: tenant quota table
--
-- Repository migration convention: the repository has no Alembic; schema
-- changes ship as version-controlled SQL DDL files (see fyjc_schema.sql,
-- constraints.sql) applied by idempotent init scripts.
--
-- This file is IDEMPOTENT and safe to re-run against production.

CREATE TABLE IF NOT EXISTS platrixa_tenant_quotas (
    api_key_hash        VARCHAR(64)  PRIMARY KEY,
    tenant_id           VARCHAR(64)  NOT NULL,
    monthly_limit       INTEGER      NOT NULL DEFAULT 100,
    current_month_usage INTEGER      NOT NULL DEFAULT 0,
    usage_month         VARCHAR(7)   NOT NULL,
    is_active           BOOLEAN      NOT NULL DEFAULT TRUE,
    last_request_at     TIMESTAMPTZ  NULL
);

-- Admission predicate support: the atomic reservation filters on
-- (api_key_hash, is_active, usage_month, current_month_usage < monthly_limit);
-- the primary key already serves the equality lookups.
CREATE INDEX IF NOT EXISTS idx_platrixa_tenant_quotas_tenant_id
    ON platrixa_tenant_quotas (tenant_id);

CREATE INDEX IF NOT EXISTS idx_platrixa_tenant_quotas_usage_month
    ON platrixa_tenant_quotas (usage_month);

-- Data integrity: the hash MUST be a 64-char lowercase SHA-256 hex digest,
-- and the month bucket MUST be a "YYYY-MM" string.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_platrixa_quota_hash_format'
    ) THEN
        ALTER TABLE platrixa_tenant_quotas
            ADD CONSTRAINT ck_platrixa_quota_hash_format
            CHECK (api_key_hash ~ '^[0-9a-f]{64}$');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_platrixa_quota_month_format'
    ) THEN
        ALTER TABLE platrixa_tenant_quotas
            ADD CONSTRAINT ck_platrixa_quota_month_format
            CHECK (usage_month ~ '^[0-9]{4}-[0-9]{2}$');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_platrixa_quota_nonneg'
    ) THEN
        ALTER TABLE platrixa_tenant_quotas
            ADD CONSTRAINT ck_platrixa_quota_nonneg
            CHECK (current_month_usage >= 0 AND monthly_limit >= 0);
    END IF;
END $$;
