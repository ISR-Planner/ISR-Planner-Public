# isr-planner Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-04-10

## Active Technologies

- Python 3.13 · FastAPI · LangGraph v4 · pytest · pyproject.toml
- (003-project-cleanup-restructure)

## Project Structure

```text
isr-planner/
├── CLAUDE.md                    # AI agent guidance (stays at root)
├── README.md                    # Project guide
├── orienteering_with_matrix.py  # Root-level Held-Karp solver (hard path dependency)
├── pyproject.toml               # Project metadata + pytest config
├── requirements.txt             # Python dependencies
├── run_planner.sh               # Server startup script
│
├── server/                      # FastAPI backend + LangGraph agents + solver
│   ├── main.py                  # Active entry point (all endpoints)
│   ├── agents/
│   │   ├── isr_agent_multi_v4.py   # ACTIVE agent
│   │   ├── archive/             # Legacy v1–v3 agents (NOT active)
│   │   └── versions/            # v1 single agent history
│   └── solver/
│       ├── solver_bridge.py     # Active solver orchestrator
│       └── archive/             # Legacy v1 solver files
│
├── webapp/                      # Vanilla JS frontend (canvas-based mission UI)
├── path_planning_core/          # SAM-aware path planning module
├── tests/                       # All automated tests (pytest)
│   ├── path_planning/           # Path planning tests
│   ├── archive/                 # Legacy tests for archived agents
│   └── e2e/                     # End-to-end JS tests
├── docs/                        # All project documentation
│   ├── architecture/            # System/segment/drone/DB architecture
│   ├── agents/                  # Agent versions, migration, tools
│   ├── solver/                  # Solver and optimization docs
│   └── session-notes/           # Dev notes, handoffs, deployment guide
├── scripts/                     # Utility scripts (PDF generation, analysis)
├── tools/                       # Developer diagnostic tools
└── specs/                       # Feature specifications (.specify workflow)
```

## Commands

```bash
# Run server
./run_planner.sh

# Run tests (pyproject.toml sets pythonpath=["."] — no PYTHONPATH needed)
pytest

# Import sanity check
PYTHONPATH=$(pwd) python -c "from server.main import app; print('OK')"
```

## Code Style

- Python: Follow PEP 8; absolute imports from project root (`from server.solver import ...`); no relative imports
- JS: Vanilla JS; all frontend logic in `webapp/isr.js`
- Tests: pytest; no `ANTHROPIC_API_KEY` required unless `@pytest.mark.live`

## Recent Changes

- 003-project-cleanup-restructure: Repository restructured — docs/ organised into 4 subdirs, all test files consolidated under tests/, legacy server/agent/solver files moved to archive/ subdirs, pyproject.toml added (no more manual PYTHONPATH for pytest), README rewritten

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
