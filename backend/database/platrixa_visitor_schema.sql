-- Platrixa Phase 1B V2 — Visitor ownership
-- Add visitor_id and owner_type columns to platrixa_async_jobs for website visitor tracking
-- This DDL is idempotent and safe to run against production.
-- Run via: python -m backend.auth.init_visitor_schema (or manually apply)

-- Add visitor_id column (NULL for developer/API jobs; set for website jobs)
ALTER TABLE platrixa_async_jobs ADD COLUMN IF NOT EXISTS visitor_id UUID NULL;

-- Add owner_type column to distinguish job ownership
-- Values: 'website_visitor', 'developer_client', 'legacy'
ALTER TABLE platrixa_async_jobs ADD COLUMN IF NOT EXISTS owner_type VARCHAR(32) NULL
    DEFAULT 'legacy';

-- Index for visitor+tenant ownership queries (website jobs only)
CREATE INDEX IF NOT EXISTS idx_paj_visitor_tenant
    ON platrixa_async_jobs (visitor_id, tenant_id)
    WHERE visitor_id IS NOT NULL;

-- Index for tenant+visitor+status queries (admin/monitoring)
CREATE INDEX IF NOT EXISTS idx_paj_tenant_visitor_status
    ON platrixa_async_jobs (tenant_id, visitor_id, status)
    WHERE visitor_id IS NOT NULL;

-- Comment: website jobs store visitor_id from verified HMAC assertion.
-- developer/legacy jobs have visitor_id = NULL and owner_type set accordingly.
-- Website requests MUST NOT fall back to a NULL-tolerant match or a bare
-- visitor id comparison — they must exclusively query website-owned jobs.

-- Update existing records: set owner_type based on visitor_id
-- website jobs (visitor_id IS NOT NULL) → owner_type = 'website_visitor'
-- developer/legacy jobs (visitor_id IS NULL) → owner_type stays 'legacy'
UPDATE platrixa_async_jobs
SET owner_type = 'website_visitor'
WHERE visitor_id IS NOT NULL;

UPDATE platrixa_async_jobs
SET owner_type = 'legacy'
WHERE visitor_id IS NULL AND owner_type IS NULL;