# Altitude Model — every position has three coordinates

**Status:** approved 2026-09-25 (Q1–Q3 answered below). **Phase A built**
(committed 4c7a479). **Phase B built** (b312e35). **Phase C built**
(uncommitted, see "Phase C — as built" at the end); Phase D next.

## Decisions (Kamal, 2026-09-25)

1. **Every position has three numbers:** latitude, longitude, altitude —
   airports, targets, SAMs, tankers, drones, cut points, every path point.
2. **All altitudes are relative to sea level.** Stored as the height Cesium /
   GPS use (WGS84 ellipsoid); it differs from true mean sea level by up to about
   ±100 m (~15–20 m in Colorado), which doesn't matter for planning. Labelled
   "altitude" in the UI.
3. **Targets and airports sit on the ground**, at the real ground elevation of
   their location (an airport on a mountain is high up).
4. **Each drone flies ONE cruise altitude.** Drones in the same mission may fly
   different altitudes.
5. **A drone changes altitude only**
   - at a **cut**, when the operator gives it a new cruise altitude, and
   - around a **refuel**: to the tanker's altitude for a tanker refuel, or down
     to the airport (landing) for an airport refuel —
   and **returns to its cruise altitude after refuelling**.
6. **Every altitude change uses the drone's own climb/descent angle**, including
   take-off from the airport and the final landing.
7. **A tanker holds the altitude it is given.**

## 1. Data model

Units are km everywhere (matching `range_km`, `fuel_budget`, `cruise_altitude_km`).

| Item | New field | Meaning | Source |
|---|---|---|---|
| Airport | `alt_km` | ground elevation | terrain (§2) |
| Target | `alt_km` | ground elevation | terrain (§2) |
| SAM | `alt_km` | site elevation = centre of its threat sphere | terrain (§2) |
| Tanker (`type: "tanker"` airport) | `alt_km` | holding altitude | operator |
| Drone config | `cruise_altitude_km` | its one cruise altitude | operator |
| Drone config | `climb_angle_deg` | its climb/descent angle | operator |
| Cut point / diamond | `[lat, lng, alt_km]` | the drone's altitude at the cut | flight profile |
| Path point (trajectory) | `[lat, lng, alt_km]` | altitude along the flight | flight profile (§3) |

- The mission-wide **Cruise Altitude** and **climb angle** boxes become the
  *defaults* for drones that don't set their own.
- **Old files load unchanged:** a missing `alt_km` on the ground is looked up
  (§2) or 0; a mission-wide `cruise_altitude_km` / `cesium_climb_angle_deg`
  becomes each drone's value; 2-number points get their altitude from the
  flight profile.
- Export writes all of it (one helper, like `stampMissionSettings`).

## 2. Ground elevation — one source: the terrain the 3D view draws

The 3D view already uses Cesium World Terrain when a Cesium token is configured
(`webapp/cesium_view.js`), and flat ground without one.

- When an airport, target or SAM is **placed, moved or imported without an
  elevation**, the browser samples that same terrain at its location
  (`Cesium.sampleTerrainMostDetailed`) and stores `alt_km`.
- So 2D data and the 3D picture can never disagree — nothing floats or sinks.
- **Offline / no token:** elevation 0, which matches the flat 3D ground.
- The server never guesses terrain; it uses the stored numbers.
- Elevations are saved on export, so imports don't need the network.

## 3. Flight profile — computed once, on the server

A new server step gives every path point its altitude, for every route the
server returns (Run Planner, agent, route edits — the shared evaluator):

- **Take-off:** from the airport's ground elevation, climb at the drone's angle
  to its cruise altitude (horizontal distance = Δh / tan(angle)).
- **Cruise:** level at `cruise_altitude_km`.
- **Tanker refuel:** change to the tanker's altitude before reaching it, back to
  cruise after.
- **Airport refuel:** descend and land (airport elevation), climb back to cruise.
- **Landing:** descend at the angle to the end airport's elevation.
- **Cut with a new altitude:** the new segment starts at the diamond's
  altitude and climbs/descends to the new cruise altitude.
- **Leg too short** to reach cruise between a climb and a descent: the drone
  peaks where the two meet (and a warning says so).
- Points are inserted where the altitude starts/stops changing so the 3D line
  shows the real ramps; the animation frame carries the altitude too.
- The 3D view **draws these altitudes** and stops computing its own ramp
  (the client-side climb heuristics in `cesium_view.js` are removed).

## 4. Planning with per-drone altitude

- **SAM slice per drone:** a drone at altitude h sees a SAM (site elevation e,
  range r) as a circle of radius √(r² − (h − e)²); none if |h − e| ≥ r. Today one
  mission-wide altitude shrinks every SAM for every drone
  (`_apply_cruise_altitude_to_sams`); it becomes per drone.
