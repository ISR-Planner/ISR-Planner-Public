# Tasks: Project Cleanup & Repository Restructure

**Input**: Design documents from `/specs/003-project-cleanup-restructure/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, quickstart.md

**Tests**: Not requested — no test tasks included.

**Organization**: Tasks are grouped by user story. Stories are ordered by dependency (US2→US3→US4→US1) rather than strict priority, because US1 (README rewrite) documents the final repository structure and must be written after all structural moves are complete.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4)
- File paths are relative to project root

---

## Phase 1: Setup

**Purpose**: Create all target directories and the new pyproject.toml before any file moves begin.

- [X] T001 Create docs/ subdirectories: `docs/architecture/`, `docs/agents/`, `docs/solver/`, `docs/session-notes/` (see research.md Decision 5)
- [X] T002 [P] Create archive directories: `server/archive/`, `server/agents/archive/`, `server/solver/archive/` (see data-model.md Group 5)
- [X] T003 [P] Create test subdirectories: `tests/path_planning/`, `tests/archive/` (see data-model.md Groups 4, 6)
- [X] T004 [P] Create `pyproject.toml` at project root with `[project]` metadata and `[tool.pytest.ini_options]` containing `testpaths = ["tests"]` and `pythonpath = ["."]` (see research.md Decisions 2 and 6)

**Checkpoint**: All target directories exist. `pyproject.toml` is in place. No files moved yet.

---

## Phase 2: Foundational (Archive Legacy Code)

**Purpose**: Move confirmed-orphan legacy files to archive directories. All files have zero active imports verified in research.md Decision 4. Must complete before user story phases to prevent conflicts with doc/test moves.

**⚠️ CRITICAL**: No user story work should begin until archiving is complete — Group 5 moves `server/continuationChatGPT-1` to `docs/session-notes/` which overlaps with US2 doc organisation.

- [X] T005 [P] Move server legacy files to `server/archive/`: `server/main_v2.py` → `server/archive/main_v2.py`, `server/ui.py` → `server/archive/ui.py` (see data-model.md Group 5, research.md Decision 4)
- [X] T006 [P] Move legacy agent files to `server/agents/archive/`: `server/agents/isr_agent.py`, `server/agents/isr_agent_multi_v2.py`, `server/agents/isr_agent_multi_v3.py`, `server/agents/reasoning_agent.py` (see data-model.md Group 5, research.md Decision 4)
- [X] T007 [P] Move legacy solver files to `server/solver/archive/`: `server/solver/post_optimizer_v1.py`, `server/solver/target_allocator_v1.py` (see data-model.md Group 5)
- [X] T008 [P] Move `server/continuationChatGPT-1` → `docs/session-notes/continuationChatGPT-1.txt` (see data-model.md Group 5)
- [X] T009 Move `tests/test_multi_agent.py` → `tests/archive/test_multi_agent_legacy.py` and prepend archive comment header explaining it tests the v1–v3 agent chain (see data-model.md Group 6, research.md Decision 4)
- [X] T010 [P] Create archive README.md files: `server/archive/README.md`, `server/agents/archive/README.md`, `server/solver/archive/README.md` — each noting these are legacy files not used in production (see data-model.md Group 8)

**Checkpoint**: All legacy files archived. Run import sanity check: `PYTHONPATH=$(pwd) python -c "from server.main import app; print('OK')"` — must print `OK`. Old paths (`server/main_v2.py`, `server/ui.py`, `server/agents/isr_agent.py`) must not exist. (see quickstart.md Steps 6–7)

---

## Phase 3: User Story 2 — Documentation Discovery (Priority: P2)

**Goal**: Consolidate all documentation under `docs/` in four topic subdirectories so developers find everything in one predictable location.

**Independent Test**: `find . -maxdepth 1 -name "*.md" | sort` returns only `./CLAUDE.md` and `./README.md`. All docs/ files are in subdirectories. (see quickstart.md Step 2, Step 8)

### Implementation for User Story 2

- [X] T011 [P] [US2] Move 10 root markdown session-notes to `docs/session-notes/`: `CHECKPOINT_REPLAN_TODO.md`, `CODE_AUDIT.md`, `DEPLOYMENT.md`, `HANDOFF_NOTES.md`, `README_ANALYSIS.md`, `SEGMENTED_IMPORT_REWRITE_NOTES.md`, `SEGMENTED_MISSION_FIXES.md`, `first_two_bugs.md`, `ANALYSIS_SUMMARY.txt` (see data-model.md Group 1)
- [X] T012 [P] [US2] Move 2 root architecture docs to `docs/architecture/`: `DRONE_CONFIG_FLOW.md`, `DRONE_CONFIG_QUICK_REF.md`, `supabase_schema.sql` (see data-model.md Group 1)
- [X] T013 [P] [US2] Move existing `docs/` agent files to `docs/agents/`: `AGENTIC_EXTENSION_PROPOSAL.md`, `AGENTIC_SYSTEM_DESCRIPTION.md`, `MISSION_ORCHESTRATION_TOOLS.md`, `V3_TO_V4_MIGRATION.md`, `V3_VS_V4_COMPARISON.md`, `V4_IMPLEMENTATION_COMPLETE.md` (see data-model.md Group 2)
- [X] T014 [P] [US2] Move existing `docs/` architecture files to `docs/architecture/`: `Agent_Architecture_Summary.md`, `MODULAR_ARCHITECTURE.md`, `SEGMENT_ANIMATION_SYSTEM.md`, `SEGMENT_SYSTEM_REDESIGN.md`, `Single_Agent_Architecture.md`, `SYSTEM_ARCHITECTURE.md` (see data-model.md Group 2)
- [X] T015 [P] [US2] Move existing `docs/` solver files to `docs/solver/`: `CHECKPOINT_DISTANCE_MATRIX_CHANGES.md`, `EXPECTED_BEHAVIORS.md`, `ISR_Optimization_Tools_Enhanced.md`, `SWAP_CLOSER_CASCADE.md` (see data-model.md Group 2)
- [X] T016 [P] [US2] Move existing `docs/` session-notes to `docs/session-notes/`: `LOITER_AND_REFUELING_HANDOFF.md`, `SESSION_SUMMARY_JAN18.md`, `SESSION_SUMMARY_JAN18_PART2.md` (see data-model.md Group 2)
- [X] T017 [P] [US2] Move `docs/generate_*.py` scripts to `scripts/`: `generate_agent_architecture_pdf.py`, `generate_agent_prompt_pdf.py`, `generate_deployment_pdf.py`, `generate_tools_pdf.py` (see data-model.md Group 2)
- [X] T018 [P] [US2] Create index README.md files: `docs/architecture/README.md`, `docs/agents/README.md`, `docs/solver/README.md`, `docs/session-notes/README.md` — each listing the files in that subdirectory with one-line descriptions (see data-model.md Group 8)

**Checkpoint**: `find . -maxdepth 1 -name "*.md" | sort` → only `./CLAUDE.md`, `./README.md`. `ls docs/` shows only subdirectories (plus optional README.md). `find docs -maxdepth 1 -name "*.md"` → empty or just `docs/README.md`. (see quickstart.md Step 2, Step 8)

---

## Phase 4: User Story 3 — Test File Consolidation (Priority: P3)

**Goal**: All test files live under `tests/` with no strays in root or other locations. `pytest` discovers only files under `tests/`.

**Independent Test**: `find . -name "test_*.py" -not -path "./tests/*" -not -path "./.specify/*" -not -path "./specs/*" -not -path "./.venv/*"` → empty. `pytest --collect-only` shows all tests under `tests/`. (see quickstart.md Steps 3, 5)

### Implementation for User Story 3

- [X] T019 [P] [US3] Move root test files to `tests/`: `test_checkpoint_matrix.py` → `tests/test_checkpoint_matrix.py` (see data-model.md Group 3)
- [X] T020 [P] [US3] Move root JS test to `tests/e2e/`: `test_animation_comparison.js` → `tests/e2e/test_animation_comparison.js` (see data-model.md Group 3)
- [X] T021 [US3] Move `path_planning_core/` debug test scripts to `tests/path_planning/`: `test_backtrack_bug.py`, `test_boundary_nav.py`, `test_t19_a5.py` — verify imports still resolve after move (see data-model.md Group 4)

**Checkpoint**: `find . -name "test_*.py" -not -path "./tests/*" -not -path "./.specify/*" -not -path "./specs/*"` → empty. `pytest --collect-only 2>&1 | grep ERROR` → empty. (see quickstart.md Steps 3, 5)

---

## Phase 5: User Story 4 — Artefact & Data File Cleanup (Priority: P4)

**Goal**: Runtime-generated JSON files are untracked from git and covered by `.gitignore`. No ambiguous output files at root.

**Independent Test**: `git ls-files ai_solution.json mission_solution.json environment.json` → empty. `grep -E "ai_solution|mission_solution|environment\.json" .gitignore` → matches. (see quickstart.md Step 4)

### Implementation for User Story 4

- [X] T022 [US4] Run `git rm --cached` on tracked artefact files: `ai_solution.json`, `mission_solution.json`, `environment.json` (see data-model.md Group 7, research.md Decision 3)
- [X] T023 [US4] Update `.gitignore` with artefact patterns: `ai_solution.json`, `mission_solution.json`, `environment.json`, `_test_output.txt`, `*.solution.json`, `__pycache__/`, `*.pyc`, `.pytest_cache/` (see research.md Decision 3)

**Checkpoint**: `git ls-files ai_solution.json mission_solution.json environment.json` → empty output. `.gitignore` contains the artefact patterns. (see quickstart.md Step 4)

---

## Phase 6: User Story 1 — New Developer Onboarding (Priority: P1)

**Goal**: A developer cloning the repo for the first time can understand the project, its structure, setup, and run commands by reading only the README within 5 minutes.

**Independent Test**: Read README.md and verify it contains all 8 required sections per SC-001. The directory structure in the README must match the actual repo layout. (see quickstart.md Step 9)

**⚠️ Dependency**: This phase MUST run after Phases 3–5 are complete, because the README documents the final repository structure produced by those phases.

### Implementation for User Story 1

- [X] T024 [US1] Rewrite `README.md` with required sections: project overview, feature list, prerequisites, setup steps, how to run the server, how to run tests, directory structure map, and pointer to `docs/` for further reading (see spec.md FR-003, SC-001)
- [X] T025 [US1] Update `CLAUDE.md` to reflect the new repository structure — update the project structure tree, update file paths in the Architecture section, update "Code Organization" section, update "Important Files" paths (see plan.md Target Repository Structure)

**Checkpoint**: README contains all 8 sections per SC-001. `CLAUDE.md` structure tree matches actual repo layout. (see quickstart.md Step 9)

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final verification, agent context updates, and cleanup of metadata files.

- [X] T026 [P] Update `.github/copilot-instructions.md` with final project structure tree and updated commands reflecting pyproject.toml (no more `PYTHONPATH` for pytest)
- [X] T027 Run full verification suite from `quickstart.md` Steps 1–9 to confirm all success criteria SC-001 through SC-007 pass (see quickstart.md, data-model.md Validation Rules)
- [X] T028 [P] Clean up empty directories left behind after file moves: check `docs/` root for orphaned `.md` files, verify `server/agents/` no longer contains the archived files (`isr_agent.py`, `isr_agent_multi_v2.py`, etc.) at the old paths. Note: `server/agents/versions/` is untouched by this feature — it contains `isr_agent_single_v1.py`, `agent_memory_v1.json`, and a `README.md`; do not clean it up.

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1: Setup ──────────────────┐
                                 ├──→ Phase 2: Foundational (archive legacy)
                                 │         │
                                 │         ├──→ Phase 3: US2 (doc moves)  ──┐
                                 │         ├──→ Phase 4: US3 (test moves) ──┤
                                 │         └──→ Phase 5: US4 (artefacts)  ──┤
                                 │                                          │
                                 │              ┌───────────────────────────┘
                                 │              ▼
                                 └────────→ Phase 6: US1 (README rewrite)
                                                    │
                                                    ▼
                                            Phase 7: Polish
```

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (directories must exist) — BLOCKS all user stories
- **US2, US3, US4 (Phases 3–5)**: All depend on Phase 2 completion. Can run **in parallel** with each other (they touch different file sets)
- **US1 (Phase 6)**: Depends on Phases 3, 4, and 5 being complete (README documents the final structure)
- **Polish (Phase 7)**: Depends on Phase 6

