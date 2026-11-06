-- ISR Mission Planner — Migration 002
-- Add request-text embeddings to agent_runs for RAG retrieval across past
-- missions (Phase 2 of the agent-learning-memory work; Phase 1, plain
-- capture into agent_runs.request_text, has been live since before this
-- migration). See server/database/mission_retrieval.py for the write/read
-- code this column supports.
--
-- Run this in the Supabase SQL Editor AFTER the base schema
-- (docs/architecture/supabase_schema.sql) has been applied.
--
-- Idempotent: safe to run more than once (CREATE EXTENSION IF NOT EXISTS,
-- ADD COLUMN IF NOT EXISTS, CREATE INDEX IF NOT EXISTS, CREATE OR REPLACE
-- FUNCTION). It only ADDS to agent_runs; no existing column is touched.
--
-- Embedding model: OpenAI text-embedding-3-small, 1536 dimensions. If you
-- later switch embedding models/dimensions, this column and index need a
-- new migration (vector dimensionality is fixed per column).

-- ---------------------------------------------------------------------------
-- 1. Enable pgvector (Supabase projects generally have this available;
--    this is a no-op if already enabled).
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- 2. Embedding column on agent_runs.
--    NULL for rows created before this migration, or for any run whose
--    request_text was empty (a question with no request text to embed) —
--    find_similar_runs() filters these out rather than erroring on them.
-- ---------------------------------------------------------------------------
ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS request_embedding vector(1536);

-- ---------------------------------------------------------------------------
-- 3. Approximate nearest-neighbor index for cosine-distance search.
--    ivfflat is the standard pgvector choice for this table size; lists=100
--    is a reasonable default for up to ~100k rows (Supabase docs' own
--    rule-of-thumb: lists ≈ rows/1000 for larger tables — revisit if this
--    table grows well past 100k rows).
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS agent_runs_embedding_idx
    ON agent_runs USING ivfflat (request_embedding vector_cosine_ops)
    WITH (lists = 100);

-- ---------------------------------------------------------------------------
-- 4. RPC function for similarity search, called via supabase-py's .rpc().
--    Returns the N closest past runs by cosine similarity, excluding the
--    run currently being created (there is none to exclude at write time,
--    but future callers may want to exclude a specific run_id) and any row
--    with a NULL embedding. similarity is 1 - cosine_distance (higher = more
--    similar), matching how most RAG code expects to reason about scores.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION match_agent_runs(
    query_embedding vector(1536),
    match_count INT DEFAULT 3,
    min_similarity FLOAT DEFAULT 0.5
)
RETURNS TABLE (
    id UUID,
    request_text TEXT,
    objective TEXT,
    hard_constraints JSONB,
    total_points NUMERIC,
    total_fuel_used NUMERIC,
    is_valid BOOLEAN,
    summary JSONB,
    created_at TIMESTAMPTZ,
    similarity FLOAT
)
LANGUAGE sql STABLE
AS $$
    SELECT
        agent_runs.id,
        agent_runs.request_text,
        agent_runs.objective,
        agent_runs.hard_constraints,
        agent_runs.total_points,
        agent_runs.total_fuel_used,
        agent_runs.is_valid,
        agent_runs.summary,
        agent_runs.created_at,
        1 - (agent_runs.request_embedding <=> query_embedding) AS similarity
    FROM agent_runs
    WHERE agent_runs.request_embedding IS NOT NULL
      AND 1 - (agent_runs.request_embedding <=> query_embedding) >= min_similarity
    ORDER BY agent_runs.request_embedding <=> query_embedding
    LIMIT match_count;
$$;