- **Distance matrix per altitude:** SAM-aware distances depend on the slices,
  so the planner builds one matrix per distinct cruise altitude in the mission
  (usually 1–3), cached as today, and each drone's allocation, ordering,
  optimization and route evaluation use the matrix for its altitude. This is
  the largest change: the allocator and the post-optimizer compare drones, so
  they must look up each drone's own distances.
- **Simplification (agreed direction):** SAM avoidance is planned at cruise
  altitude. During take-off, landing and tanker transitions a drone is at other
  altitudes; if that puts it inside a SAM's reach, the app **warns** rather than
  plans around it. Can be made exact later.
- **Fuel stays horizontal distance** (as today, decided Q2). Climbing costs are
  not modelled; the 3D path is slightly longer than the fuel figure. Fuel is
  computed in ONE place so a climb cost can be added later without touching
  the planner's callers.
- **Detection radius stays horizontal** (as today).

## 5. Warnings

- A drone's cruise altitude is below the ground at an airport, target or SAM
  site it uses.
- A tanker's altitude is below the ground under it.
- A climb/descent can't complete within a leg (the drone peaks early).
- A take-off, landing or tanker transition passes within a SAM's reach at its
  lower altitude.
- **Terrain obstruction (decided Q3):** the path goes below the ground anywhere
  along it (e.g. a mountain between two waypoints higher than the cruise
  altitude). The server has no terrain, so the browser samples the same terrain
  the 3D view draws along each drone's path after a solve and warns where the
  path's altitude is below the ground. Planning does not avoid terrain — known
  limitation, noted in the UI warning.

## 6. User interface

- **Asset tab:** per-drone *Cruise altitude* and *Climb angle* columns.
- **Tanker:** an altitude field when placing/editing a tanker.
- **Env tab:** the mission Cruise Altitude and climb angle become "defaults".
- **Changing a drone's altitude at a cut:** in the live-edit Asset tab, like
  disabling a drone; the agent's `set_drone` gains `cruise_altitude_km`.
- **Dashed SAM boundary (decided Q1):** each drone's slice is drawn in that
  drone's colour. Drones at the same altitude share one slice (drawn once).

## 7. Code impact (known)

