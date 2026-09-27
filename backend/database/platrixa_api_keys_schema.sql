-- Platrixa Phase 5G — Developer API-key lifecycle registry
--
-- Companion to platrixa_tenant_quota_schema.sql (Phase 16). The runtime
-- admission credential STILL lives in platrixa_tenant_quotas (keyed by
-- SHA-256 hash, is_active flag); this table adds the durable lifecycle
-- metadata (name, environment, status, timestamps) that the developer
-- key-management API exposes.
--
-- SECURITY INVARIANTS (enforced here and in backend/auth/api_keys.py):
--   * The raw key is NEVER stored — only its SHA-256 hash.
--   * key_hash is UNIQUE across the whole system: one credential record
--     per secret, ever.
--   * tenant_id is present on every row; every management query filters
--     by tenant_id (tenant scoping enforced in code, never by the client).
--   * A revoked key never authenticates: revoke/rotate atomically flip
--     BOTH this row's status and the companion quota row's is_active
--     (single transaction in backend/auth/api_keys.py).

CREATE TABLE IF NOT EXISTS platrixa_api_keys (
    id            VARCHAR(36)  PRIMARY KEY,
    tenant_id     VARCHAR(64)  NOT NULL,
    name          VARCHAR(120) NOT NULL,
    key_prefix    VARCHAR(20)  NOT NULL,
    key_hash      VARCHAR(64)  NOT NULL,
    environment   VARCHAR(20)  NOT NULL DEFAULT 'test',
    status        VARCHAR(20)  NOT NULL DEFAULT 'ACTIVE',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_used_at  TIMESTAMPTZ  NULL,
    revoked_at    TIMESTAMPTZ  NULL
);

-- One credential record per secret, ever. Also the runtime lookup index
-- (hash → registry row) for the resolve path.
CREATE UNIQUE INDEX IF NOT EXISTS ux_platrixa_api_keys_hash
    ON platrixa_api_keys (key_hash);

CREATE INDEX IF NOT EXISTS idx_platrixa_api_keys_tenant
    ON platrixa_api_keys (tenant_id);

-- Tenant-scoped listing ordered created_at DESC, id DESC (deterministic).
CREATE INDEX IF NOT EXISTS idx_platrixa_api_keys_tenant_created
    ON platrixa_api_keys (tenant_id, created_at DESC, id DESC);

-- Data integrity: hash format, closed status/environment vocabularies,
-- and revocation consistency (ACTIVE ⇔ no revoked_at).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_platrixa_api_keys_hash_format'
    ) THEN
        ALTER TABLE platrixa_api_keys
            ADD CONSTRAINT ck_platrixa_api_keys_hash_format
            CHECK (key_hash ~ '^[0-9a-f]{64}$');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_platrixa_api_keys_status'
    ) THEN
        ALTER TABLE platrixa_api_keys
            ADD CONSTRAINT ck_platrixa_api_keys_status
            CHECK (status IN ('ACTIVE', 'REVOKED'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_platrixa_api_keys_env'
    ) THEN
        ALTER TABLE platrixa_api_keys
            ADD CONSTRAINT ck_platrixa_api_keys_env
            CHECK (environment IN ('test', 'live'));
    END IF;

    -- Revocation consistency: ACTIVE keys have no revoked_at; REVOKED
    -- keys always carry the timestamp.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_platrixa_api_keys_revocation'
    ) THEN
        ALTER TABLE platrixa_api_keys
            ADD CONSTRAINT ck_platrixa_api_keys_revocation
            CHECK (
                (status = 'ACTIVE' AND revoked_at IS NULL)
                OR (status = 'REVOKED' AND revoked_at IS NOT NULL)
            );
    END IF;
END $$;
