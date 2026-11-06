# Research: Project Cleanup & Repository Restructure

**Phase 0 output** — all NEEDS CLARIFICATION items resolved before Phase 1 design.

---

## Decision 1: Python Project Layout Strategy

**Decision**: Keep current "src-less" layout (`server/`, `path_planning_core/`, `orienteering_with_matrix.py` all at root). Do NOT introduce a `src/` wrapper.

**Rationale**: The project uses `PYTHONPATH=$(pwd)` with absolute imports (`from server.solver import ...`). Wrapping in `src/` would require updating ~50 import statements across the codebase with no functional benefit. The spec explicitly rules out module restructuring.

**Alternatives considered**:
- `src/` layout (PEP 517 recommended): rejected — import rewrites out of scope, high risk, low benefit.
- Moving `path_planning_core/` into `server/`: rejected — it is a separate module used independently (has its own `__init__.py` and tests).

---

## Decision 2: pytest Configuration

**Decision**: Add `pyproject.toml` at project root with `[tool.pytest.ini_options]` containing:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

**Rationale**: `pythonpath = ["."]` (pytest ≥ 7.0) tells pytest to add the project root to `sys.path` automatically, eliminating the `PYTHONPATH=$(pwd)` requirement noted in the constitution and CLAUDE.md. `testpaths = ["tests"]` prevents accidental discovery of root-level test files or `path_planning_core/test_*.py` debug scripts.

**Alternatives considered**:
- `pytest.ini`: simpler but doesn't support `pythonpath`; would need `conftest.py` sys.path hack.
- `setup.cfg`: valid but pyproject.toml is the modern standard (PEP 517/518/621).
- Keeping manual `PYTHONPATH=$(pwd)`: rejected — SC-004 goal is to simplify this; pyproject.toml solves it cleanly.

---

## Decision 3: Git Untracking Workflow for Artefact Files

**Decision**: Use `git rm --cached <file>` to stop tracking each artefact file, then add patterns to `.gitignore`. Files are not deleted from working directory (developers may have local copies they need).

**Rationale**: `git rm --cached` removes the file from git's index without touching the local filesystem. This is the standard, safe workflow for untracking committed files. The patterns added to `.gitignore` prevent re-tracking.

**Files to untrack** (confirmed runtime artefacts per clarification session):
- `ai_solution.json`
- `mission_solution.json`
- `environment.json`
- `_test_output.txt` (not currently tracked, but add gitignore pattern)

**`.gitignore` patterns to add**:
```
# Runtime artefacts (solver outputs, test data)
ai_solution.json
mission_solution.json
environment.json
_test_output.txt
*.solution.json
```

---

## Decision 4: Import Graph — Archive Candidate Verification

Full `grep` analysis of active imports in `server/main.py` and `server/agents/`:

| File | Imported by `main.py`? | Imported elsewhere actively? | Decision |
|------|------------------------|------------------------------|----------|
| `server/agents/isr_agent_multi_v4.py` | ✅ YES (`run_multi_agent_v4`) | coordinator_v4.py | **KEEP — active** |
| `server/agents/coordinator_v4.py` | ✅ YES (via v4) | — | **KEEP — active** |
| `server/agents/mission_executive.py` | ✅ YES | — | **KEEP — active** |
| `server/agents/agent_memory.py` | ✅ YES | — | **KEEP — active** |
| `server/agents/llm_factory.py` | ✅ YES | — | **KEEP — active** |
| `server/agents/mission_tools.py` | — | v4 agent | **KEEP — active** |
| `server/agents/mission_orchestration_tools.py` | — | v4 agent | **KEEP — active** |
| `server/agents/mission_store.py` | — | v4 agent | **KEEP — active** |
| `server/agents/graph.py` | — | v4 agent | **KEEP — active** |
| `server/agents/isr_agent.py` | ❌ NO | `tests/test_multi_agent.py` only | **ARCHIVE → `server/agents/archive/`** |
| `server/agents/isr_agent_multi_v3.py` | ❌ NO | `isr_agent.py` (itself archived) only | **ARCHIVE → `server/agents/archive/`** |
| `server/agents/isr_agent_multi_v2.py` | ❌ NO | `isr_agent_multi_v3.py` (archived) only | **ARCHIVE → `server/agents/archive/`** |
| `server/agents/reasoning_agent.py` | ❌ NO | Not found in any active import | **ARCHIVE → `server/agents/archive/`** |
| `server/solver/post_optimizer_v1.py` | ❌ NO | Not found in any import | **ARCHIVE → `server/solver/archive/`** |
| `server/solver/target_allocator_v1.py` | ❌ NO | Not found in any import | **ARCHIVE → `server/solver/archive/`** |
| `server/ui.py` | ❌ NO | Pure tkinter; not in web server | **ARCHIVE → `server/archive/`** |
| `server/main_v2.py` | ❌ NO | Comment only; confirmed in clarification | **ARCHIVE → `server/archive/`** |
| `server/solver/delivery_solver.py` | ❌ NO direct | `server/solver/__init__.py` | **LEAVE IN PLACE** — removing breaks solver package init |
| `server/solver/nfz_distance_calculator.py` | ❌ NO direct | `solver/__init__.py`, `delivery_solver.py` | **LEAVE IN PLACE** |
| `server/solver/nfz_trajectory_planner.py` | ❌ NO direct | `solver/__init__.py`, `delivery_solver.py` | **LEAVE IN PLACE** |
| `server/solver/sam_navigation.py` | ❌ NO direct | Unknown; nfz cluster | **LEAVE IN PLACE** — conservative |

