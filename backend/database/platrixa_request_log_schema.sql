-- Platrixa Phase 5H — Request observability log (append-only metadata)
--
-- Companion to platrixa_tenant_quota_schema.sql. Records METADATA for
-- admitted data-plane requests so developers/operators can observe usage
-- and history. Deliberately NOT an analytics platform: one small
-- append-only table, tenant-scoped, metadata-only.
--
-- PRIVACY INVARIANTS:
--   * No raw financial input is ever stored here or returned in any
--     history list. The input payload stays in the request scope only.
--   * No raw API keys, no key hashes, no secrets.
--   * capability_id is recorded ONLY when the engine actually reported
--     one — never invented (it currently never is; the column simply
--     refuses to fabricate).
--   * Every read is tenant-scoped; attribution comes from the
--     authenticated tenant context, never from a request_id.
--
-- RETENTION (documented honestly):
--   * Request METADATA: retained by this table; operators may prune rows
--     older than REQUEST_LOG_RETENTION_DAYS (default 30). Pruning is an
--     explicit operator action (module function provided), not automatic
--     silent deletion.
--   * Idempotent RESULT snapshots remain governed by the Phase 5C
--     retention (72h) — this table never extends or fakes that window.

CREATE TABLE IF NOT EXISTS platrixa_request_log (
    id            BIGSERIAL    PRIMARY KEY,
    tenant_id     VARCHAR(64)  NOT NULL,
    key_prefix    VARCHAR(20)  NULL,
    endpoint      VARCHAR(64)  NOT NULL,
    request_id    VARCHAR(128) NULL,
    http_status   INTEGER      NOT NULL,
    api_status    VARCHAR(40)  NULL,
    reason_code   VARCHAR(80)  NULL,
    capability_id VARCHAR(120) NULL,
    duration_ms   INTEGER      NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Deterministic history ordering: newest first, stable tiebreaker.
CREATE INDEX IF NOT EXISTS idx_request_log_tenant_created
    ON platrixa_request_log (tenant_id, created_at DESC, id DESC);

-- Detail lookup: (tenant, request_id) — the only sanctioned lookup path.
CREATE INDEX IF NOT EXISTS idx_request_log_tenant_request
    ON platrixa_request_log (tenant_id, request_id);