### User Story Dependencies

- **User Story 2 (P2, Docs)**: After Foundational — independent of US3/US4
- **User Story 3 (P3, Tests)**: After Foundational — independent of US2/US4
- **User Story 4 (P4, Artefacts)**: After Foundational — independent of US2/US3
- **User Story 1 (P1, README)**: After US2 + US3 + US4 (documents final structure)

### Within Each User Story

- All tasks marked [P] within a story can run in parallel
- Tasks without [P] must run sequentially in listed order
- Commit after each phase completes

### Parallel Opportunities

**After Phase 2 completes, launch all three in parallel:**

```
Phase 3 (US2): T011, T012, T013, T014, T015, T016, T017 — all [P]
Phase 4 (US3): T019, T020 — both [P]; then T021 sequentially
Phase 5 (US4): T022 then T023 — sequential
```

---

## Implementation Strategy

### MVP First (US2 + US3 + US4 Structural Moves)

1. Complete Phase 1: Setup (create directories + pyproject.toml)
2. Complete Phase 2: Foundational (archive legacy code)
3. Complete Phases 3, 4, 5 in parallel (doc moves, test moves, artefact cleanup)
4. **STOP and VALIDATE**: Run quickstart.md Steps 1–8 to verify all structural changes
5. Complete Phase 6: README rewrite
6. Complete Phase 7: Polish + full verification

