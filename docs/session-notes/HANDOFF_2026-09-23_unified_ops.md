# Handoff — 2026-09-23 overnight: unified route edits (operator + agent)

Built unattended from `docs/architecture/UNIFIED_MISSION_OPERATIONS.md`, with the
user's permission. **Nothing is committed.** Server-side code is covered by tests;
**the browser changes have not been run in a browser** — start with the test
steps below.

## Restore point

Everything as it was when the user left (all of 2026-09-23's browser-confirmed
work, uncommitted): `docs/session-notes/restore_2026-09-23_before_unified_ops/`
(see its README for the three restore commands).

## What was built

### 1. Whole-mission sequence (G0) — `webapp/isr.js`
- The sequence box shows each drone's whole mission, e.g.
  `A1,T2,T5,C1,T9,T1,C2,A3`: flown targets, diamonds (map labels `C1`, `C2`),
  then the remaining route. `buildMissionSequence(did)`.
- The flown part is computed from what was actually flown: each committed
  segment's route is split at the next segment's cut position on its trajectory
  (`splitRouteAtCut`). Unflown targets of a cut segment are not in the history.
- Also shown: a C-key cut waiting for a plan (diamond + old route's unreached
  part), a checkpoint draft on screen, a pause (previewed as the cut it would
  become), a killed drone (ends at its diamond).
- The box refreshes on every mode change (`refreshSequenceBox`, from
  `setMissionMode`). Display is fail-safe: any error falls back to the old
  current-route text and logs to the debug tab.

### 2. Route edits through the shared path (G3) — `webapp/isr.js`
- One browser function, `applyRouteEdits(edits)`, for the sequence bar AND the
  agent: cut first if paused/live-editing (same as Edit→Done), build the
  request with `buildSolveRequest()`, evaluate edited drones, keep every other
  drone's current plan (`currentPlanRoutes`), apply with `applySolveResult()`
  → a normal draft with frozen history, diamonds and Accept.
- Sequence bar Apply: the flown part is read-only (changing it is refused with a
  debug-tab message); only what follows the latest diamond is edited.
- Same permission gate as Run Planner / the agent (`canSolve` or
  `canAgentContinue`): blocked while animating, and after Accept before
  animating (Run Planner is blocked there too, because a solve would add a
  segment with no cut).

### 3. One route evaluator — `server/solver/route_evaluation.py` (new)
- `evaluate_route()` is the single implementation behind `/api/apply_sequence`
  (now a thin wrapper) and the agent's route-edit tools.
- Fixed on the way: it now knows `D#_START` nodes (were "Unknown waypoint"),
  applies the cruise-altitude SAM reduction (edited routes used the full ground
  radius), and uses the SAM-avoiding trajectory length for any leg the cached
  matrix doesn't have (was a straight line through the SAM).

### 4. Agent tools — `server/agents/reasoning_agent.py`
- **New:** `set_route(drone, waypoints)` (remaining route after the current
  position; the diamond is added automatically), `insert_in_route`,
  `remove_from_route`, `set_drone(drone, enabled, end_airport)`.
- **Abort** = `set_route(d, ["A1"])` for every drone. The environment is
  untouched.
- **Removed (broken or dead, ~930 lines):** `solve_route`, `commit_route`,
  `_solve_and_store` (ignored the cut and the requested end airport),
  `compare_routes`, `solve_with_leg_split` and `commit_mission_plan` (never
  given to the agent), and the unreachable two-phase block in
  `solve_full_mission`. `crewai_agent.py` now wraps `set_route`.
- The agent returns **only the drones it changed this turn**; the browser keeps
  every other drone's current plan (routes carried from earlier turns could be
  stale).
- **Committed plan (G4):** the reply ends with a "Committed plan (what the map
  shows)" section generated from the routes themselves, not from the model's
  text.
- **Settings (G5):** drone settings changed by `set_drone` come back in the
  reply (`drone_configs`) and become part of the request before the draft is
  built; killed-drone detection is re-run so a drone the agent disables gets its
  red diamond.
- The prompt now distinguishes route edits (the order dictates the route) from
  replans (the order asks the agent to choose).
- The browser sends `current_routes` (each drone's route as the operator sees
  it, from its current position), so the agent's insert/remove edit that route.

## NOT done — needs the user

- **`commit_ordered_plan` kept (decision needed).** It is still a separate
  planning path. Deleting it would make the agent worse: for "D1 visits T5
  first" on `tests/fixtures/isr_env_20260721-1253_N1_1.json`, Run Planner's
  planner with the same per-drone constraint gives a 399.8 km route vs the known
  312.9 km from `commit_ordered_plan`, and its route data is inconsistent (A1
  refuels in the route, `refuel_stops` lists TK1). Real unification = move
  `commit_ordered_plan`'s ordering/refuel logic into the shared planner so Run
  Planner gets it too. Deep solver change — deliberately not done unattended.
- **G1 `allowed_targets`** (for "only visit T1, T3, T5" replans): deferred to
  after the demo, as agreed.
- **Asset tab after an agent `set_drone`:** the new settings are in the draft;
  whether Accept shows them in the Asset tab is unverified.
- **Start-on-SAM-edge** ("no valid path" when a cut lands exactly on the
  altitude-reduced SAM edge): not touched.

## Tests

- `tests/test_route_evaluation.py` — 8: abort from a diamond, `D#_START`,
  no straight line through a SAM, altitude shrinks the detour, matrix legs,
  unknown waypoint, endpoint accepts `D#_START`.
