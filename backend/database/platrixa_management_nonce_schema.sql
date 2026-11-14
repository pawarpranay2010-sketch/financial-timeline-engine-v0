-- Platrixa — Management gateway assertion replay protection
--
-- One row per consumed (issuer, nonce) pair. The nonce is stored ONLY as a
-- SHA-256 hex digest: the raw nonce never reaches the database.
--
-- Kept deliberately SEPARATE from visitor_assertion_nonces: operator
-- management and visitor authentication are different credential planes and
-- must never share a replay namespace.

CREATE TABLE IF NOT EXISTS management_assertion_nonces (
    issuer     TEXT        NOT NULL,
    nonce_hash TEXT        NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (issuer, nonce_hash)
);

-- Supports bounded cleanup of expired rows.
CREATE INDEX IF NOT EXISTS idx_management_assertion_nonces_expires
    ON management_assertion_nonces (expires_at);
