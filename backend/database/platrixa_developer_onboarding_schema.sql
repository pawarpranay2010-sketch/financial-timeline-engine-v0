-- Platrixa — Self-service developer onboarding (v1)
--
-- Repository migration convention: no Alembic; version-controlled, IDEMPOTENT
-- DDL applied by an init script (backend/auth/init_onboarding.py). Safe to run
-- repeatedly against production. No destructive statement appears here: no
-- DROP, no TRUNCATE, no ALTER of existing tables.
--
-- The tables deliberately REUSE the existing tenancy model: a developer
-- tenant is the ``tenant_id`` string carried by ``platrixa_api_keys`` and
-- ``platrixa_tenant_quotas`` (Phase 5G / Phase 16). There is no new tenants
-- table and no second credential format.
--
-- SECURITY INVARIANTS:
--   * No plaintext API key is ever stored. ``platrixa_api_keys.key_hash``
--     and ``platrixa_tenant_quotas.api_key_hash`` hold SHA-256 digests only.
--   * A one-time delivery row holds the PUBLIC nonce used to derive the key
--     server-side, never the key itself, and the nonce is zeroed on reveal or
--     expiry (so the key stops being derivable at all).
--   * Verification tokens are stored as SHA-256 hashes only.
--   * One email address can hold at most one OPEN application, and one tenant
--     can be provisioned at most once (both are database constraints).
--
-- Portability: every statement below is accepted by PostgreSQL and by SQLite,
-- so the SAME schema file backs production and the test store. Timestamps are
-- supplied by the application (no now() defaults) so both engines agree.

CREATE TABLE IF NOT EXISTS platrixa_developer_access_requests (
    id                    VARCHAR(36)  PRIMARY KEY,
    email                 VARCHAR(320) NOT NULL,
    name                  VARCHAR(120) NOT NULL,
    company               VARCHAR(160) NOT NULL,
    building              VARCHAR(1000) NOT NULL,
    expected_usage        VARCHAR(40)  NOT NULL,
    profile_url           VARCHAR(300) NULL,
    status                VARCHAR(32)  NOT NULL,
    status_reason         VARCHAR(64)  NULL,
    email_verified_at     TIMESTAMP    NULL,
    tenant_id             VARCHAR(64)  NULL,
    key_id                VARCHAR(36)  NULL,
    monthly_limit         INTEGER      NULL,
    client_ip_hash        VARCHAR(64)  NULL,
    visitor_id            VARCHAR(64)  NULL,
    provisioning_attempts INTEGER      NOT NULL DEFAULT 0,
    last_error            VARCHAR(120) NULL,
    created_at            TIMESTAMP    NOT NULL,
    updated_at            TIMESTAMP    NOT NULL,
    expires_at            TIMESTAMP    NOT NULL,
    CONSTRAINT ck_dev_req_status CHECK (
        status IN (
            'PENDING_VERIFICATION', 'VERIFIED', 'PROVISIONING', 'ACTIVE',
            'REVIEW_REQUIRED', 'REJECTED', 'EXPIRED', 'SUSPENDED'
        )
    ),
    CONSTRAINT ck_dev_req_email_lower CHECK (email = lower(email)),
    CONSTRAINT ck_dev_req_usage CHECK (
        expected_usage IN ('hobby', 'small', 'medium', 'large', 'unspecified')
    )
);

-- One OPEN application per email address. Closed states (REJECTED, EXPIRED,
-- SUSPENDED) are outside the index, so a developer may re-apply after a
-- rejection or expiry — but never while an application is live.
CREATE UNIQUE INDEX IF NOT EXISTS ux_dev_req_email_open
    ON platrixa_developer_access_requests (email)
    WHERE status IN (
        'PENDING_VERIFICATION', 'VERIFIED', 'PROVISIONING', 'ACTIVE',
        'REVIEW_REQUIRED'
    );

