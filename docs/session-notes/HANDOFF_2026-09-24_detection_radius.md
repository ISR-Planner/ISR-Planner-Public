# Handoff — 2026-09-24 overnight: detection radius (items 1, 2, 3)

Asked by Kamal before leaving: "keep working until you are done with 1, 2 and 3".
**All three are done and NOT committed** (last commit: `a265be4`). Server tests
pass; the browser changes have not been run in a browser.

## What was wrong (measured on a 3-target mission, 2 km radius)

| | No radius | 2 km radius, before | 2 km radius, now |
|---|---|---|---|
| Path drawn by Run Planner | 34.9 km | 27.0 km (passes each target at 1.99 km) | 27.1 km |
| Fuel reported | 35.0 | **35.0** (centre-to-centre) | **27.1** |
| Route edits (sequence bar, agent `set_route`) | 35.0, through centres | **35.0, through centres** | **27.1, within radius** |
| 30 km tank | 2 targets | 2 targets | **3 targets** (saved fuel used) |

## 1. Fuel from the flown path, and use of the fuel it saves

`server/solver/detection_radius.py` (new) — `apply_detection_radius(...)`, run
once on a finished solution, only for drones with a radius:
- **(a)** every such route is re-evaluated with the shared route evaluator:
  trajectory, distance, per-hop (`span_distances`), per-leg (`leg_distances`) and
  therefore feasibility and the animation frame all come from the path flown.
- **(b)** unassigned targets are inserted where they add the least flown distance
  (highest priority first), only if every leg still fits its tank — checked on
  the real path. The existing order is kept; it can only add targets.
- Wired into `run_solve_with_allocation` (Run Planner, Edit→Done, agent
  `solve_full_mission`) right after checkpoint-candidate selection, before time
  sync. The agent's `commit_ordered_plan` gets (a) only (it plans one drone and
  can't see the others' targets).
- The animation frame prefers the flown per-hop distances
  (`_attach_anim_frame`), so the fuel counter matches.
- Drones with ordering constraints get (a) only (an insert could break the
  rule). Drones with loiter enabled are skipped entirely (loiter fuel isn't
  modelled in the flown-path evaluation).
- Stress test (20 targets, 3 drones, 2 SAMs, 45 km tanks): 18 → **20** targets,
  108 → **124** points, no drone over its tank, 5.4 s for the whole solve.
- Design doc updated: `docs/DETECTION_RADIUS_TRAJECTORY_OPTIMIZATION.md`,
  "Future Enhancements" 3 (partly) and 4 (done).

## 2. Route edits use the radius

- `evaluate_route(..., drone_config=...)` applies the planner's own taut-string
  step (`_relax_positions_to_detection_radii`); with a radius, distances come
  from the flown path instead of the centre-to-centre matrix. It returns
  `span_distances`, and `route_animation_frame()` builds the same playback frame
  the solve response has.
- Callers pass the drone's settings: `/api/apply_sequence` (new
  `drone_config` field; response now carries `span_distances` + `anim_frame`),
  the browser's `evaluateDroneRoute`, and the agent's `set_route` /
  `insert_in_route` / `remove_from_route`.

## 3. One "visited" rule with the radius

`targetHitToleranceKm(target, did)` (`webapp/isr.js`) — radius × 1.05, else the
default vertex tolerance — used by all four visited checks: animation loop, cut,
segment switch, resume. Before, only the animation loop used the radius.

## Tests

- `tests/test_detection_radius.py` (8): fuel and animation = flown path; no
  radius unchanged; saved fuel adds a target a centre-distance plan couldn't
  afford; never over the tank; constrained drone keeps its targets and order;
  forbidden target type never added; route-edit endpoint returns a matching
  animation frame; agent `commit_ordered_plan` reports flown fuel.
- `tests/test_route_evaluation.py` (+2): edited route within radius and shorter;
  distance ignores the centre-to-centre matrix once waypoints moved.
- Full suite: 361 passed, 3 skipped (`tests/test_gnn_pipeline.py` excluded,
  needs torch). Node: `tests/e2e/test_mission_sequence.js` 12,
  `tests/e2e/test_landed_drone.js` 5.
- Live server check: 30 km tank, 2 km radius → all 3 targets, 27.1 km,
  animation total 27.1.

## Browser test steps (hard-refresh: `isr.js?v=20260924-detection-radius`)

1. Asset tab → Detection radius: give D1 e.g. 2 km for type a. Run Planner.
   The path should pass beside targets, and the fuel in the stats should be the
   shorter distance (compare with radius 0).
2. Lower D1's tank until, with radius 0, it drops a target; set the radius back
   — the dropped target should come back in (server log: `🎯 D1: detection
   radius saved fuel — added T…`).
3. Animate: targets get their green ✕ when the drone passes within the radius;
   cut (C) and replan — radius targets already passed are not offered again.
4. Edit a route in the sequence bar with a radius set: the edited path also
   passes beside targets, with the shorter fuel.
5. Agent "Solve" and an agent route edit: same behaviour.

## Known limits (not done)

- Allocation and ordering are still chosen on centre-to-centre distances; only
  inserting extra targets uses the saving. Re-ordering with a detection-aware
  matrix is still open.
- The stitched full-mission replay (Replay segments unchecked) builds its
  playback frame in the browser from the matrix, so its fuel counter can show
  centre-to-centre fuel for radius routes.
- Loiter-enabled drones are skipped by the new step.
- Insertion doesn't check agent "priority filter" rules (v4-style priority
  constraints); Run Planner doesn't use those.
