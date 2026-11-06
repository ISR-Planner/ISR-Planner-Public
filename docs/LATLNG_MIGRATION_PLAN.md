# Lat/Lng + Real-World Units Migration Plan

**Status:** Planning — not started
**Author:** Kamal + Claude
**Date:** 2026-04-15

## Goal

Replace the 0–100 grid coordinate system with native `{lat, lng}` storage, and convert all distance-based values to real-world units. One-shot hard cutover — no backward compatibility with old `x,y` environments.

## Unit system (confirmed)

| Quantity | Unit | Notes |
|---|---|---|
| Element positions | `{lat, lng}` degrees | WGS84 |
| Distances (fuel budget, SAM range, route distance, distance matrix) | **kilometers** | Single unit everywhere |
| Drone speed | **km/h** | User-facing, no double-conversion |
| Time | hours (derived: `distance_km / speed_kmh`) | |

### Defaults for new elements
- New SAM `range`: **5 km**
- New drone `speed`: **120 km/h**
- New drone `fuel_budget`: **50 km** (≈25 min at 120 km/h)
- SAM polygon chord length (`minSeg`): **0.1 km** (100 m)
- Hit-test radius: **8 px** (pixel-space, not world-space)

## Scope decisions

1. **Grid concept deleted entirely.** No `gridSize`, no `mapState.bounds` as a transformer. `bounds` only persists as an optional initial-view hint.
2. **Distance math:** equirectangular projection around a per-call reference lat/lng, converted to km. Accurate for mission-scale areas (<100 km). Not geodesic — overkill for this use case.
3. **SAM geometry:** project every point in a SAM cluster into a local meter frame at the cluster centroid, run existing polygon code unchanged, unproject back to lat/lng.
4. **No backward compat.** Old `{x, y}` fixtures get one-shot converted by a migration script using the SF bounds baked into `mapState.bounds` today. Old segment exports (`SegmentIO.js`) are not migrated — forward only, per explicit decision.
5. **Default view for empty env:** `localStorage['isrLastView']` → `navigator.geolocation` prompt → SF fallback.

## Migration phases

Each phase is independently runnable and testable. Do NOT interleave — finish and verify each before starting the next.

---

### Phase 1 — Shared helpers + one-shot fixture conversion

New file `path_planning_core/latlng_utils.py`:
- `latlng_to_local_xy(lat, lng, ref_lat, ref_lng) -> (x_km, y_km)` — equirectangular projection around `ref`, returns km.
- `local_xy_to_latlng(x_km, y_km, ref_lat, ref_lng) -> (lat, lng)` — inverse.
- `haversine_km(lat1, lng1, lat2, lng2) -> float` — for the distance matrix builder.

Mirror in JS as `webapp/latlng_utils.js`:
- Same three functions. Loaded before `isr.js` in `index.html`.

One-shot migration script `scripts/migrate_env_to_latlng.py`:
- Inputs: a list of env JSON paths. Default bounds: `{south:37.70, north:37.85, west:-122.51, east:-122.36}` (the current hardcoded SF bounds).
- For each airport/target: `lat = south + (y/100)*(north-south)`, `lng = west + (x/100)*(east-west)`. Delete `x`, `y`; add `lat`, `lng`.
- For each SAM: convert `pos: [x, y]` → `center: {lat, lng}`, then `range_km = range * 0.15 * 111 * cos(midlat) ≈ range * 0.1318` (approximate grid→km at SF latitude; good enough for one-shot seed data).
- Convert `fuel_budget` in any drone config: `fuel_budget_km = fuel_budget * 0.1318`.
- Convert `distance_matrix` values by same factor if present.
- Backup original alongside (`.bak`).
- Files to convert:
  - `webapp/editor/tmp_environment.json`
  - `webapp/editor/mission_control_drone1.json`
- Run it. Commit the new files.

Phase 1 is mergeable on its own — pure additive code + data conversion, no runtime behavior change yet.

**Status: DONE (commit de784e6 on branch `feat/latlng-migration`).**

---

### Phase 2 — Backend: schema + solver bridge

**Status: NOT STARTED. Revised approach below after reading the code.**

Original plan said "mostly field-name renames." Reading `server/solver/solver_bridge.py` (1600+ lines) showed that's
too optimistic — `x`/`y` access is scattered through `_parse_env_for_solver`, `_build_distance_matrix`,
`_solve_one_leg`, `_solve_two_leg_refuel`, `solve_mission`, `solve_mission_with_allocation`, trajectory generation,
and the cached `dist_data["waypoints"]` that carries `{id, x, y}` through the entire allocator + post-optimizer
pipeline. Surgical rename across all call sites is a multi-day job with high regression risk.

