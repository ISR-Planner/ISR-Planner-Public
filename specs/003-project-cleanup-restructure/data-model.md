# Data Model: Project Cleanup & Repository Restructure

**Phase 1 output** — Complete before→after file system map. This is the authoritative reference for all task implementation.

---

## Entities

### Root Directory

The root directory is the primary entity being transformed. Target state: ≤ 15 top-level items, all immediately recognisable.

**Before** (37 items): configuration, source, tests, documentation, artefacts, and mystery files all mixed together.

**After** (~20 non-hidden items):
```text
isr-planner/
├── CLAUDE.md                    # AI agent guidance (stays at root by design)
├── README.md                    # Rewritten project guide
├── orienteering_with_matrix.py  # Root-level solver (hard path dependency)
├── pyproject.toml               # NEW: Python project config + pytest settings
├── requirements.txt             # Python dependencies
├── .env.example                 # Environment variable template
├── .gitignore                   # Updated with artefact patterns
├── .flake8                      # Linting config
├── mypy.ini                     # Type-checker config (unchanged, stays at root)
├── Makefile                     # Build/run targets
├── Dockerfile                   # Production container
├── Dockerfile.railway           # Railway deployment container
├── docker-compose.yml           # Local container orchestration
├── railway.toml                 # Railway deployment config
├── run_planner.sh               # Server startup script
├── docs/                        # ALL project documentation
├── server/                      # Python backend (FastAPI)
├── webapp/                      # Frontend (HTML/CSS/JS)
├── path_planning_core/          # Path planning Python module
├── tests/                       # ALL automated tests
├── scripts/                     # Utility scripts
├── tools/                       # Developer tools
├── specs/                       # Feature specifications (.specify)
├── .github/                     # GitHub config
├── .specify/                    # Speckit config
└── .claude/                     # Claude AI config
```

---

## File Move Map

### Group 1: Root Documentation → `docs/` subdirectories

| Before | After | Action |
|--------|-------|--------|
| `CHECKPOINT_REPLAN_TODO.md` | `docs/session-notes/CHECKPOINT_REPLAN_TODO.md` | move |
| `CODE_AUDIT.md` | `docs/session-notes/CODE_AUDIT.md` | move |
| `DEPLOYMENT.md` | `docs/session-notes/DEPLOYMENT.md` | move |
| `DRONE_CONFIG_FLOW.md` | `docs/architecture/DRONE_CONFIG_FLOW.md` | move |
| `DRONE_CONFIG_QUICK_REF.md` | `docs/architecture/DRONE_CONFIG_QUICK_REF.md` | move |
| `HANDOFF_NOTES.md` | `docs/session-notes/HANDOFF_NOTES.md` | move |
| `README_ANALYSIS.md` | `docs/session-notes/README_ANALYSIS.md` | move |
| `SEGMENTED_IMPORT_REWRITE_NOTES.md` | `docs/session-notes/SEGMENTED_IMPORT_REWRITE_NOTES.md` | move |
| `SEGMENTED_MISSION_FIXES.md` | `docs/session-notes/SEGMENTED_MISSION_FIXES.md` | move |
| `first_two_bugs.md` | `docs/session-notes/first_two_bugs.md` | move |
| `ANALYSIS_SUMMARY.txt` | `docs/session-notes/ANALYSIS_SUMMARY.txt` | move |
| `supabase_schema.sql` | `docs/architecture/supabase_schema.sql` | move |

### Group 2: Existing `docs/` flat files → `docs/` subdirectories

| Before | After | Action |
|--------|-------|--------|
| `docs/AGENTIC_EXTENSION_PROPOSAL.md` | `docs/agents/AGENTIC_EXTENSION_PROPOSAL.md` | move |
| `docs/AGENTIC_SYSTEM_DESCRIPTION.md` | `docs/agents/AGENTIC_SYSTEM_DESCRIPTION.md` | move |
| `docs/Agent_Architecture_Summary.md` | `docs/architecture/Agent_Architecture_Summary.md` | move |
| `docs/CHECKPOINT_DISTANCE_MATRIX_CHANGES.md` | `docs/solver/CHECKPOINT_DISTANCE_MATRIX_CHANGES.md` | move |
| `docs/EXPECTED_BEHAVIORS.md` | `docs/solver/EXPECTED_BEHAVIORS.md` | move |
| `docs/ISR_Optimization_Tools_Enhanced.md` | `docs/solver/ISR_Optimization_Tools_Enhanced.md` | move |
| `docs/LOITER_AND_REFUELING_HANDOFF.md` | `docs/session-notes/LOITER_AND_REFUELING_HANDOFF.md` | move |
| `docs/MISSION_ORCHESTRATION_TOOLS.md` | `docs/agents/MISSION_ORCHESTRATION_TOOLS.md` | move |
| `docs/MODULAR_ARCHITECTURE.md` | `docs/architecture/MODULAR_ARCHITECTURE.md` | move |
| `docs/SEGMENT_ANIMATION_SYSTEM.md` | `docs/architecture/SEGMENT_ANIMATION_SYSTEM.md` | move |
| `docs/SEGMENT_SYSTEM_REDESIGN.md` | `docs/architecture/SEGMENT_SYSTEM_REDESIGN.md` | move |
| `docs/SESSION_SUMMARY_JAN18.md` | `docs/session-notes/SESSION_SUMMARY_JAN18.md` | move |
| `docs/SESSION_SUMMARY_JAN18_PART2.md` | `docs/session-notes/SESSION_SUMMARY_JAN18_PART2.md` | move |
| `docs/Single_Agent_Architecture.md` | `docs/architecture/Single_Agent_Architecture.md` | move |
| `docs/SWAP_CLOSER_CASCADE.md` | `docs/solver/SWAP_CLOSER_CASCADE.md` | move |
| `docs/SYSTEM_ARCHITECTURE.md` | `docs/architecture/SYSTEM_ARCHITECTURE.md` | move |
| `docs/V3_TO_V4_MIGRATION.md` | `docs/agents/V3_TO_V4_MIGRATION.md` | move |
| `docs/V3_VS_V4_COMPARISON.md` | `docs/agents/V3_VS_V4_COMPARISON.md` | move |
| `docs/V4_IMPLEMENTATION_COMPLETE.md` | `docs/agents/V4_IMPLEMENTATION_COMPLETE.md` | move |
| `docs/generate_agent_architecture_pdf.py` | `scripts/generate_agent_architecture_pdf.py` | move |
| `docs/generate_agent_prompt_pdf.py` | `scripts/generate_agent_prompt_pdf.py` | move |
| `docs/generate_deployment_pdf.py` | `scripts/generate_deployment_pdf.py` | move |
| `docs/generate_tools_pdf.py` | `scripts/generate_tools_pdf.py` | move |

