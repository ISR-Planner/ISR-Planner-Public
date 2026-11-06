# Potential Orphan Code Audit - 2026-05-10

This note captures a static review of code that may be unused by the current ISR planner runtime. These are candidates for later review, not confirmed deletion targets.

## Scan Scope

- Active runtime entry point: `server/main.py`
- Served frontend entry point: `webapp/index.html`
- Active runtime areas reviewed: `server/`, `path_planning_core/`, `webapp/`, `scripts/`, `tools/`, `tests/`
- Static checks used: import/reference search, served script checks, Python import smoke checks, and textual function occurrence counts.

Tools not available in the local environment during this scan: `vulture`, `pyflakes`, and `ruff`.

## Likely Frontend Orphans

The current `webapp/index.html` only loads:

- `https://unpkg.com/leaflet@1.9.4/dist/leaflet.js`
- `/static/latlng_utils.js`
- `/static/isr.js?v=20260509-delete-restoreinit`

These frontend modules are present but do not appear to be loaded by the current app:

- `webapp/segment_manager.js`
- `webapp/segmented_mission.js`
- `webapp/segment_v2/`

Evidence:

- `webapp/index.html` does not include script tags for those files.
- Current segmented workflow appears implemented inside monolithic `webapp/isr.js`.

Review action:

- Confirm no alternate page or deployment template loads these files.
- If unused, move them to an archive directory or remove after a smoke test of import/export, segmented mission load, cut/replan, reset, and animation.

## Possible Unused Functions in `webapp/isr.js`

These functions had only one textual occurrence in `webapp/isr.js`, which usually means defined but not called:

- `calculateVisitedTargetsFromCutPosition` - around line 4469
- `setEditMode` - around line 5566
- `getUnfrozenRoutes` - around line 7976
- `pushMissionSegment` - around line 8710
- `getPreviousSegmentDroneConfigs` - around line 8758
- `getJoinedTrajectories` - around line 9045
- `regenerateTrajectoriesFromSequences` - around line 9390
- `replanFromCheckpoint` - around line 11672
- `appendChatMessage` - around line 12595
- `removeChatMessage` - around line 12611

Review action:

- Check for dynamic/global invocation before deleting, especially functions intended for event handlers or console debugging.
- Prefer removing one logical group at a time with browser smoke tests.

## Duplicate Python Class Definition

`webapp/editor/solver/orienteering_interface.py` defines `OrienteeringSolverInterface` twice:

- First definition starts around line 18.
- Second definition starts around line 48 and overwrites the first.

Evidence:

- Python keeps only the second class bound to the name.
- The first class body has no reachable effect after the second definition.

Review action:

- Remove the first duplicate class definition if behavior is identical or obsolete.
- Verify planner startup still logs successful solver initialization.

## Broken Optional Delivery/NFZ Solver Cluster

The delivery/NFZ solver components appear to be optional and currently broken through package exports:

- `server/solver/delivery_solver.py`
- `server/solver/orienteering_solver.py`
- `server/solver/nfz_distance_calculator.py`
- `server/solver/nfz_trajectory_planner.py`

Evidence:

- `server/solver/orienteering_solver.py` imports `isr_editor.solver`, which does not resolve in this repo.
- Importing `server.solver` leaves `DeliverySolver`, `DeliveryOrienteeringSolver`, `NFZDistanceCalculator`, and `NFZTrajectoryPlanner` as `None`.
- Existing cleanup research previously recommended keeping this cluster conservatively because it is exported from `server/solver/__init__.py`.

Review action:

- Decide whether delivery/NFZ support is still a product goal.
- If not, remove package exports and archive/delete the cluster.
- If yes, fix imports to use the current `webapp.editor.solver.orienteering_interface` path and add a focused test.

## Intentional Archive/Version Areas

These directories appear intentionally retained and should not be treated as accidental orphans without a separate decision:

- `server/archive/`
- `server/agents/archive/`
- `server/agents/versions/`
- `server/solver/archive/`
- `tests/archive/`

Review action:

- Keep as historical reference, or move to a clearer top-level archival area if repository size/noise becomes a concern.

## Generated Or Stale Artifacts

These look like generated output or local runtime artifacts:

- `scripts/comparison_result_*.json`
- `benchmarks/*/runs.jsonl`
- `benchmarks/*/manifest.json`
- `benchmarks/*/summary.md`
- `benchmarks/overnight-check.log`
- `nohup.out`
- `nohup.out.bak`

Review action:

- Confirm whether benchmark outputs are intended fixtures or historical records.
- If not, move generated artifacts out of the repo or add ignore rules.

## Suggested Cleanup Order

1. Remove the duplicate first class in `webapp/editor/solver/orienteering_interface.py`.
2. Archive or remove unloaded frontend module files after confirming no alternate HTML entry point uses them.
3. Review single-occurrence functions in `webapp/isr.js` in small groups.
4. Decide whether the delivery/NFZ solver cluster should be fixed or archived.
5. Clean generated artifacts and update `.gitignore` if needed.

## Verification Checklist For Any Cleanup

- `node --check webapp/isr.js`
- Start server: `python3 -m uvicorn server.main:app --reload --port 8893`
- Browser smoke test:
  - environment load
  - local geolocation/map initialization
  - add/edit airports, targets, SAMs
  - run planner
  - accept solution
  - cut/replan segmented flow
  - reset and animate
  - import/export environment and mission JSON

