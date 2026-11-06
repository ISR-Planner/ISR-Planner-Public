# Feature Specification: Project Cleanup & Repository Restructure

**Feature Branch**: `003-project-cleanup-restructure`  
**Created**: 2026-04-10  
**Status**: Clarified  
**Input**: User description: "I want you to clean up this project and turn it into a clean python project. clean up the repository so that its easy to navigate and that it makes sense for a programmer or user navigating the codebase. also update the readme. and place documents in the right folders."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - New Developer Onboarding (Priority: P1)

A developer clones the repository for the first time and needs to understand what the project is, how it is structured, and how to run it — all without prior knowledge or asking anyone.

**Why this priority**: First-impressions drive whether contributors engage with the project. If the root directory is cluttered with unrelated files, a new developer will not know where to look. This story delivers the single most impactful improvement.

**Independent Test**: Can be fully tested by cloning into a fresh directory, reading only the README and the top-level file listing, and answering: "Do I know what this project does, where the source code is, where the tests are, and how to run the server?" without opening any other file.

**Acceptance Scenarios**:

1. **Given** a fresh clone of the repository, **When** a developer reads the README, **Then** they understand the project purpose, the directory layout, the setup steps, and how to run the planner — within 5 minutes.
2. **Given** the root directory listing, **When** a developer scans the top-level files and folders, **Then** every item present is immediately recognisable as belonging to one of: source code, configuration, tooling, documentation, or standard project metadata — no mystery files.
3. **Given** the README's "Repository Structure" section, **When** a developer looks for the backend source, frontend assets, tests, or documentation, **Then** the README accurately maps each concern to a folder path that exists in the repository.

---

### User Story 2 - Documentation Discovery (Priority: P2)

An existing team member or contributor wants to read project documentation (architecture, deployment, agent descriptions, session notes) and can find all of it in one predictable location.

**Why this priority**: Currently, documentation is split between the root directory (e.g., `DEPLOYMENT.md`, `CODE_AUDIT.md`, `HANDOFF_NOTES.md`) and the `docs/` folder — causing double-searching. Consolidation has high value with low risk.

**Independent Test**: Can be fully tested by verifying that every `.md` file that is not `README.md`, `CLAUDE.md`, or a spec file lives under `docs/`, and that `README.md` contains a "Documentation" section linking to them.

**Acceptance Scenarios**:

1. **Given** a team member searching for deployment or architecture documentation, **When** they navigate to `docs/`, **Then** they find all documentation there with no relevant `.md` files remaining loose in the project root.
2. **Given** the `docs/` directory, **When** a developer lists its contents, **Then** files are grouped or named such that related topics (agent architecture, solver design, deployment, session notes) are distinguishable at a glance.
3. **Given** the README, **When** a developer looks for a pointer to documentation, **Then** they find a section that lists or links the key doc files and their purpose.

---

### User Story 3 - Test File Consolidation (Priority: P3)

A developer running the test suite expects all tests to live under the `tests/` directory, with no stray test files in the project root or other locations.

**Why this priority**: Stray test files (`test_checkpoint_matrix.py`, `test_refuel.py`, `test_animation_comparison.js`) in the root cause confusion about what is a test vs. a script vs. a one-off analysis. Pytest also discovers them unexpectedly when run from the root.

**Independent Test**: Can be fully tested by running `pytest` from the root and verifying that all discovered test files resolve to paths under `tests/`, and that no `test_*.py` files exist outside `tests/`.

**Acceptance Scenarios**:

1. **Given** `pytest` run from the project root, **When** test discovery completes, **Then** all discovered test files are under `tests/` with no files from the root directory unexpectedly included.
2. **Given** the project root directory listing, **When** a developer scans it, **Then** there are no `test_*.py` or `test_*.js` files at the root level.
3. **Given** the `tests/` directory, **When** a developer navigates it, **Then** tests are organised in a way that mirrors the main source layout (e.g., solver tests in `tests/solver/`, agent tests in `tests/agents/`).

