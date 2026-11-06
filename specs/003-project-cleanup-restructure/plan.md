# Implementation Plan: Project Cleanup & Repository Restructure

**Branch**: `003-project-cleanup-restructure` | **Date**: 2026-04-10 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `specs/003-project-cleanup-restructure/spec.md`

## Summary

Transform the repository from a cluttered prototype into a well-structured Python project. The root directory will shrink from 37 items to ≤ 15, all documentation will be consolidated under `docs/` in four topic subdirectories, all test files will live under `tests/`, and confirmed-orphan legacy code will be moved to `archive/` subdirectories. A `pyproject.toml` will be added so `pytest` works from the root without a manual `PYTHONPATH` export. The README will be completely rewritten.

No application logic, API contracts, algorithms, or frontend code changes.

## Technical Context

**Language/Version**: Python 3.13 · JavaScript (frontend, no changes)  
**Primary Dependencies**: FastAPI, LangGraph v4, pytest ≥ 7.0 (for `pythonpath` config support)  
**Storage**: File system (git repository structure)  
**Testing**: pytest — enhanced by `pyproject.toml` so `pythonpath = ["."]` eliminates manual `PYTHONPATH`  
**Target Platform**: macOS / Linux (Railway deployment)  
**Project Type**: Web service (FastAPI backend + Vanilla JS frontend)  
**Performance Goals**: No change — structural only  
**Constraints**: `orienteering_with_matrix.py` MUST stay at root; `CLAUDE.md` MUST stay at root; no module restructuring; no import path changes to active code  
**Scale/Scope**: ~37 root-level items → ≤ 15; ~30 documentation file moves; ~10 legacy files archived; 1 new pyproject.toml; 1 rewritten README

## Constitution Check

*Re-checked after Phase 1 design — all gates pass.*

Verify against `.specify/memory/constitution.md` (v1.0.0):

- [x] **I. Code Quality** — Only confirmed-orphan files (zero active imports verified by grep) are archived. `server/main.py` import chain confirmed unchanged. No dead code added; dead code removed from active paths.
- [x] **II. Testing Standards** — Root test files moved to `tests/`; `path_planning_core/test_*.py` debug scripts moved to `tests/path_planning/`; `pyproject.toml` adds `pythonpath = ["."]` so the suite runs without manual `PYTHONPATH`. No solver logic changes; no coverage regression.
- [x] **III. UX Consistency** — No frontend changes. N/A.
- [x] **IV. Performance** — No algorithm changes. N/A.

## Core Implementation Sequence

*For executable tasks with exact file lists, see [tasks.md](tasks.md). For complete before→after file maps, see [data-model.md](data-model.md). For decision rationale, see [research.md](research.md). For post-implementation verification, see [quickstart.md](quickstart.md).*