**Revised approach: boundary shim at `solve_mission` / `solve_mission_with_allocation`.**

Add a single helper `_project_env_to_local_km(env) -> (env_projected, ref_lat, ref_lng)`:
1. Deep-copy `env`.
2. Compute `ref_lat, ref_lng` from the centroid of all airport/target/SAM lat/lngs.
3. For each airport/target: replace `lat/lng` with `x/y` in local km via `latlng_to_local_xy`.
4. For each SAM: replace `center` with `pos: [x_km, y_km]` and rename `range_km` → `range`.
5. For each drone config: rename `fuel_budget_km` → `fuel_budget`, `speed_kmh` → `speed` (keeping numeric values
   unchanged since both are already in km / km-ish units after Phase 1).
6. Store `_ref` on the returned env dict for the unproject step.

Then at the top of `solve_mission` / `solve_mission_with_allocation`:
```python
env, ref_lat, ref_lng = _project_env_to_local_km(env)
# ... existing solve code runs unchanged, operating in km ...
# before return, unproject trajectories:
_unproject_solution_trajectories(result, ref_lat, ref_lng)
```

**Files that only need the shim, nothing else:**
- `_build_distance_matrix` — already uses `math.hypot`; values are now km because inputs are km.
- `_parse_env_for_solver` — already reads `x`/`y`; values are now km.
- `calculate_sam_aware_matrix` and all `path_planning_core` geometry — units-agnostic, works in km as-is.
- `target_allocator`, `post_optimize_solution`, `ISRTrajectoryPlanner` — units-agnostic.
- The cached `dist_data["waypoints"]` format stays the same (`{id, x, y}`) but values are km.

**What needs actual changes (not just the shim):**
- `_compute_env_hash` — reads `x`/`y`; update to read lat/lng from the *pre-projection* env, so the cache key
  survives the projection.
- `_unproject_solution_trajectories` — new helper that walks the solution dict, finds all `trajectory` lists
  (lists of `[x, y]` pairs), and rewrites them as `[lat, lng]` pairs via `local_xy_to_latlng`.
- `server/main.py:162` — default `fuel_budget: float = 50.0` (km).
- Any endpoint that returns `distance` values — these are now km. Verify the response schema docs (if any) reflect that.

**Testing strategy for Phase 2:**
Phase 2 alone cannot be tested against a live frontend (Phase 3 not done yet). Instead:
1. Write a small standalone Python script that loads `webapp/editor/tmp_environment.json` (already in new schema
   after Phase 1), calls `solve_mission(env, drone_configs)`, and asserts:
   - Return value has `routes` for each drone.
   - Each `distance` is in a plausible km range (5–50 km for SF-scale missions).
   - Each `trajectory` point is `[lat, lng]` with `lat ∈ [37.7, 37.85]`, `lng ∈ [-122.51, -122.36]`.
2. Run existing Python tests; any coord-specific ones will fail and get fixed in Phase 4.

**Do NOT commit Phase 2 without running that script and getting clean output.**