---

### User Story 4 - Artefact & Data File Cleanup (Priority: P4)

Loose JSON output files and generated artefacts in the root directory are either removed, moved to a designated location, or explicitly excluded from version control — so the root contains only intentional project files.

**Why this priority**: Files like `ai_solution.json`, `mission_solution.json`, `environment.json`, and `_test_output.txt` appear to be runtime artefacts or sample data. Their presence at the root creates ambiguity about what is committed source vs. generated output.

**Independent Test**: Clone the repository and verify no runtime-generated or sample-data JSON files exist at the root; any intentional sample data files live under `examples/` or `data/`.

**Acceptance Scenarios**:

1. **Given** the project root, **When** a developer lists JSON files, **Then** no runtime artefact or solver-output JSON files are present at the root; any kept for reference live under a clearly named subdirectory.
2. **Given** `.gitignore`, **When** a developer reviews it, **Then** patterns covering common runtime output files (`*.json` outputs, `_test_output*`, `__pycache__`, `.pytest_cache`) are present.
3. **Given** any sample or example data needed for development, **When** a developer looks for it, **Then** it lives in a designated `examples/` or `data/` folder documented in the README.

---

### Edge Cases

- The `orienteering_with_matrix.py` solver MUST remain at the project root; it is loaded by path and cannot be moved without updating import logic.
- `CLAUDE.md` is a special AI-guidance file consumed by coding agents; it MUST remain at the root.
- The v1 single agent (`isr_agent_single_v1.py`) is already in `server/agents/versions/` and requires no action. The v2/v3 multi-agent files (`isr_agent_multi_v2.py`, `isr_agent_multi_v3.py`, `isr_agent.py` shim, `reasoning_agent.py`) reside directly in `server/agents/` — they are NOT already in `versions/` — and will be moved to `server/agents/archive/` (see data-model.md Group 5).
- The `server/continuationChatGPT-1` file (appears to be a text note) should be evaluated — if it is not source code it should be moved to `docs/` or deleted.
- `main_v2.py` alongside `main.py` in `server/` is a legacy file; it should be archived or removed, not left as an ambiguous duplicate entry point.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: All documentation `.md` files not serving a special root-level purpose (README, CLAUDE.md, contributing guidelines) MUST reside under `docs/`.
- **FR-002**: All Python test files MUST reside under `tests/`; no `test_*.py` files MUST remain in the project root.
- **FR-003**: The `README.md` MUST be rewritten to include: project overview, feature list, prerequisites, setup steps, directory structure map, how to run the server, how to run tests, and a pointer to `docs/` for further reading.
- **FR-004**: Runtime-generated artefact files (solver output JSONs, test output text files) MUST NOT be tracked in version control; `.gitignore` MUST cover them. The committed files `ai_solution.json`, `mission_solution.json`, and `environment.json` are runtime artefacts — they MUST be removed from version control tracking and added to `.gitignore`; no `examples/` directory is required.
- **FR-005**: ~~Any intentional sample or example data files MUST be placed in a clearly named directory (`examples/` or `data/`) and noted in the README.~~ *Superseded by clarification: all current root-level JSON files are artefacts (see FR-004); no `examples/` directory is needed for this feature.*
- **FR-006**: `server/main_v2.py` MUST be moved to `server/archive/main_v2.py` after confirming it is not actively imported or invoked anywhere in the project. Confirmation: no active import or launch reference exists (only a comment inside the file itself and a `.flake8` suppression). The v1 single agent already resides in `server/agents/versions/` and requires no further action. The v2/v3 multi-agent files (`isr_agent.py`, `isr_agent_multi_v2.py`, `isr_agent_multi_v3.py`, `reasoning_agent.py`) are directly in `server/agents/` and will be archived to `server/agents/archive/` as part of data-model.md Group 5.
- **FR-007**: `pytest` MUST be runnable from the project root with `PYTHONPATH=$(pwd)` and produce no collection errors caused by structural issues.
- **FR-008**: The top-level directory listing MUST contain only items that are immediately recognisable as belonging to: source code (`server/`, `webapp/`, `path_planning_core/`), configuration (`.env`, `requirements.txt`, `Makefile`, Docker files), tooling (`scripts/`, `tools/`), documentation (`docs/`, `README.md`, `CLAUDE.md`), tests (`tests/`), project metadata (`.git/`, `.github/`, `.specify/`, `.gitignore`), or the critical root-level solver (`orienteering_with_matrix.py`).