- Server: `_apply_cruise_altitude_to_sams` (per drone), `sam_distance_matrix`
  (per altitude), allocator + post-optimizer (per-drone matrices),
  `route_evaluation` (drone's altitude), `solve_routes`, agents (`set_drone`),
  new flight-profile step, animation frame.
- Browser: 13 places assume a position has exactly 2 numbers (e.g. cut
  positions, split points: `webapp/isr.js` lines ~1438, 2546, 2952, 3509, 4009,
  5160, 5220, 6301, 6323, 11116, 11162, 11741, 11758) — changed to "at least 2"
  before any 3-number position exists; `refreshSamWrapping`; `buildSolveRequest`;
  export/import; `cesium_view.js` draws server altitudes.

## 8. Order of work

| Phase | What | Behaviour change |
|---|---|---|
| **A. Three numbers everywhere** | `alt_km` on all items, terrain sampling, tanker altitude, per-drone cruise altitude + climb angle fields (defaulting to today's mission values), 3-number points accepted everywhere, export/import, old files load | **None** — all drones default to the mission altitude, so plans are identical |
| **B. Per-drone planning** | per-drone SAM slices, per-altitude matrices, allocator/optimizer per drone, warnings | Drones at different altitudes plan around different SAM slices |
| **C. Flight profile** | server altitude per path point (take-off, cruise, refuel transitions, landing), animation frame altitude | Paths carry real altitudes; 2D unchanged |
| **D. Altitude changes + 3D** | new altitude at a cut, 3D draws server altitudes (remove client ramps), agent `set_drone` altitude | 3D shows the true profile |

Each phase ships with tests and is usable on its own. A and C are mostly
additive; B is the risky one (planner core) and needs the most tests —
including "all drones at the same altitude gives exactly today's plans".

## Decided questions

- **Q1 — Dashed SAM boundary with several altitudes:** each drone's slice in
  its own colour (§6).
- **Q2 — Climb/descent fuel:** fuel stays horizontal distance for now, in one
  function so it can change later (§4).
- **Q3 — Terrain between waypoints:** not avoided in planning; the browser warns
  where a path goes below the ground (§5). Known limitation.

## Phase A — as built (2026-09-25)

- **Ground elevation:** `cesiumView.groundElevationsKm()` (`webapp/cesium_view.js`)
  samples Cesium World Terrain — the terrain the 3D view draws — or returns 0
  without a Cesium token / offline / on error. `fillMissingAltitudes(env)`
  (`webapp/isr.js`) sets `alt_km` on airports, targets and SAM sites that lack
  it, and gives tankers the mission cruise altitude. It runs when an item is
  placed, after a drag (a moved ground item is re-sampled; a tanker keeps its
  altitude), after every import (live env and every segment env, via
  `fillMissingAltitudesEverywhere`) and on the initial page load. Exports carry
  `alt_km` because the env is exported as-is.
- **No Cesium token is configured on this server** (`.env` has no
  `CESIUM_ION_TOKEN`), so elevations are 0 today; setting the token turns on
  real terrain with no code change.
- **Positions with three numbers are accepted:** the 13 `length === 2` /
  `!== 2` position checks in `isr.js` and 3 in `segment_manager.js` /
  `segmented_mission.js` are now "at least 2".
- **Drone cruise altitude / climb angle are NOT per-drone fields yet.** A missing
  per-drone value means "use the mission default", so nothing is copied into
  each drone now (it would drift from the mission box). The fields arrive in
  Phase B, where the planner uses them.
- **The server planner stays 2D inside.** Cut positions and synthetic starts
  reach it as `{lat, lng}` objects, and Phase C adds altitude to paths as a final
  step, so the planner's `x, y = point` internals are untouched.
- **Tests:** `tests/test_altitude_phase_a.py` (plans identical with `alt_km` on
  every item; route evaluation unaffected), `tests/e2e/test_altitude_phase_a.js`
  (elevation fill incl. tanker + offline 0; sequence code with `[lat, lng, alt]`
  points).

## Phase B — as built (2026-09-25)

- **One helper module:** `server/solver/altitude.py` — a drone's altitude
  (`drone_altitude_km`: its `cruise_altitude_km`, else the mission's), the SAM
  slice `sqrt(r² − (h − e)²)` measured from the SAM site's elevation `e`
  (`slice_sams_at_altitude`, `env_for_drone`, `sams_for_drone`), per-altitude
  matrices and warnings. `_apply_cruise_altitude_to_sams` now calls it.
- **Re-slicing is safe:** a sliced SAM keeps its full range in
  `ground_range_km`, so an env sliced at the mission altitude can be re-sliced
  at a drone's altitude, and slicing twice never shrinks twice (replaces the
  old `_cruise_altitude_applied` flag).
- **One matrix per altitude:** `solve_mission_with_allocation` builds the
  mission matrix as before; when a drone flies another altitude,
  `attach_altitude_matrices` adds `by_altitude` (altitude → matrix built with
  SAMs sliced there) and `drone_altitudes` (drone → altitude) to it.
  `matrix_for_drone(dist_data, drone_id)` hands each drone its own. Every
  matrix carries `altitude_key`. The cache key includes every drone's altitude.
  With all drones at the mission altitude neither field exists — plans are
  identical (tested with and without the optimizer).
- **Who asks for a drone's own distances:** allocator (all strategies; a target
  inside the SAM at a drone's altitude is taken off that drone's capabilities,
  and only targets no drone can reach are dropped), per-drone Held-Karp solve
  (matrix, SAM slices and excluded targets rebound per drone), all optimizers
  (Insert ×2, Swap, Or-opt, No-Cross, unified), refuel expansion, trajectory
  regeneration (optimizer, pipeline runner, agent), animation frame spans,
  and the route evaluator (operator edits + agent `set_route`). The evaluator
  checks the matrix was built at the drone's CURRENT altitude and otherwise
  measures along the trajectory.
- **Also fixed:** a solve that bypassed the API boundary (every agent solve)
  sliced SAMs in the matrix but drew trajectories around the full ground
  circle. Trajectories are now always planned around the drone's slice.
- **Asset tab:** per-drone *Alt* (km) and *Clb* (deg) columns; blank = mission
  default (`cruise_altitude_km` / `climb_angle_deg` on the drone config, null
  when blank). Exported with the drone configs.
- **Dashed SAM boundary:** one altitude → yellow as before; several → one
  outline per altitude in its drone's colour (drones sharing an altitude share
  one, in the first one's colour); disabled drones are not drawn.
- **3D:** each drone's route and marker at its own altitude and climb angle;
  SAM spheres centred on the site elevation; a tanker at its `alt_km`.
- **Agent:** `set_drone(..., cruise_altitude_km=)`; `get_drones` shows each
  drone's altitude.
- **Warnings:** the solve response carries `warnings` (shown in Debug): a
  drone cruising below the ground at an airport or target on its route.
  Take-off/landing/tanker transitions through a SAM and "climb can't complete"
  need the flight profile — **Phase C**. Tanker-below-ground needs terrain under
  the tanker — browser, with the tanker altitude field.
- **Not changed:** time sync and the v4 agent still use the mission matrix
  (distances at the mission altitude).
- **Tests:** `tests/test_altitude_phase_b.py` (slice rules, identical plans at
  one altitude, high drone takes the target a low one can't reach, different
  slices per drone, route edits at the drone's altitude, warnings),
  `tests/e2e/test_sam_boundary_altitude.js` (site elevation, per-altitude
  outlines).

## After Phase B — altitude per drone only, Asset tab layout (2026-09-26)

Decided with Kamal from clickable mockups (layout "C").

- **No mission-wide altitude or climb angle.** The Env tab's Cruise Altitude and
  3D Climb Angle boxes are gone. Every drone carries `cruise_altitude_km`
  (0 = ground level: SAMs at full size) and `climb_angle_deg` (default 12).
  An older file's mission value is copied into each drone on import
  (`droneConfigFromSaved`), so its plans don't change. Exports carry the values
  inside the drone configs; nothing mission-wide is written.
- **Server:** an explicit 0 is 0 (`drone_altitude_km`); a config without the field
  still falls back to the env's value (old callers). The mission matrix is built
  at the altitude most drones fly (`with_primary_altitude`, both solve entry
  points), so one shared altitude builds one matrix.
- **Asset tab:** a Drones table (On, Start, End, Fuel, Alt — edited in place)
  with a drawer per drone (Runway needed, Speed, Alt again — the same value —,
  Climb, a target-type grid Visit / Loiter / Detect, refuel chips + Max).
  Several drawers can be open; "Collapse all" closes them; a disabled drone
  doesn't open. Every input kept its old id.
- **No loiter on/off box:** a loiter time above 0 is the switch
  (`loiter_enabled` is set from the times). A file saved with loitering off
  imports its times as 0, as before.
- **Tankers:** a Tankers section (holding altitude, editable; clearance above
  the ground under it) and an altitude box next to the Edit panel's Tanker
  button — the selected tanker's altitude, else the altitude for the next one
  placed; both show the same value. Tankers record `ground_km`.
- **Terrain margin 0.6 km** (2,000 ft, FAA mountainous-area minimum,
  14 CFR 91.177): clearance under it is amber, below ground red. One setting
  (`TERRAIN_MARGIN_KM`), to be reused for drone paths in Phase C. Known gap: the
  ground is sampled at the tanker's point, not over its orbit.
- **Open:** whether drones at another altitude may use a tanker, and how.
- **Tests:** `tests/e2e/test_asset_tab.js` (import migration, loiter rule,
  clearance), updated `test_altitude_phase_a.js` / `test_sam_boundary_altitude.js`,
  `tests/test_altitude_phase_b.py` (explicit 0, primary altitude, one matrix).

## Phase C — as built (2026-09-27)

- **One step:** `server/solver/flight_profile.py` `attach_flight_profile` adds
  `altitudes` (km, one per point) to the animation frame — the densified path
  the animation and the 3D view use. Both frame builders call it: the solve
  (`_attach_anim_frame`, Run Planner and agent) and route edits
  (`route_animation_frame`, operator sequence + agent `set_route`).
- **Rule:** fixed altitudes at take-off (start airport ground), landing (end
  airport ground), airport refuel (its ground) and tanker refuel (the tanker's
  altitude); in between the drone holds its cruise altitude, changing at most
  tan(climb angle) per km from each fixed point. That gives climb-out, descent,
  tanker transitions both ways and an early peak on a short leg. A route
  starting at a cut node is airborne. Cruise 0 ("ground level") holds the
  airports' height.
- **Tankers within reach:** a drone may refuel only at a tanker within 2 km of
  its cruise altitude (`MAX_TANKER_ALTITUDE_CHANGE_KM`): 2 km at 12° takes
  9.4 km each way, which fits these missions' legs, and limits time at
  altitudes the route wasn't planned for. Out-of-reach tankers are removed from
  a drone's refuel options on the server (`without_unreachable_tankers`, both
  solve entry points) and greyed in its refuel chips, with the reason.
- **Warnings** (solve response, Debug log): altitude can't change in time
  between two fixed points; a leg too short to reach cruise (with the peak);
  a climb/descent passing within a SAM's reach at its lower altitude.
- **3D:** routes and markers use the server altitudes; the old client-side
  climb ramp remains only for paths without a profile (stitched replays).
- **One frame (fixed 2026-09-27):** the frame's distance is flying only, plus
  loiter as a HOLD at each loiter target (the point repeats, the distance
  advances, the drone stays put; `hold_distances`). The solver's per-leg fuel
  distances count loiter, so it is taken out of them (`travel_leg_distances`)
  and counted once, as the hold. The browser plays the server frame as is (it
  used to re-insert loiter, double-counting it and shifting the 3D altitude
  lookup — D1 never reached a tanker's altitude). Climbs and descents are
  measured on ground distance, never on the playback distance. Time-on-target
  never hovers: its delay stays spread over the approach (or delays take-off).
- **Not yet (Phase D):** a new altitude at a cut; cut points carrying their
  altitude; removing the client ramp entirely.
- **Tests:** `tests/test_flight_profile.py`.
