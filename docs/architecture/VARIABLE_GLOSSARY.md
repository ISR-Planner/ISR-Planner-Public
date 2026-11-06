# Variable Glossary — one concept → one name

The unification map for the ISR Planner (north-star refactor). For each CONCEPT:
the canonical name, what it means, the redundant names that were collapsed into
it, and where it lives. Updated 2026-06-09 (fuel + distance + visited unified).

> Rule: one name per concept, computed once, read everywhere. DELETE the stored
> intermediate that forced a duplicate — don't just rename. Some redundancy was a
> load-bearing workaround; the unification removes the workaround.

---

## Fuel (§11.10 — DONE, commit 65d83ea/6d70609)

| Concept | Canonical name | Meaning | Collapsed-from / retired |
|---|---|---|---|
| The tank | `fuel_budget` | Full tank, immutable per drone. Full at the start of EVERY leg. The stats-box denominator. The ONLY stored fuel field on a config. | `fuel_capacity`, `full_tank`, `tank`, `leg2_budget` |
| Reduced leg-1 fuel after a cut | `start_fuel` | TRANSIENT — rides the solve REQUEST only (a dynamic replan has < a full tank). Never stored on the config, never overwrites the tank. Absent on a fresh solve. | the old destructive `fuel_budget = remaining` overwrite |
| Fuel used in the current leg | `state.legFuel[did]` | Distance flown since the leg start. Cut-NEUTRAL (accumulates across cuts); resets to 0 only at a refuel crossing. Durable (survives the startAnimation wipe). | `legBurnedDistance` is a live mirror of this on `animation.drones` |
| Finalized past legs | `state.legFuelHistory[did]` | Array of completed legs' fuel (frozen at each refuel). Drives the `⛽leg1+leg2` box. | — |

- **`fuel_capacity` is RETIRED.** Legacy read-only fallback only: on env LOAD and
  the UI fuel-input DISPLAY, an old env's `fuel_capacity` is read AS the tank.
- Solver: leg-1 budget = `start_fuel ?? fuel_budget`; every post-refuel leg = full
  `fuel_budget`. ([solver_bridge.py](../../server/solver/solver_bridge.py))
- Fuel stats accessor: [`computeFuelStats`](../../webapp/latlng_utils.js#L117).

## Distance / position frame (§10 / §unify — DONE, commit d5abb38/7e17c29)

| Concept | Canonical name | Meaning | Collapsed-from |
|---|---|---|---|
| Per-trajectory cumulative distance | `computeAnimationCumDist(traj, route, matrix, opts)` | THE single builder. SELF-SELECTS the frame from the inputs: SAM-aware matrix profile when route↔trajectory↔matrix line up (profile ≥ 0.9× raw haversine), else plain haversine. | 4 separate ad-hoc haversine loops; the per-caller `forceHaversine` flag (removed) |
| How far the drone has flown | `droneState.distanceTraveled` | SEGMENT-RELATIVE (resets to 0 each segment). Indexes the segment-only `cumulativeDistances`. | `animDist`, `currentDist` (locals now derive from it) |
| Global mission distance | `state.animation.missionDistance` | CUMULATIVE across segments. Drives segment-boundary / next-cut logic. `segmentStartOffset` bridges the two frames. | — |
| Per-leg distances (for stats) | [`getLegDistances(routeInfo)`](../../webapp/latlng_utils.js#L96) | Returns an ARRAY. Reads `leg_distances[]` if present, else falls back to `[leg1_distance, leg2_distance]`. | — (forward-compat accessor already in place) |

## Visited targets (§unify Phase 2 — DONE, commit 61edae3)

| Concept | Canonical name | Meaning |
|---|---|---|
| The visited set | `state.visitedTargets` | Target IDs visited across all segments. The ONLY read for the green X. |
| Owner map | `state.visitedTargetByDrone` | `{ targetId: droneId }` for per-drone stats. Kept in sync INLINE by the writers. |
| The FOUR writers | `clearVisited()`, `markTargetVisitedByDrone(t,d)`, `seedVisitedFromSegment(ids)`, `markAllVisited(targets)` | The ONLY writers. No raw `visitedTargets =/.push` or scattered owner rebuilds anywhere else. |
| The rule | — | A target is visited iff the drone passed its vertex on the canonical distance frame (`isTargetVisited`). |

---

## Deferred (do with the feature that makes them real)

- **`leg1_distance`/`leg2_distance` → `leg_distances[]`** — the array accessor
  already exists; the leg1/leg2 STORAGE + the solver's hard 2-tuple
  `recalculate_leg_distances` only become an array when the solver emits N legs.
  Do it AS PART OF the N-leg multi-stop refuel feature (see
  [MULTI_STOP_REFUEL_PLAN.md](../MULTI_STOP_REFUEL_PLAN.md)), NOT as a standalone
  rename (~25 hot-path sites, zero behavioral gain now, would be partly redone).

---

## Status

| Concept cluster | State |
|---|---|
| Fuel (tank / remaining / consumed) | ✅ unified |
| Distance / position frame | ✅ unified |
| Visited targets | ✅ unified |
| Leg distances (2 → N) | ⏸️ deferred to N-leg refuel |