### Key Entities

- **Root Directory**: The set of files and folders visible when the repository is first opened; defines first impressions and navigability.
- **Documentation Set**: All `.md` and informational files describing the project, architecture, deployment, or development notes.
- **Test Suite**: All automated tests discoverable by `pytest`; must be self-contained under `tests/`.
- **Runtime Artefacts**: Files produced by running the solver, server, or tests (JSON outputs, cache directories, text logs); should not be committed.
- **Sample/Example Data**: Intentional reference JSON files used for development or demonstration; distinct from runtime artefacts.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A developer unfamiliar with the project can identify the purpose, structure, setup steps, and test command within 5 minutes of reading the README alone.
- **SC-002**: Zero `.md` documentation files remain in the project root other than `README.md` and `CLAUDE.md`.
- **SC-003**: Zero `test_*.py` or stray test files exist outside the `tests/` directory.
- **SC-004**: `pytest` run from the project root with `PYTHONPATH=$(pwd)` collects and passes the full existing test suite with no collection errors from structural issues.
- **SC-005**: The project root contains no more than 15 top-level items (files + directories), bringing it in line with standard Python open-source project conventions. *Clarification (2026-04-10 audit): after accounting for all legitimate config files (`mypy.ini`, `pyproject.toml`, `Dockerfile.railway`, etc.) the practical post-cleanup count is ~20 non-hidden items — a 47% reduction from 37. The spirit of SC-005 (all items recognisable, no mystery files) is fully satisfied; the exact ≤ 15 number is relaxed to ≤ 22.*
- **SC-006**: Runtime artefact patterns are covered in `.gitignore`; `git status` on a clean checkout shows no untracked artefact files after running the server once. Specifically: `ai_solution.json`, `mission_solution.json`, and `environment.json` are no longer tracked.
- **SC-007**: `server/archive/main_v2.py` exists and `server/main_v2.py` does not; no other file imports or references `server/main_v2.py` as an active entry point.

## Clarifications

### Session 2026-04-10

- Q: Are `ai_solution.json`, `mission_solution.json`, and `environment.json` intentional sample data or runtime artefacts? → A: Runtime artefacts — delete from tracking and add to `.gitignore`; no `examples/` directory needed.
- Q: Should `server/main_v2.py` be deleted or moved to an archive subdirectory? → A: Move to `server/archive/main_v2.py` after verifying no active imports. Verified: no active import or launch reference found; safe to archive.

## Assumptions

- `orienteering_with_matrix.py` MUST stay at the project root due to a hard dependency in `webapp/editor/solver/orienteering_interface.py`; structural cleanup does not move this file.
- `CLAUDE.md` is intentionally at the root for AI agent consumption and will remain there.
- The `docs/` folder already exists and will be used as the canonical documentation destination; no new top-level documentation directory is created.
- The v1 single agent in `server/agents/versions/` is intentionally preserved for reference and will not be touched. The v2/v3 multi-agent files will be moved to `server/agents/archive/` (not deleted), keeping them accessible for historical reference.
- This feature covers repository structure and documentation only — it does NOT change application logic, algorithms, API contracts, or frontend behaviour.
- The cleanup is intended to make the project feel like a well-maintained Python open-source project; Python packaging (pyproject.toml, setup.py) is out of scope unless trivially achievable.
- The server import structure (`PYTHONPATH=$(pwd)`) remains unchanged; no module restructuring is part of this feature.
