-- ============================================
-- FYJC Accounting AI — Minimum Viable Schema
-- ============================================
-- 4 tables: interactions → interpretations → training_candidates + knowledge_base
-- Module 4 tables in schema.sql are NOT modified.
-- ============================================


-- ------------------------------------------------
-- FYJC Table 1: Interactions
-- Raw student input.  Write-once, never modified.
-- ------------------------------------------------

CREATE TABLE IF NOT EXISTS fyjc_interactions (
    id              SERIAL PRIMARY KEY,

    session_id      VARCHAR(64)
                        NULL
                        INDEX,

    raw_input       TEXT NOT NULL
                        COMMENT 'Exact student text, never normalised',

    board           VARCHAR(32)
                        NULL
                        COMMENT 'HSC / CBSE / ICSE',

    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);


-- ------------------------------------------------
-- FYJC Table 2: Interpretations
-- AI interpretation + kernel verdict.
-- One row per interpretation attempt.
-- ------------------------------------------------

CREATE TABLE IF NOT EXISTS fyjc_interpretations (
    id              SERIAL PRIMARY KEY,

    interaction_id  INTEGER NOT NULL
                        REFERENCES fyjc_interactions(id) ON DELETE CASCADE,

    model_id        VARCHAR(128) NOT NULL
                        COMMENT 'qwen2.5-1.5b-lora-p5a / deterministic-fallback / kernel-only',

    -- AI output fields (maps to AIInterpretation dataclass)

    transaction_type VARCHAR(32) NULL
                        COMMENT 'PURCHASE / SALE / EXPENSE / CAPITAL / etc.',

    parties         JSONB NULL
                        COMMENT '["Raj", "Sharma"]',

    amounts         JSONB NULL
                        COMMENT '["20000", "5000"] — strings for Decimal precision',

    payment_method  VARCHAR(32) NULL
                        COMMENT 'CASH / BANK / CHEQUE / CREDIT / UNKNOWN',

    ambiguity_flags JSONB NULL
                        COMMENT '["MISSING_PAYMENT_MODE"]',

    field_confidences JSONB NULL
                        COMMENT '[{field, confidence, grounding, source_text}]',

    raw_model_output TEXT NULL
                        COMMENT 'Verbatim model response before parsing',

    parse_success   BOOLEAN DEFAULT TRUE,

    -- Kernel verdict (merged — one interpretation → one verification)

    kernel_status   VARCHAR(32) NULL
                        COMMENT 'VERIFIED / REVIEW_REQUIRED / NOT_SUPPORTED / BLOCKED',

    reason_classification VARCHAR(64) NULL
                        COMMENT 'balanced_journal / UNRESOLVED_PRONOUN / etc.',

    journal_balanced BOOLEAN NULL,

    journal_narration TEXT NULL,

    debit_accounts  JSONB NULL
                        COMMENT '[{account, amount, rule}]',

    credit_accounts JSONB NULL
                        COMMENT '[{account, amount, rule}]',

    calculations    JSONB NULL
                        COMMENT '[{id, label, result}]',

    latency_ms      INTEGER NULL,

    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);


-- ------------------------------------------------
-- FYJC Table 3: Training Candidates
-- P4 lifecycle: candidate → validated/rejected/retired → export
-- ------------------------------------------------

CREATE TABLE IF NOT EXISTS fyjc_training_candidates (
    id              SERIAL PRIMARY KEY,

    interaction_id  INTEGER
                        REFERENCES fyjc_interactions(id) ON DELETE SET NULL,

    interpretation_id INTEGER
                        REFERENCES fyjc_interpretations(id) ON DELETE SET NULL,

    problem_id      VARCHAR(64) NOT NULL UNIQUE
                        COMMENT 'Deterministic hash from ProblemRecord',

    content_hash    VARCHAR(64) NULL
                        COMMENT 'Deduplication key across interactions',

    -- Classification

    category        VARCHAR(64) NULL
                        COMMENT 'cash_credit / settlement / gst / compound / etc.',

    subcategory     VARCHAR(64) NULL
                        COMMENT 'missing_payment_mode / explicit_cash / etc.',

    -- Lifecycle

    status          VARCHAR(32) NOT NULL DEFAULT 'CANDIDATE'
                        COMMENT 'CANDIDATE / VALIDATED / REJECTED / RETIRED / CONFLICTING',

    evidence_count  INTEGER NOT NULL DEFAULT 0,
    validation_count INTEGER NOT NULL DEFAULT 0,
    rejection_count INTEGER NOT NULL DEFAULT 0,
    source_diversity INTEGER NOT NULL DEFAULT 0,

    confidence      NUMERIC(5,4) NOT NULL DEFAULT 0.0000
                        COMMENT 'Deterministic confidence from evidence counts',

    -- Human approval

    human_approved  BOOLEAN NOT NULL DEFAULT FALSE,
    human_notes     TEXT NULL,
    human_approved_at TIMESTAMP NULL,

    -- Export

    exported_to_jsonl BOOLEAN NOT NULL DEFAULT FALSE,
    export_batch_id VARCHAR(64) NULL,
    exported_at     TIMESTAMP NULL,

    -- Provenance

    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    version         INTEGER NOT NULL DEFAULT 1
);


-- ------------------------------------------------
-- FYJC Table 4: Knowledge Base
-- Validated knowledge patterns.  Reusable rules/vocabulary.
-- ------------------------------------------------

CREATE TABLE IF NOT EXISTS fyjc_knowledge_base (
    id              SERIAL PRIMARY KEY,

    knowledge_id    VARCHAR(128) NOT NULL UNIQUE
                        COMMENT 'Deterministic from pattern+type+scope',

    pattern         TEXT NOT NULL
                        COMMENT 'The input pattern observed',

    canonical_interpretation TEXT NOT NULL
                        COMMENT 'What the pattern maps to',

    knowledge_type  VARCHAR(32) NOT NULL
                        COMMENT 'VOCABULARY / RULE / FORMULA / EDGE_CASE',

    scope           VARCHAR(32) NOT NULL DEFAULT 'PERSONAL'
                        COMMENT 'PERSONAL / CLASSROOM / SCHOOL / GLOBAL',

    status          VARCHAR(32) NOT NULL DEFAULT 'CANDIDATE'
                        COMMENT 'CANDIDATE / VALIDATED / REJECTED / RETIRED',

    confidence      NUMERIC(5,4) NOT NULL DEFAULT 0.0000,

    evidence_count  INTEGER NOT NULL DEFAULT 0,
    validation_count INTEGER NOT NULL DEFAULT 0,

    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    version         INTEGER NOT NULL DEFAULT 1
);
