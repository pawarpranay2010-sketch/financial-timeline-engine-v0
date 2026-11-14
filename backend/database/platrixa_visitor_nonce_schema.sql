-- Platrixa Phase 1B V2 — Visitor Assertion Replay Protection
-- Nonce store for atomic one-time consumption of visitor assertions.
-- Adapted from the existing management_assertion_nonces pattern.

CREATE TABLE IF NOT EXISTS visitor_assertion_nonces (
    nonce VARCHAR(128) PRIMARY KEY,
    asserted_at TIMESTAMPTZ DEFAULT NOW(),
    consumed BOOLEAN DEFAULT FALSE,
    expires_at TIMESTAMPTZ DEFAULT NOW() + INTERVAL '1 hour'
);

-- Index for cleanup of expired nonces
CREATE INDEX IF NOT EXISTS idx_visitor_assertion_nonces_expires
    ON visitor_assertion_nonces (expires_at);

-- Index for cleanup of consumed nonces
CREATE INDEX IF NOT EXISTS idx_visitor_assertion_nonces_consumed
    ON visitor_assertion_nonces (consumed)
    WHERE consumed = TRUE;