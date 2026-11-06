-- ISR Mission Planner — Migration 001
-- Make the `sessions` table mission-aware (Phase 0 of the Agent Dynamic-Mission /
-- per-mission memory work). See docs/ISR_PLANNER_DESCRIPTION.md §1a-0 and §7.
--
-- Run this in the Supabase SQL Editor AFTER the base schema
-- (docs/architecture/supabase_schema.sql) has been applied.
--
-- Idempotent: safe to run more than once (ADD COLUMN IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS). It only ADDS to `sessions`; it does not touch
-- `conversations` or `mission_plans`, which already fit the Mission model as-is.
--
-- Rationale for reusing `sessions` as the Mission container:
--   sessions      ≈ the Mission (identity, lifecycle, owns the environment)
--   conversations ≈ conversation recall            (ready as-is)
--   mission_plans ≈ the solutions for the mission  (ready as-is)

-- ---------------------------------------------------------------------------
-- 1. Mission identity + lifecycle + environment ownership on `sessions`
-- ---------------------------------------------------------------------------

-- Officer-declared mission name/number (§1a-0 identity).
ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS name TEXT;

-- Explicit lifecycle: a mission is 'active' until the officer ends it.
-- (§1a-0: missions end by explicit action, NOT by env/cfg-hash mismatch.)
ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'active';

-- When the mission was explicitly ended (NULL while active).
ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS ended_at TIMESTAMPTZ;

-- The Mission OWNS the environment (§1a-0). Stored as JSONB so the full
-- scenario (airports, targets, sams, ...) and drone configs travel with the
-- mission and survive restarts (replacing today's RAM-only MISSION_STORE).
ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS env JSONB;

ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS drone_configs JSONB;

-- Continuity bookkeeping (NOT identity): the env/cfg hashes are kept for
-- detecting changes/attempts, but they must no longer mint a new mission.
ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS env_hash TEXT;

ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS cfg_hash TEXT;

-- Constrain status to the known values. Guarded so re-runs don't error.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'sessions_status_check'
    ) THEN
        ALTER TABLE sessions
            ADD CONSTRAINT sessions_status_check
            CHECK (status IN ('active', 'ended'));
    END IF;
END$$;

-- ---------------------------------------------------------------------------
-- 2. Indexes for the common Mission lookups
-- ---------------------------------------------------------------------------

-- "Show me the active mission(s)" and lifecycle filtering.
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);

-- Lookup by declared mission name/number.
CREATE INDEX IF NOT EXISTS idx_sessions_name ON sessions(name);

-- ---------------------------------------------------------------------------
-- Notes
-- ---------------------------------------------------------------------------
-- * `conversations` and `mission_plans` already reference sessions(id) and need
--   no change. Each agent turn appends to `conversations`; each accepted solve
--   appends to `mission_plans`. The future constraint ledger (§1e) can live in
--   sessions.metadata JSONB initially, or graduate to its own table later.
-- * `agent_runs` (mission_ledger.py) stays as-is for per-run decision traces;
--   it already carries a mission_id that should point at sessions(id).