-- One tenant per application, and (by the global tenant uniqueness below)
-- one application per tenant.
CREATE UNIQUE INDEX IF NOT EXISTS ux_dev_req_tenant
    ON platrixa_developer_access_requests (tenant_id)
    WHERE tenant_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_dev_req_status_created
    ON platrixa_developer_access_requests (status, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_dev_req_created
    ON platrixa_developer_access_requests (created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_dev_req_ip_created
    ON platrixa_developer_access_requests (client_ip_hash, created_at);

CREATE TABLE IF NOT EXISTS platrixa_developer_email_verifications (
    id             VARCHAR(36) PRIMARY KEY,
    application_id VARCHAR(36) NOT NULL,
    token_hash     VARCHAR(64) NOT NULL,
    created_at     TIMESTAMP   NOT NULL,
    expires_at     TIMESTAMP   NOT NULL,
    consumed_at    TIMESTAMP   NULL,
    attempts       INTEGER     NOT NULL DEFAULT 0,
    CONSTRAINT fk_dev_verif_app FOREIGN KEY (application_id)
        REFERENCES platrixa_developer_access_requests (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_dev_verif_token
    ON platrixa_developer_email_verifications (token_hash);

-- At most one live (unconsumed) token per application: a resend supersedes
-- the previous link instead of leaving two valid tokens in circulation.
CREATE UNIQUE INDEX IF NOT EXISTS ux_dev_verif_open
    ON platrixa_developer_email_verifications (application_id)
    WHERE consumed_at IS NULL;

CREATE TABLE IF NOT EXISTS platrixa_developer_key_deliveries (
    key_id            VARCHAR(36) PRIMARY KEY,
    application_id    VARCHAR(36) NULL,
    tenant_id         VARCHAR(64) NOT NULL,
        key_prefix     VARCHAR(20)  NOT NULL,
    key_hash       VARCHAR(64)  NOT NULL,
    key_nonce      VARCHAR(64)  NULL,
    reveal_token_hash VARCHAR(64) NOT NULL,
    -- Versioned derivation secret (see PLATRIXA_KEY_DELIVERY_SECRET_VERSION).
    -- Rows written before versioning default to 1 and stay revealable while a
    -- previous secret is configured, so rotating the secret never strands a
    -- key inside its delivery window.
    secret_version INTEGER      NOT NULL DEFAULT 1,
    created_at     TIMESTAMP    NOT NULL,
    expires_at        TIMESTAMP   NOT NULL,
    revealed_at       TIMESTAMP   NULL,
    voided_at         TIMESTAMP   NULL,
    CONSTRAINT fk_dev_delivery_app FOREIGN KEY (application_id)
        REFERENCES platrixa_developer_access_requests (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_dev_delivery_reveal
    ON platrixa_developer_key_deliveries (reveal_token_hash);

-- At most ONE live (unrevealed, unvoided) delivery per application. History
-- rows from rotation stay in the table so the audit trail survives.
CREATE UNIQUE INDEX IF NOT EXISTS ux_dev_delivery_app_live
    ON platrixa_developer_key_deliveries (application_id)
    WHERE application_id IS NOT NULL
      AND revealed_at IS NULL
      AND voided_at IS NULL;

CREATE TABLE IF NOT EXISTS platrixa_developer_notifications (
    id             VARCHAR(36)  PRIMARY KEY,
    application_id VARCHAR(36)  NULL,
    audience       VARCHAR(16)  NOT NULL,
    kind           VARCHAR(48)  NOT NULL,
    recipient      VARCHAR(320) NULL,
    subject        VARCHAR(200) NOT NULL,
    body_text      TEXT         NOT NULL,
    status         VARCHAR(16)  NOT NULL,
    attempts       INTEGER      NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMP   NULL,
    last_error     VARCHAR(80)  NULL,
    dedupe_key     VARCHAR(200) NOT NULL,
    created_at     TIMESTAMP    NOT NULL,
    sent_at        TIMESTAMP    NULL,
    -- Dispatcher claim metadata: only the holder of claim_token may
    -- acknowledge; a crashed worker's row is recovered when the lease expires.
    claimed_at        TIMESTAMP NULL,
    claim_token       VARCHAR(36) NULL,
    lease_expires_at  TIMESTAMP NULL,
    CONSTRAINT ck_dev_notif_audience CHECK (audience IN ('DEVELOPER', 'OWNER')),
    CONSTRAINT ck_dev_notif_status CHECK (
        status IN ('PENDING', 'SENDING', 'SENT', 'FAILED', 'SUPPRESSED')
    )
);

-- Exactly-once notification creation per logical event, so retried
-- provisioning can never send the owner the same message twice.
CREATE UNIQUE INDEX IF NOT EXISTS ux_dev_notif_dedupe
    ON platrixa_developer_notifications (dedupe_key);

CREATE INDEX IF NOT EXISTS idx_dev_notif_due
    ON platrixa_developer_notifications (status, next_attempt_at);

CREATE INDEX IF NOT EXISTS idx_dev_notif_created
    ON platrixa_developer_notifications (created_at DESC, id DESC);

-- Stale-claim recovery scans by expiry, never by created_at.
CREATE INDEX IF NOT EXISTS idx_dev_notif_lease
    ON platrixa_developer_notifications (status, lease_expires_at);

-- One opaque reference per ACCEPTED SUBMISSION. It is the capability used by
-- GET /v1/developer/access-status; it is NOT the application id, NOT a tenant
-- id and NOT an authentication token, and it grants nothing else.
CREATE TABLE IF NOT EXISTS platrixa_developer_submission_handles (
    id             VARCHAR(36)  PRIMARY KEY,
    application_id VARCHAR(36)  NOT NULL,
    email          VARCHAR(320) NOT NULL,
    created_at     TIMESTAMP    NOT NULL,
    last_used_at   TIMESTAMP    NULL,
    revoked_at     TIMESTAMP    NULL,
    CONSTRAINT ck_dev_handle_email_lower CHECK (email = lower(email))
);

CREATE INDEX IF NOT EXISTS idx_dev_handle_application
    ON platrixa_developer_submission_handles (application_id);

CREATE TABLE IF NOT EXISTS platrixa_developer_onboarding_audit (
    id             VARCHAR(36)  PRIMARY KEY,
    actor          VARCHAR(128) NOT NULL,
    action         VARCHAR(64)  NOT NULL,
    application_id VARCHAR(36)  NULL,
    tenant_id      VARCHAR(64)  NULL,
    detail         VARCHAR(500) NULL,
    created_at     TIMESTAMP    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dev_audit_created
    ON platrixa_developer_onboarding_audit (created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_dev_audit_app
    ON platrixa_developer_onboarding_audit (application_id, created_at DESC);
