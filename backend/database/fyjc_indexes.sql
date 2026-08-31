-- ============================================
-- FYJC Accounting AI — Indexes
-- ============================================
-- These indexes support the core query patterns:
--   - Look up all interpretations for a student interaction
--   - Find training candidates by status/category
--   - Deduplicate by content_hash
--   - Query knowledge base by pattern/type/scope
--   - Filter exported vs pending candidates
-- ============================================


-- ------------------------------------------------
-- fyjc_interactions
-- ------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_fyjc_interactions_session
    ON fyjc_interactions(session_id);

CREATE INDEX IF NOT EXISTS idx_fyjc_interactions_board
    ON fyjc_interactions(board);

CREATE INDEX IF NOT EXISTS idx_fyjc_interactions_created
    ON fyjc_interactions(created_at);


-- ------------------------------------------------
-- fyjc_interpretations
-- ------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_fyjc_interpretations_interaction
    ON fyjc_interpretations(interaction_id);

CREATE INDEX IF NOT EXISTS idx_fyjc_interpretations_model
    ON fyjc_interpretations(model_id);

CREATE INDEX IF NOT EXISTS idx_fyjc_interpretations_kernel_status
    ON fyjc_interpretations(kernel_status);

CREATE INDEX IF NOT EXISTS idx_fyjc_interpretations_created
    ON fyjc_interpretations(created_at);

-- JSONB index on ambiguity_flags for filtering
CREATE INDEX IF NOT EXISTS idx_fyjc_interpretations_ambiguity
    ON fyjc_interpretations USING GIN (ambiguity_flags);


-- ------------------------------------------------
-- fyjc_training_candidates
-- ------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_fyjc_candidates_problem_id
    ON fyjc_training_candidates(problem_id);

CREATE INDEX IF NOT EXISTS idx_fyjc_candidates_content_hash
    ON fyjc_training_candidates(content_hash);

CREATE INDEX IF NOT EXISTS idx_fyjc_candidates_status
    ON fyjc_training_candidates(status);

CREATE INDEX IF NOT EXISTS idx_fyjc_candidates_category
    ON fyjc_training_candidates(category);

CREATE INDEX IF NOT EXISTS idx_fyjc_candidates_interaction
    ON fyjc_training_candidates(interaction_id);

CREATE INDEX IF NOT EXISTS idx_fyjc_candidates_interpretation
    ON fyjc_training_candidates(interpretation_id);

CREATE INDEX IF NOT EXISTS idx_fyjc_candidates_exported
    ON fyjc_training_candidates(exported_to_jsonl);

CREATE INDEX IF NOT EXISTS idx_fyjc_candidates_human_approved
    ON fyjc_training_candidates(human_approved);

CREATE INDEX IF NOT EXISTS idx_fyjc_candidates_export_batch
    ON fyjc_training_candidates(export_batch_id);

CREATE INDEX IF NOT EXISTS idx_fyjc_candidates_created
    ON fyjc_training_candidates(created_at);


-- ------------------------------------------------
-- fyjc_knowledge_base
-- ------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_fyjc_kb_knowledge_id
    ON fyjc_knowledge_base(knowledge_id);

CREATE INDEX IF NOT EXISTS idx_fyjc_kb_pattern
    ON fyjc_knowledge_base USING GIN (to_tsvector('english', pattern));

CREATE INDEX IF NOT EXISTS idx_fyjc_kb_type
    ON fyjc_knowledge_base(knowledge_type);

CREATE INDEX IF NOT EXISTS idx_fyjc_kb_scope
    ON fyjc_knowledge_base(scope);

CREATE INDEX IF NOT EXISTS idx_fyjc_kb_status
    ON fyjc_knowledge_base(status);

CREATE INDEX IF NOT EXISTS idx_fyjc_kb_created
    ON fyjc_knowledge_base(created_at);