- `tests/test_reasoning_agent_learning.py` — 27 total, incl. 9 new route-edit
  tests: abort from each diamond (env untouched), insert cheapest / after,
  remove, edits building on each other, disabled drone refused, only changed
  drones returned + committed-plan text, `set_drone` returned to the UI.
- `tests/e2e/test_mission_sequence.js` — 9 (`node tests/e2e/test_mission_sequence.js`):
  runs the REAL `buildMissionSequence` from `isr.js` — the two-cut example
  `A1,T2,T5,C1,T9,T1,C2,A3`, pending C cut, pause preview, checkpoint draft
  (abort), killed drone, cut exactly at a target, fail-safe display.
- Full suite: 343 passed, 3 skipped (`tests/test_gnn_pipeline.py` excluded —
  needs torch). The running server (port 8893, nohup) reloaded the new code;
  `/api/apply_sequence` answered a live `D1_START → A1` request correctly.

## Browser test steps (hard-refresh first: Cmd+Shift+R)

1. **Sequence display.** Solve, Accept, Animate, press C, Run Planner, Accept,
   Animate, press C again. Select D1 in the sequence box: it should read like
   `A1,T…,C1,T…,C2,…` and match the map.
2. **Abort by hand.** In CHECKPOINT (after C), type D1's sequence with
   everything after the last diamond replaced by `A1` → Apply → draft shows
   D1 diamond → A1, other drones unchanged → Accept → Animate.
3. **Edit the flown part** (e.g. delete an early target) → refused with a
   debug-tab message, box resets.
4. **Paused edit.** Pause mid-flight, edit D2's remaining route, Apply → a cut
   is made at the current positions and the draft uses it.
5. **Agent abort.** "Abort. All drones go directly to A1." → every drone
   diamond → A1; the reply's "Committed plan" section matches the map; unvisited
   targets stay unvisited (not green).
6. **Agent add/remove.** "Add T9 to D1's route" / "D2 skip T4" → only that
   drone changes; the rest keep their routes.
7. **Agent replan.** "Continue from here" and "T9 first" still work as before.
8. **Agent disable.** "D2 is down, continue" → D2 in its red diamond, D1
   replans.
9. **Regression:** Run Planner fresh, C cut + Run Planner, Edit→Done — as before.

If a step fails, the debug tab lines around `Applying route edit`,
`🩺 SOLVE BOUNDARY` and `Agent routes:` show what was sent and applied.

## Update 2026-09-24 (while the user was in class)

Reported: Edit pressed **while flying** (no pause) → Done did not replan; Run
Planner had to be pressed by hand. Pause → Edit → Done worked.

**Likely cause (found in code, not yet confirmed in the browser):** during a live
edit the animation keeps running. If the drones reached the end of their routes
while the operator was still editing, the animation loop switched the mode to
READY_TO_ANIMATE ("animation complete") underneath the editor. The next Done
click then *entered* a new, plain edit (live-edit flag cleared, staged targets
set aside), and the following Done only accepted it — CHECKPOINT "ready to
replan", no cut, no solve. Short test missions (20–45 km) make this likely.

Fixes (all `webapp/isr.js`, uncommitted):
- `finishedDuringLiveEdit()`: when the drones finish during a live edit, the
  animation stops but the mode stays EDITING_ENV; Done takes the live-edit path
  (cut → Run Planner → Accept) from where they stopped. Applied to both
  "animation complete" branches (start and resume).
- `acceptEdits()` now reports a failed live-edit replan in the debug tab
  (`❌ Live-edit replan failed: …`) — it was un-awaited, so errors only reached
  the browser console.
- Older bug found on the way: at a cut, a drone that had **already finished**
  kept its ORIGINAL start airport in `buildCheckpointEnv`'s checkpoint branch
  (only drones still flying were moved to their diamond), so it would be
  replanned from where the mission began. It now resumes where it stopped.
- Sequence box: a segment with no diamond (drones landed, or replanned in place)
  now uses where the next segment actually starts, so a fully flown route stays
  in the history; a drone that has landed shows no preview diamond.
  3 new tests (`tests/e2e/test_mission_sequence.js`, now 12).

Checked and ruled out: C-key cut and P-key pause are blocked while editing;
`checkpointSource` is never "replay_cut"; the Mission Control `mc-edit` button
does not exist in `index.html`, so its duplicate handlers never attach.

**To confirm in the browser:** Solve → Accept → Animate → press Edit while
flying → add a target → wait until the drones land → Done. Expect
`✈️ Drones reached the end of their routes during the live edit`, then
`📍 Creating checkpoint…`, `🔄 Replanning from checkpoint…`, and an accepted
replan — without pressing Run Planner. Also repeat with Done pressed BEFORE the
drones land.

**Question for Kamal:** a drone that already landed resumes with the fuel it has
left, not a full tank. Should landing at an airport refuel it?

### Landed drones (decided by Kamal, 2026-09-24)

"Once a drone lands it is the end of that mission, unless it is allowed to refuel
at that airport." Implemented in `resumeLandedDrone()` (`webapp/isr.js`), used by
both cut branches of `buildCheckpointEnv`:
- may refuel there (`refuel_options` / legacy `refuel_airport`, refuels left
  under `max_refuels`) → takes off again from that airport with a full tank;
- otherwise → parked: start = end = that airport, `start_fuel` 0, settings stay
  enabled; the planner gives it no targets (`tests/test_landed_drone_parked.py`).
- `server/solver/time_sync.py` read `start_fuel` with `or full_tank`, turning a
  real 0 into a full tank; now only `None` means full tank.
Tests: `tests/e2e/test_landed_drone.js` (5), `tests/test_landed_drone_parked.py` (1).