### Group 3: Root Test Files → `tests/`

| Before | After | Action |
|--------|-------|--------|
| `test_checkpoint_matrix.py` | `tests/test_checkpoint_matrix.py` | move |
| `test_animation_comparison.js` | `tests/e2e/test_animation_comparison.js` | move |

### Group 4: `path_planning_core/` debug scripts → `tests/path_planning/`

These are debug scripts that use pytest-compatible names but live outside `tests/`. They test `path_planning_core` module behavior.

| Before | After | Action |
|--------|-------|--------|
| `path_planning_core/test_backtrack_bug.py` | `tests/path_planning/test_backtrack_bug.py` | move |
| `path_planning_core/test_boundary_nav.py` | `tests/path_planning/test_boundary_nav.py` | move |
| `path_planning_core/test_t19_a5.py` | `tests/path_planning/test_t19_a5.py` | move |

### Group 5: Server Legacy Files → Archive Subdirectories

All files confirmed to have zero active imports from `server/main.py` and the production code path.

| Before | After | Action |
|--------|-------|--------|
| `server/main_v2.py` | `server/archive/main_v2.py` | move |
| `server/ui.py` | `server/archive/ui.py` | move |
| `server/continuationChatGPT-1` | `docs/session-notes/continuationChatGPT-1.txt` | move |
| `server/agents/isr_agent.py` | `server/agents/archive/isr_agent.py` | move |
| `server/agents/isr_agent_multi_v2.py` | `server/agents/archive/isr_agent_multi_v2.py` | move |
| `server/agents/isr_agent_multi_v3.py` | `server/agents/archive/isr_agent_multi_v3.py` | move |
| `server/agents/reasoning_agent.py` | `server/agents/archive/reasoning_agent.py` | move |
| `server/solver/post_optimizer_v1.py` | `server/solver/archive/post_optimizer_v1.py` | move |
| `server/solver/target_allocator_v1.py` | `server/solver/archive/target_allocator_v1.py` | move |

### Group 6: Legacy Test for Archived Agents

| Before | After | Action |
|--------|-------|--------|
| `tests/test_multi_agent.py` | `tests/archive/test_multi_agent_legacy.py` | move + add comment header |

### Group 7: Runtime Artefacts → Untrack + Gitignore

| File | Action |
|------|--------|
| `ai_solution.json` | `git rm --cached` + add to `.gitignore` |
| `mission_solution.json` | `git rm --cached` + add to `.gitignore` |
| `environment.json` | `git rm --cached` + add to `.gitignore` |
| `_test_output.txt` | add pattern `_test_output*.txt` to `.gitignore` |

### Group 8: New Files Created

| File | Purpose |
|------|---------|
| `pyproject.toml` | Project metadata + pytest config (`testpaths`, `pythonpath`) |
| `docs/architecture/README.md` | Index of architecture docs |
| `docs/agents/README.md` | Index of agent docs |
| `docs/solver/README.md` | Index of solver docs |
| `docs/session-notes/README.md` | Index of session notes |
| `server/archive/README.md` | Note: legacy server files, not production code |
| `server/agents/archive/README.md` | Note: legacy agent versions (v1–v3) |
| `server/solver/archive/README.md` | Note: legacy solver versions |
| `tests/path_planning/` | New test subfolder for path_planning_core tests |
| `tests/archive/` | Legacy tests for archived agent versions |

---

## Validation Rules

| Rule | Test |
|------|------|
| No `.md` files at root except README.md and CLAUDE.md | `find . -maxdepth 1 -name "*.md" \| grep -v README\|CLAUDE` → empty |
| No `test_*.py` outside `tests/` | `find . -name "test_*.py" -not -path "./tests/*" -not -path "./.specify/*" -not -path "./specs/*"` → empty |
| Root item count ≤ 22 visible non-hidden items (relaxed from ≤ 15; see spec.md SC-005) | `ls \| wc -l` → ≤ 22 |
| `pytest` runs cleanly with no path errors | `pytest --collect-only 2>&1 \| grep ERROR` → empty |
| Runtime artefacts not tracked | `git ls-files ai_solution.json mission_solution.json environment.json` → empty |
| `server/main_v2.py` does not exist at old path | `test ! -f server/main_v2.py` |