### SC-005 Root Item Count: Decision

**Decision**: Accept ≤ 22 visible root items as the practical target. The ≤ 15 spec target is not achievable without merging directories (`scripts/`, `tools/`, `specs/`) that serve distinct purposes.

**Post-cleanup root will have ~20 non-hidden items:**
- 12 files: `CLAUDE.md`, `README.md`, `orienteering_with_matrix.py`, `pyproject.toml`, `requirements.txt`, `run_planner.sh`, `Makefile`, `Dockerfile`, `Dockerfile.railway`, `docker-compose.yml`, `railway.toml`, `mypy.ini`
- 8 directories: `docs/`, `server/`, `webapp/`, `path_planning_core/`, `tests/`, `scripts/`, `tools/`, `specs/`

This is a 47% reduction from 37 items. All remaining items are immediately recognisable — FR-008 is fully satisfied. SC-005 relaxed to ≤ 22 in spec.md.

`__pycache__/` and `.pytest_cache/` are covered by T023 (gitignore update) so they will not re-accumulate after a fresh run.

---

## Notes

- All file moves should use `git mv` to preserve git history
- [P] tasks = different files, no dependencies on each other
- [Story] label maps task to specific user story for traceability
- Cross-references to design docs (data-model.md, research.md, quickstart.md) are included in each task
- Commit after each phase completes
- Run quickstart.md verification at each checkpoint