**Impact on `tests/test_multi_agent.py`**: This test imports `isr_agent` (the v3 shim being archived). The test must be updated to either: (a) import directly from the archive path, or (b) be moved to `tests/archive/` alongside the archived agents. Recommended: move to `tests/archive/test_multi_agent_legacy.py` with a comment explaining it tests the archived v1–v3 agent chain.

---

## Decision 5: docs/ Subdirectory Organisation

**Decision**: Group docs into four subdirectories instead of a flat `docs/` dumping ground.

| Subdirectory | Contents |
|---|---|
| `docs/architecture/` | SYSTEM_ARCHITECTURE.md, MODULAR_ARCHITECTURE.md, SEGMENT_ANIMATION_SYSTEM.md, SEGMENT_SYSTEM_REDESIGN.md, DRONE_CONFIG_FLOW.md, DRONE_CONFIG_QUICK_REF.md, Single_Agent_Architecture.md, Agent_Architecture_Summary.md |
| `docs/agents/` | AGENTIC_SYSTEM_DESCRIPTION.md, AGENTIC_EXTENSION_PROPOSAL.md, MISSION_ORCHESTRATION_TOOLS.md, V3_TO_V4_MIGRATION.md, V3_VS_V4_COMPARISON.md, V4_IMPLEMENTATION_COMPLETE.md |
| `docs/solver/` | ISR_Optimization_Tools_Enhanced.md, SWAP_CLOSER_CASCADE.md, CHECKPOINT_DISTANCE_MATRIX_CHANGES.md, EXPECTED_BEHAVIORS.md |
| `docs/session-notes/` | SESSION_SUMMARY_JAN18.md, SESSION_SUMMARY_JAN18_PART2.md, LOITER_AND_REFUELING_HANDOFF.md, HANDOFF_NOTES.md, CHECKPOINT_REPLAN_TODO.md, CODE_AUDIT.md, README_ANALYSIS.md, ANALYSIS_SUMMARY.txt, first_two_bugs.md, SEGMENTED_IMPORT_REWRITE_NOTES.md, SEGMENTED_MISSION_FIXES.md, DEPLOYMENT.md |

**Note**: `supabase_schema.sql` → `docs/architecture/supabase_schema.sql` (it's reference schema documentation, not executable setup).
**Note**: `server/continuationChatGPT-1` → `docs/session-notes/continuationChatGPT-1.txt`.
**Note**: `docs/generate_*.py` PDF scripts → `scripts/` (they are scripts, not documentation).

**Alternatives considered**:
- Flat `docs/` (status quo): rejected — 30+ files with no grouping is not discoverable.
- Single level with filename prefixes only: rejected — subdirectories are standard Python project practice (e.g., aiohttp, FastAPI documentation trees).

---

## Decision 6: pyproject.toml Scope

**Decision**: Add a minimal `pyproject.toml` containing:
1. `[project]` metadata (name, version, description, python requires)
2. `[tool.pytest.ini_options]` (testpaths, pythonpath, addopts)
3. `[tool.flake8]` — migrate current `.flake8` config into pyproject.toml (optional, depends on flake8 version support; keep `.flake8` as fallback)

**Not included** in scope: build system (`[build-system]`), entry points, package discovery — these require `__init__.py` at root level and pip-install intent, which is out of scope per spec assumptions.

**Rationale**: A `pyproject.toml` with just `[project]` + `[tool.pytest.ini_options]` is the minimum to signal "this is a proper Python project" to any developer who clones it. It also documents the Python version requirement.
