# Unify Distance-Frame + Visited-Target — Design Spec

Branch: `refactor/unify-distance-visited`. Goal: kill the two cross-cutting
duplications that keep producing bugs (fuel/cut/replay each re-fight the same
frame + marking issues). Grounded in a full read of the current code (not memory).

---

## Part A — Distance frame

### Current reality (the map)

One builder exists: `computeAnimationCumDist(trajectory, route, matrixObj, opts)`
([latlng_utils.js:302](../../webapp/latlng_utils.js#L302)). It has TWO internal
regimes:
- **matrix profile** (SAM-aware, §10) when `route` + `matrixObj` valid and
  `!opts.forceHaversine` → `buildRouteDistanceProfile` + `buildTrajectoryCumDistFromProfile`.
- **haversine fallback** otherwise (and forced for full-mission replay via
  `forceHaversine`).

Called from 4 sites: init (10451 draft, 11038 startAnimation), segment-switch ×2
(11773, 11909). Plus **3 MANUAL haversine cumDist builds that bypass it**:
- isr.js:2443 (acceptEdits unvisited-target detection)
- isr.js:10750 (freezeAtCheckpoint unvisited-target detection)
- isr.js:11159 (startAnimation loiter rebuild — legitimate local adjustment)

`droneState.cumulativeDistances` / `.totalDistance` written at 4 sites (the 4
builder calls). `distanceTraveled` written at 9 sites (init, per-step advance ×2
loops, waypoint snap ×2, lost-stop, end-clamp ×2, cut). Frame is
SEGMENT-RELATIVE (resets to 0 each segment; `segmentStartOffset` holds the global
offset).

### The disagreement that causes bugs

The matrix profile assumes `route` waypoints line up with `trajectory`. When the
trajectory is STITCHED across segments (full-mission replay) the route is the
last-segment-only route → profile computes a too-short total → drone crawls/stops.
That's why `forceHaversine` was bolted on. So today: per-segment animation uses
the matrix frame; stitched replay uses haversine. Two frames, chosen per-caller.

### Target design

ONE function the four call sites use, that picks the correct frame from the
INPUTS, not from a per-caller flag:
- If the trajectory's waypoints (route) map cleanly onto the matrix → matrix frame.
- If not (stitched / route-trajectory mismatch) → haversine over the actual points.
- Decision made INSIDE the builder by validating route↔trajectory↔matrix
  coverage, so callers stop passing `forceHaversine`.

The 3 manual haversine builds (2443, 10750) should call the same builder (with a
"haversine only" intent that the builder already supports as its fallback), so
there is exactly ONE place that turns points into a cumDist array.

### Open decision (needs answer before coding)

**D1. DECIDED = (a) haversine for replay.** Builder self-selects: per-segment
animation keeps the matrix frame; a stitched/mismatched trajectory falls to
haversine over the actual points. Replays an already-validated flight, so the
non-SAM-aware measure is acceptable. No whole-mission-route reconstruction.

---

## Part B — Visited targets

### Current reality (the map)

Drawn from ONE read: `state.visitedTargets.includes(t.id)` (isr.js:5316, green X).
Owner map `state.visitedTargetByDrone` drives per-drone stats.

But WRITTEN in many patterns:
- **Reset to []** — 9 sites (init/abort/reset/import/replay-start).
- **Wholesale replace (=)** — 3 sites (acceptSolution: from JSON, from
  calc-from-routes, final-accept = ALL targets).
- **Incremental push** — `markTargetVisitedByDrone` (the during-animation writer,
  6 call sites) + 2 segment-switch merges.

`markTargetVisitedByDrone` ([isr.js:7433](../../webapp/isr.js#L7433)) appends to
visitedTargets + sets owner. Marking decision = `isTargetVisited` (vertex-reached
on the cumDist frame, 5 call sites). Owner map rebuilt by
`rebuildVisitedTargetOwnersFromRoutes` (6 call sites, always paired with a
wholesale replace).

### The disagreement that causes bugs

Two SOURCES of "visited": (1) live vertex-crossing during animation, (2)
deterministic segment data (`frozenTargets`/`visited_targets`) restored on
switch/import/accept. They can disagree — e.g. replay marks only last-segment
targets because the route-membership filter uses last-segment route (the
continuous-replay bug we deferred). The owner map needs a separate rebuild call
every time, easy to forget.

### Target design

ONE rule: **a target is visited iff the drone's position has passed its vertex on
the canonical distance frame** (Part A). Everything else derives:
- Live animation: the existing `isTargetVisited` + `markTargetVisitedByDrone` IS
  this rule. Keep it as the single writer.
- Segment data restore (`frozenTargets`): treat as a SEED of already-passed
  vertices, funneled through the same marker, not a parallel writer.
- Owner map: updated INSIDE the single marker (never a separate forgotten call).
- Reset: one `clearVisited()` that zeroes both maps (replaces the 9 scattered
  resets).
- Final-accept "all visited": one explicit `markAllVisited()` path.

So: `markTargetVisitedByDrone` (single incremental writer, updates owner inline),
`clearVisited`, `seedVisitedFromSegment`, `markAllVisited` — 4 named writers, no
raw `state.visitedTargets =`/`.push` anywhere else.

### Open decision

**D2. DECIDED = follow-up.** This branch is Phases 1+2 only. The whole-mission
route reconstruction (Phase 3) and the continuous-replay refuel box (memory 1c)
are deferred to a later branch.

---

## Sequencing (proposed)

Both parts are big and touch the hot animation loop. Do them as SEPARATE,
independently-testable phases on this branch, committing between:

- **Phase 1 — Distance:** consolidate to one builder that self-selects the frame;
  route the 2 manual haversine builds through it; drop `forceHaversine` from
  callers. Verify: solve/animate/cut/refuel/replay all still position + mark
  correctly (regression of everything we fixed this week).
- **Phase 2 — Visited:** introduce the 4 named writers; replace the scattered
  resets/replaces/pushes; owner map updates inline. Verify: green X timing across
  solve/cut/import/both replay modes.
- **Phase 3 (optional) — whole-mission route:** reconstruct it; fixes
  continuous-replay refuel box + makes replay matrix-framed. Only if D1=(b)/D2=yes.

Each phase is revertible. No big-bang.

---

## Risk

This is the hot path for EVERY mission. A frame error = drone in the wrong place;
a visited error = wrong green X / wrong stats. The week's bugs all lived here.
Mitigation: phase-by-phase, commit between, regression-test the full
solve→cut→refuel→replay matrix after each phase before moving on.
