-- Platrixa — ExecutionEvidence persistence (Phase 18)
-- Locks a frozen versioned snapshot of one real execution's evidence,
-- with a chain digest binding consecutive records (append-only ledger).
--
-- Precedent: platrixa_tenant_quota_schema.sql (Phase 16) — same engine,
-- same init convention. Applied idempotently by
-- python -m backend.semantics.init_evidence_store.
--
-- Immutable-on-write: no UPDATE path exists. Rows are INSERT-only.

CREATE TABLE IF NOT EXISTS platrixa_execution_evidence (
    id                            BIGSERIAL PRIMARY KEY,
    request_id                    VARCHAR(128) NOT NULL,
    evidence_schema_version       VARCHAR(32) NOT NULL,
    input_hash                    CHAR(64) NOT NULL,
    candidate_interpretation_hash CHAR(64) NOT NULL,
    grounded_interpretation_hash  CHAR(64) NOT NULL,
    model_identity                JSONB NOT NULL DEFAULT '{}'::jsonb,
    adapter_identity              JSONB NOT NULL DEFAULT '{}'::jsonb,
    schema_version                VARCHAR(64) NOT NULL DEFAULT '',
    prompt_version                VARCHAR(64) NOT NULL DEFAULT '',
    grounding_version             VARCHAR(64) NOT NULL DEFAULT '',
    accounting_version            VARCHAR(92) NOT NULL DEFAULT '',
    rule_pack_hash                CHAR(64)  NOT NULL DEFAULT '',
    rule_evidence                 JSONB NOT NULL DEFAULT '[]'::jsonb,
    final_state                   VARCHAR(32) NOT NULL,
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    chain_digest                  CHAR(64) NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_execution_evidence_request_id
    ON platrixa_execution_evidence (request_id);

CREATE INDEX IF NOT EXISTS ix_execution_evidence_input_hash
    ON platrixa_execution_evidence (input_hash);
