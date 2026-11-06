# Quickstart: Verifying the Cleanup

**Purpose**: Step-by-step verification that a developer can run after implementation to confirm all success criteria pass.

---

## Prerequisites

```bash
cd isr-planner
git checkout 003-project-cleanup-restructure
```

---

## Step 1: Verify Root Directory Item Count (SC-005)

```bash
ls | wc -l
```

Expected: ≤ 22 visible items (SC-005 relaxed; see spec.md SC-005).

Current expected items (~20): `CLAUDE.md`, `README.md`, `orienteering_with_matrix.py`, `pyproject.toml`, `requirements.txt`, `run_planner.sh`, `Makefile`, `mypy.ini`, `Dockerfile`, `Dockerfile.railway`, `docker-compose.yml`, `railway.toml`, `.flake8`, `docs/`, `server/`, `webapp/`, `path_planning_core/`, `tests/`, `scripts/`, `tools/`, `specs/`

---

## Step 2: Verify No Stray Documentation at Root (SC-002)

```bash
find . -maxdepth 1 -name "*.md" | sort
```

Expected output — only these two files:
```
./CLAUDE.md
./README.md
```

---

## Step 3: Verify No Stray Test Files Outside `tests/` (SC-003)

```bash
find . -name "test_*.py" \
  -not -path "./tests/*" \
  -not -path "./.specify/*" \
  -not -path "./specs/*" \
  -not -path "./.venv/*"
```

Expected: no output (empty).

Also check for the JS test file:
```bash
find . -name "test_*.js" -not -path "./tests/*" -not -path "./webapp/*"
```

Expected: no output.

---

## Step 4: Verify Artefacts Are No Longer Tracked (SC-006)

```bash
git ls-files ai_solution.json mission_solution.json environment.json
```

Expected: no output (files not tracked).

Check `.gitignore` covers them:
```bash
grep -E "ai_solution|mission_solution|environment\.json|_test_output" .gitignore
```

Expected: matches found.

---

## Step 5: Verify pytest Runs Cleanly (SC-004)

```bash
pytest --collect-only 2>&1 | tail -5
```

Expected: collection summary with zero errors, all files under `tests/`.

Run the suite:
```bash
pytest -x -q
```

Expected: passes (or pre-existing test failures only — no NEW failures introduced by the restructure).

---

## Step 6: Verify Import Sanity Gate (Constitution Principle I)

```bash
PYTHONPATH=$(pwd) python -c "from server.main import app; print('OK')"
```

Expected: `OK`

---

## Step 7: Verify Archive Files Exist and Old Paths Are Gone

```bash
# Archived server files
test -f server/archive/main_v2.py && echo "OK: main_v2 archived"
test ! -f server/main_v2.py && echo "OK: old path gone"
test -f server/archive/ui.py && echo "OK: ui.py archived"

# Archived agent files
test -f server/agents/archive/isr_agent.py && echo "OK: isr_agent archived"
test -f server/agents/archive/isr_agent_multi_v2.py && echo "OK: v2 archived"
test -f server/agents/archive/isr_agent_multi_v3.py && echo "OK: v3 archived"

# Archived solver files
test -f server/solver/archive/post_optimizer_v1.py && echo "OK: post_opt_v1 archived"
test -f server/solver/archive/target_allocator_v1.py && echo "OK: target_alloc_v1 archived"
```

Expected: all `OK` messages printed.

---

## Step 8: Spot-check docs/ Structure

```bash
ls docs/
```

Expected subdirectories: `agents/`, `architecture/`, `session-notes/`, `solver/`

```bash
ls docs/architecture/ | head -5
ls docs/agents/ | head -5
```

No `.md` files should remain at the `docs/` root level (only subdirectories and README.md for the folder):
```bash
find docs -maxdepth 1 -name "*.md" | sort
```

Expected: empty or just `docs/README.md` (if created).

---

## Step 9: Read the New README (SC-001)

Open `README.md` and verify it contains:
- [ ] Project overview / what ISR Mission Planner does
- [ ] Feature list
- [ ] Prerequisites (Python 3.11+, `ANTHROPIC_API_KEY`)
- [ ] Setup steps (`pip install -r requirements.txt`)
- [ ] How to run the server (`./run_planner.sh`)
- [ ] How to run tests (`pytest`)
- [ ] Directory structure map
- [ ] Pointer to `docs/` for further reading

---

## Complete Checklist

| Criterion | Verification Command | Expected |
|-----------|---------------------|----------|
| SC-001 README complete | Read README | All 8 sections present |
| SC-002 No stray .md at root | `find . -maxdepth 1 -name "*.md"` | Only CLAUDE.md + README.md |
| SC-003 No stray test files | `find . -name "test_*.py" -not -path "./tests/*"` | Empty |
| SC-004 pytest runs | `pytest -x -q` | Zero new failures |
| SC-005 Root ≤ 22 items | `ls \| wc -l` | ≤ 22 |
| SC-006 Artefacts untracked | `git ls-files ai_solution.json ...` | Empty |
| SC-007 archive layout | `test -f server/archive/main_v2.py` | Exit 0 |
| Import sanity | `python -c "from server.main import app"` | OK |