`server/solver/solver_bridge.py`:
- [solver_bridge.py:124-172](/Users/kamalali/isr-planner/server/solver/solver_bridge.py#L124-L172) — `_build_distance_matrix()`: replace `math.hypot(dx, dy)` with `haversine_km(lat1, lng1, lat2, lng2)`. Read `wp["lat"]`, `wp["lng"]`.
- [solver_bridge.py:174-242](/Users/kamalali/isr-planner/server/solver/solver_bridge.py#L174-L242) — `_parse_env_for_solver()`: read `lat`/`lng` instead of `x`/`y`. SAMs: read `center: {lat, lng}` and `range_km`. Synthetic starts keep the same shape with lat/lng.

`server/solver/sam_distance_matrix.py`:
- [sam_distance_matrix.py:86-160](/Users/kamalali/isr-planner/server/solver/sam_distance_matrix.py#L86-L160) — `calculate_sam_aware_matrix()`: read lat/lng; for SAM-avoidance geometry, project all waypoints + SAM centers into a local km frame using the env centroid as reference (one reference lat/lng for the whole matrix build is fine for mission-scale areas). Existing Euclidean geometry then works unchanged, outputs in km.

`path_planning_core/sam_navigator.py`, `sam_wrapping.py`, `boundary_navigation.py`:
- These are pure geometry and stay in local-km coordinates. The caller is responsible for projecting lat/lng → local km before calling, and unprojecting results back.
- Default `min_seg` changes from 2.0 (grid) to 0.1 (km).

`server/solver/trajectory_planner.py`:
- [trajectory_planner.py:32-66](/Users/kamalali/isr-planner/server/solver/trajectory_planner.py#L32-L66) — accepts SAMs with lat/lng, projects internally at init time. Output trajectory points are lat/lng after unprojecting.

`server/main.py`:
- [main.py:162](/Users/kamalali/isr-planner/server/main.py#L162) — default `fuel_budget: float = 50.0` (km, was 300 grid units).

Commit. Run tests (they'll break — that's Phase 4's job, but confirm only coord-related ones break).

---

### Phase 3 — Frontend: storage, transforms, rendering

**Status: NOT STARTED.** Do not begin until Phase 2 is committed and verified with the standalone test script. Phase 3 cannot be partially committed — the frontend will be non-functional until every call site is migrated.

`webapp/isr.js`:

**Delete/rewrite:**
- [isr.js:4282](/Users/kamalali/isr-planner/webapp/isr.js#L4282) — remove `gridSize = 100.0` constant.
- [isr.js:252-258](/Users/kamalali/isr-planner/webapp/isr.js#L252-L258), [isr.js:4311-4335](/Users/kamalali/isr-planner/webapp/isr.js#L4311-L4335) — `w2c(x, y)` becomes `w2c(lat, lng)` — direct Mercator projection from lat/lng to canvas pixels, no bounds intermediary. The `latLngToMercator` helper stays.
- [isr.js:5207-5244](/Users/kamalali/isr-planner/webapp/isr.js#L5207-L5244) — `canvasToWorld` becomes `canvasToLatLng`, returns `{lat, lng}`.
- [isr.js:4347-4354](/Users/kamalali/isr-planner/webapp/isr.js#L4347-L4354) — `worldToPixelRadius(range_km, centerLat, centerLng)`: project `(centerLat, centerLng)` and `(centerLat, centerLng + range_km/km_per_deg_lng)` through `w2c`, measure pixel distance. **This is the fix for the oval SAM bug.**
- Delete `gridToLatLng`, `latLngToGrid` (no longer needed — storage IS lat/lng).

**Mutate:**
- [isr.js:3751-3910](/Users/kamalali/isr-planner/webapp/isr.js#L3751-L3910) — `_sampleCircle`, `_samsOverlap`, `_clusterOverlappingSams`, `computeWrappedPolygonsClientSide`: take SAMs in lat/lng, project to local-km using cluster centroid, run existing geometry, unproject polygon vertices back to lat/lng. Polygons stored as lat/lng arrays.
- [isr.js:5298-5370](/Users/kamalali/isr-planner/webapp/isr.js#L5298-L5370) — click-to-add handlers: push `{id, lat, lng, ...}` for airports/targets; `{center: {lat, lng}, range_km: 5}` for SAMs.
- [isr.js:5399-5428](/Users/kamalali/isr-planner/webapp/isr.js#L5399-L5428) — drag handler: `canvasToLatLng` → update `element.lat/lng` or `sam.center`.
- [isr.js:5252](/Users/kamalali/isr-planner/webapp/isr.js#L5252) — hit-test `R = 4.0` grid units becomes pixel-space check (~8 px).
- [isr.js:5055-5175](/Users/kamalali/isr-planner/webapp/isr.js#L5055-L5175) — `exportEnvironment()`: write lat/lng fields; `map_view` stays as-is (already lat/lng).
- [isr.js:6560-6800](/Users/kamalali/isr-planner/webapp/isr.js#L6560-L6800) — import handlers: expect lat/lng fields. No fallback for old format.
- [isr.js:139-160](/Users/kamalali/isr-planner/webapp/isr.js#L139-L160) — `mapState.bounds` demoted: still populated from `map_view` on import for view restoration, but no longer drives coordinate transforms.

**All render sites** (airports [L4479-L4488](/Users/kamalali/isr-planner/webapp/isr.js#L4479-L4488), targets [L4541-L4560](/Users/kamalali/isr-planner/webapp/isr.js#L4541-L4560), SAMs [L4407-L4415](/Users/kamalali/isr-planner/webapp/isr.js#L4407-L4415), trajectories [L4642-L4651](/Users/kamalali/isr-planner/webapp/isr.js#L4642-L4651), drone [L4771-L4780](/Users/kamalali/isr-planner/webapp/isr.js#L4771-L4780), wrapped polygons [L4459-L4465](/Users/kamalali/isr-planner/webapp/isr.js#L4459-L4465)): swap `w2c(a.x, a.y)` → `w2c(a.lat, a.lng)`. Mechanical.

**Solve payload** [isr.js:8698-8746](/Users/kamalali/isr-planner/webapp/isr.js#L8698-L8746): ships the new env schema directly; nothing special needed since it's just `JSON.stringify(state.env)`.

**Default view on empty env** (new code near [L157-L163](/Users/kamalali/isr-planner/webapp/isr.js#L157-L163)):
```js
function getInitialView() {
  const saved = localStorage.getItem('isrLastView');
  if (saved) return JSON.parse(saved);
  // fallthrough: try geolocation, else SF
}
```
Persist on every pan/zoom settle.

`webapp/index.html`:
- Load `latlng_utils.js` before `isr.js`.

---

### Phase 4 — Tests + fixtures

- `tests/test_unified_optimizer.py` — rewrite `create_simple_env()` to use lat/lng around SF.
- `tests/test_trajectory_planner_poly.py` — polygon coords in lat/lng.
- `tests/test_multi_agent_v4.py` — update `fuelBudget: 150` → km value.
- `tests/path_planning/test_backtrack_bug.py` — SAM with lat/lng.
- Verify all distance assertions still hold given the unit change (most tests assert ordering/relative values — those are unit-independent).

---

### Phase 5 — Cleanup

- Delete any grid-related comments and dead code paths.
- Update `docs/SESSION_SUMMARY_APRIL_15_2026.html` grid-reference section (or mark it historical).
- Update `CLAUDE.md` / `README.md` if they mention the 0–100 grid.

---

## Risks

1. **Solver numerical stability.** Distance matrix values change from ~0–150 (grid) to ~0–50 (km). Similar magnitude, should be fine, but watch for any hardcoded epsilons or thresholds in the orienteering code.
2. **SAM polygon chord length tuning.** 0.1 km default may be too coarse for tiny SAMs or too fine for huge ones. Consider `max(0.05, range_km * 0.05)` adaptive.
3. **Equirectangular accuracy at high latitudes.** Fine for SF. If someone plans a mission near the poles it'll distort. Ignore for now; flag for later.
4. **Leaflet already uses lat/lng** — Phase 3 mostly aligns the canvas renderer with what Leaflet already does internally. That's why the scaffolding inventory showed the code is 90% there.

## Estimated effort

| Phase | Scope | Risk |
|---|---|---|
| 1 | Helpers + fixture migration | Low |
| 2 | Backend schema + solver | Medium |
| 3 | Frontend rewrite | Medium-High (most code changes) |
| 4 | Tests | Low |
| 5 | Cleanup | Low |

Phase 3 is the bulk of the work and the riskiest — lots of call sites, tight coupling between rendering and interaction.

## Order of operations

Strict sequence: 1 → 2 → 3 → 4 → 5. Commit after each phase. After Phase 2, backend won't run against the new fixtures until Phase 3 ships the new frontend — so test Phases 2+3 as a pair on a branch before merging.

---

## Current state (as of this commit)

- **Phase 1 is DONE** on branch `feat/latlng-migration`, commit `de784e6`.
- The branch has been pushed to `origin` so you can check it out from another machine.
- **Phase 2 was attempted and deliberately halted** after reading the code. The solver bridge is too coupled to
  `x`/`y` for a surgical rename in one autonomous pass — see the revised boundary-shim approach under Phase 2.
- **Phase 3 is untouched.** It depends on Phase 2 shipping first.

### What Phase 1 gives you right now

- New helper modules (`path_planning_core/latlng_utils.py`, `webapp/latlng_utils.js`) ready for import.
- Migration script (`scripts/migrate_env_to_latlng.py`) that is idempotent — safe to re-run on any legacy env.
- Converted fixtures (`tmp_environment.json`, `mission_control_drone1.json`) with `.bak` originals alongside.
- The converted fixtures are **not yet consumed by the running app** — the backend still reads `x`/`y` and will
  break if you try to load them through the current solver. To test that Phase 1 is safe:
  - `git checkout main` → restore originals with `cp *.bak` (or reset the JSON files).
  - OR just don't load those specific fixtures until Phase 2 lands.
  - Running the app on the `feat/latlng-migration` branch without running Phase 2 will break env load.
  - Running the app on `main` is unaffected — Phase 1 only touches files the main branch doesn't have.

### Recommended next session

Do Phase 2 with a human in the loop:
1. Write `_project_env_to_local_km` + `_unproject_solution_trajectories` helpers.
2. Write the standalone test script described in Phase 2.
3. Wire the shim into `solve_mission` and `solve_mission_with_allocation`.
4. Run the test script. Iterate until clean.
5. Commit.

Phase 3 can then follow with visual verification in a browser after each sub-step (transforms, then rendering,
then interaction, then export/import, then SAM geometry).