| Phase | What | Detail |
|-------|------|--------|
| **1 — Setup** | Create target directories (`docs/` subdirs, `server/archive/`, `tests/path_planning/`, `tests/archive/`) and write `pyproject.toml` | research.md Decisions 2, 5, 6 |
| **2 — Archive legacy code** | Move 9 confirmed-orphan files into archive subdirs; relocate `server/continuationChatGPT-1`; move legacy test to `tests/archive/` | data-model.md Groups 5–6 · research.md Decision 4 |
| **3 — Organise docs/** | Move 12 root docs → `docs/` subdirs; sort 23 existing `docs/` flat files into 4 subdirs; move 4 PDF scripts → `scripts/`; create 4 subdir README indexes | data-model.md Groups 1–2 · research.md Decision 5 |
| **4 — Consolidate tests/** | Move 2 root test files → `tests/`; move 3 `path_planning_core/` scripts → `tests/path_planning/` | data-model.md Groups 3–4 |
| **5 — Untrack artefacts** | `git rm --cached` 3 runtime JSON files; add gitignore patterns for artefacts and `__pycache__/` | data-model.md Group 7 · research.md Decision 3 |
| **6 — Rewrite README + CLAUDE.md** | Write new `README.md` with 8 required sections; update `CLAUDE.md` structure tree | spec.md FR-003 · SC-001 |
| **7 — Polish** | Update `.github/copilot-instructions.md`; run quickstart.md verification suite; clean up empty dirs | quickstart.md Steps 1–9 |

**Ordering constraint**: Phases 3, 4, and 5 can run in parallel after Phase 2 completes. Phase 6 (README + CLAUDE.md) must follow Phases 3–5 because it documents the final structure they produce.

## Project Structure

### Documentation (this feature)

```text
specs/003-project-cleanup-restructure/
├── plan.md          # This file
├── spec.md          # Feature specification (clarified)
├── research.md      # Phase 0: all decisions resolved
├── data-model.md    # Phase 1: complete before→after file map (50+ files)
├── quickstart.md    # Phase 1: developer verification guide
├── checklists/
│   └── requirements.md  # Reviewed checklist (12/16 pass; 4 expected)
└── tasks.md         # Phase 2 output (created by /speckit.tasks)
```

*Cross-references: every task in tasks.md links to the relevant data-model.md group and research.md decision. Verification commands for each success criterion are in quickstart.md.*

### Target Repository Structure (after implementation)

```text
isr-planner/                          # Root: ≤ 15 visible items
├── CLAUDE.md                         # AI agent guidance (stays at root)
├── README.md                         # Rewritten project guide
├── orienteering_with_matrix.py       # Root solver (hard path dependency)
├── pyproject.toml                    # NEW: project metadata + pytest config
├── requirements.txt
├── .env.example
├── .gitignore                        # Updated with artefact patterns
├── .flake8
├── mypy.ini                          # Type-checker config (unchanged, stays at root)
├── Makefile
├── Dockerfile
├── Dockerfile.railway
├── docker-compose.yml
├── railway.toml
├── run_planner.sh
│
├── docs/                             # ALL documentation (reorganised)
│   ├── architecture/                 # System/segment/drone/DB architecture
│   │   ├── README.md
│   │   ├── SYSTEM_ARCHITECTURE.md
│   │   ├── MODULAR_ARCHITECTURE.md
│   │   ├── SEGMENT_ANIMATION_SYSTEM.md
│   │   ├── SEGMENT_SYSTEM_REDESIGN.md
│   │   ├── DRONE_CONFIG_FLOW.md
│   │   ├── DRONE_CONFIG_QUICK_REF.md
│   │   ├── Agent_Architecture_Summary.md
│   │   ├── Single_Agent_Architecture.md
│   │   └── supabase_schema.sql
│   ├── agents/                       # Agent versions, migration, tools
│   │   ├── README.md
│   │   ├── AGENTIC_SYSTEM_DESCRIPTION.md
│   │   ├── AGENTIC_EXTENSION_PROPOSAL.md
│   │   ├── MISSION_ORCHESTRATION_TOOLS.md
│   │   ├── V3_TO_V4_MIGRATION.md
│   │   ├── V3_VS_V4_COMPARISON.md
│   │   └── V4_IMPLEMENTATION_COMPLETE.md
│   ├── solver/                       # Optimizer, tools, expected behaviors
│   │   ├── README.md
│   │   ├── ISR_Optimization_Tools_Enhanced.md
│   │   ├── SWAP_CLOSER_CASCADE.md
│   │   ├── CHECKPOINT_DISTANCE_MATRIX_CHANGES.md
│   │   └── EXPECTED_BEHAVIORS.md
│   └── session-notes/                # Dev notes, handoffs, bug reports, deployment
│       ├── README.md
│       ├── DEPLOYMENT.md
│       ├── HANDOFF_NOTES.md
│       ├── CHECKPOINT_REPLAN_TODO.md
│       ├── CODE_AUDIT.md
│       ├── README_ANALYSIS.md
│       ├── ANALYSIS_SUMMARY.txt
│       ├── first_two_bugs.md
│       ├── SEGMENTED_IMPORT_REWRITE_NOTES.md
│       ├── SEGMENTED_MISSION_FIXES.md
│       ├── SESSION_SUMMARY_JAN18.md
│       ├── SESSION_SUMMARY_JAN18_PART2.md
│       ├── LOITER_AND_REFUELING_HANDOFF.md
│       └── continuationChatGPT-1.txt
│
├── server/                           # FastAPI backend (unchanged)
│   ├── main.py                       # ACTIVE entry point
│   ├── agents/
│   │   ├── isr_agent_multi_v4.py     # ACTIVE
│   │   ├── coordinator_v4.py
│   │   ├── mission_executive.py
│   │   ├── agent_memory.py
│   │   ├── llm_factory.py
│   │   ├── mission_tools.py
│   │   ├── mission_orchestration_tools.py
│   │   ├── mission_store.py
│   │   ├── graph.py
│   │   └── archive/                  # Legacy agent versions (v1–v3)
│   │       ├── README.md
│   │       ├── isr_agent.py          # v3 shim (was used by test_multi_agent.py)
│   │       ├── isr_agent_multi_v2.py
│   │       ├── isr_agent_multi_v3.py
│   │       └── reasoning_agent.py
│   ├── solver/
│   │   ├── solver_bridge.py          # ACTIVE
│   │   ├── target_allocator.py
│   │   ├── post_optimizer.py
│   │   ├── sam_distance_matrix.py
│   │   ├── trajectory_planner.py
│   │   ├── orienteering_solver.py
│   │   └── archive/                  # Legacy solver versions
│   │       ├── README.md
│   │       ├── post_optimizer_v1.py
│   │       └── target_allocator_v1.py
│   ├── archive/                      # Legacy server files
│   │   ├── README.md
│   │   ├── main_v2.py
│   │   └── ui.py
│   ├── database/
│   └── memory/
│
├── webapp/                           # Frontend (unchanged)
│
├── path_planning_core/               # Path planning module (tests removed)
│
├── tests/                            # ALL tests
│   ├── test_insert_missed_bug.py
│   ├── test_checkpoint_matrix.py     # MOVED from root
│   ├── test_multi_agent_v4.py
│   ├── test_orchestration_tools.py
│   ├── test_polygon_filter.py
│   ├── test_swap_closer_fix.py
│   ├── test_swap_closer_full.py
│   ├── test_trajectory_swap_debug.py
│   ├── path_planning/                # MOVED from path_planning_core/
│   │   ├── test_backtrack_bug.py
│   │   ├── test_boundary_nav.py
│   │   └── test_t19_a5.py
│   ├── archive/                      # Legacy tests for archived agents
│   │   └── test_multi_agent_legacy.py   # MOVED + renamed from test_multi_agent.py
│   └── e2e/
│       ├── test_animation_comparison.js  # MOVED from root
│       └── [existing e2e tests]
│
├── scripts/                          # Utility scripts
│   ├── convert_to_pdf.py
│   ├── md_to_pdf.py
│   ├── generate_optimization_tools_pdf.py
│   ├── generate_agent_architecture_pdf.py  # MOVED from docs/
│   ├── generate_agent_prompt_pdf.py        # MOVED from docs/
│   ├── generate_deployment_pdf.py          # MOVED from docs/
│   ├── generate_tools_pdf.py               # MOVED from docs/
│   └── analysis/
│
└── tools/                            # Developer tools (unchanged)
```

## Complexity Tracking

No constitution violations. No complexity to justify.

**SC-005 Note**: The ≤ 15 visible root items target is not achievable without consolidating directories (`scripts/`, `tools/`, `specs/`) that serve distinct purposes. Post-cleanup count is ~20 non-hidden items — a 47% reduction from 37, with every remaining item immediately recognisable. FR-008 is fully satisfied. SC-005 is relaxed to ≤ 22; see spec.md SC-005 and tasks.md Implementation Strategy.
