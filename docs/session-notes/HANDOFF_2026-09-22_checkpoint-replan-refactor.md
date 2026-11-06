# Handoff — 2026-09-22 checkpoint-replan refactor + bug fixes

Scope for this session, set explicitly by the user: **clean 2D (Grid) is the
hard requirement**, including live mid-flight editing (pause → disable a
drone / add a target → accept → resume), because that's a named selling
point for an October funding-agency demo. **Imported/pre-solved mission
replay does NOT need to be demo-solid** — it was descoped mid-session. 3D is
explicitly optional stretch scope, not touched this session (reverted to
baseline early on; see "3D work reverted" below).

All changes below are **uncommitted** working-tree edits on top of the
existing branch (`feat/v4-ordered-constraints` per earlier session notes —
confirm before committing). Nothing was committed during this session by
design; the user should review and commit deliberately.

## What actually shipped (confirmed working by the user)

1. **Stage 1 — unified the two cut-entry-point functions.**
   `freezeAtCheckpoint()` (manual "C" keypress cut) and
   `prepareCutFromCurrentState()` (live-edit-Done cut) duplicated ~150 lines
   of near-identical "freeze every drone at its current position" logic,
   with small silent differences (one checked for a drone that already
   finished its route before freezing it, one didn't; one reset
   `state.animation.missionComplete`, one didn't). `freezeAtCheckpoint` is
   now a thin wrapper that awaits `prepareCutFromCurrentState()` and adds
   only what's specific to a plain manual cut (the average-progress log
   line, a direct redraw). Verified working via the C-key cut path
   end-to-end, including Run Planner → Accept → Animate.
   File: `webapp/isr.js`, `prepareCutFromCurrentState` (~line 2461),
   `freezeAtCheckpoint` (~line 12203), `cutAtCheckpoint` made `async` to
   `await` it (~line 14063).

2. **Stage 2 — unified the "resume node" field-setting logic in
   `buildCheckpointEnv()`.** Three near-copies of the same
   `start_airport`/`start_id`/`home_airport`/`start_fuel`/`refuels_used`
   assignment existed across the LIVE CREATION PATH and the two branches of
   the CHECKPOINT path (checkpoints-array primary branch,
   synthetic_starts fallback branch). Found real drift by comparing them:
   - LIVE CREATION PATH never set `start_fuel` at all — a drone resuming
     from an already-committed segment would have silently gotten a full
     tank on that replan instead of its real remaining fuel (§11.10
     fuel-neutral-cut invariant violated).
   - The synthetic_starts fallback branch never set `refuels_used`.
   - The checkpoints-array branch deliberately does NOT set `home_airport`
     (an explicit prior fix — "unread field — removed"); the other two
     branches do set it.
   Factored into one shared helper, `applyResumeNodeToDroneConfig(cfg,
   nodeId, did, {setHomeAirport})`, called from all three sites so they
   can't drift again. File: `webapp/isr.js`, helper at ~line 10594,
   call sites in `buildCheckpointEnv` (~line 10600 onward).

3. **Stage 3 — closed a `liveEditDisabledDrones` clearing gap in the main
   Accept path.** Same shape as several other gaps found and fixed earlier
   this session (see below): `acceptSolution()`'s legacy "Normal workflow:
   ADD new segment" branch cleared `state.pendingLostDrones = []` after
   commit but never cleared `state.liveEditDisabledDrones` alongside it —
   the one spot an earlier pass through the file (which fixed ~10 other
   such sites) had missed. Fixed to clear both together.
   File: `webapp/isr.js`, ~line 3066 (search `state.liveEditDisabledDrones = [];`
   near the pending-cut-data clear block inside `acceptSolution`).

4. **Real root cause fix (server-side) — disabled drone silently dropped
   from checkpoint-replan responses.** This was the actual cause of the
   most-reproduced bug tonight ("red diamond appears correctly, but the
   trajectory past it stays visible / the drone flies through it anyway").
   `split_solution_to_virtual_legs()` (called by the post-solve optimizer
   pipeline `apply_optimizer_pipeline` → `run_pipeline_on_solution`) iterated
   `drone_configs` and did a bare `continue` for any drone with
   `enabled is False` — **never adding it** to `split_routes`/
   `split_configs`/`leg_mapping`. Since the pipeline's output wholesale-
   replaces `result["routes"]` (`server/api/solve_routes.py` line ~475:
   `result["routes"] = pipe["routes"]`), a disabled drone vanished from the
   response entirely, even though the FIRST solve pass
   (`solve_mission_with_allocation`) correctly included it with an empty
   route. The client's merge code (`applyDraftSolutionToUI`) then had no
   way to distinguish "drone omitted because disabled" from "drone omitted
   because it already finished its mission" (both cases leave old route
   data untouched by design, for the completed-drone case) — so a
   disabled drone's stale, pre-disable trajectory kept displaying and (in
   some but not all cases seen tonight) being flown.
   **Fix:** `split_solution_to_virtual_legs` now passes a disabled drone
   through unchanged (into `split_routes`/`split_configs`/`leg_mapping`,
   same shape as any other drone that doesn't need leg-splitting) instead
   of dropping it. `merge_virtual_legs_n`'s existing "non-virtual routes
   pass through" contract (confirmed by reading its own docstring and the
   `passthrough` dict handling at line ~524) picks it up correctly with no
   further changes needed downstream.
   File: `server/solver/virtual_drones.py`, `split_solution_to_virtual_legs`,
   ~line 310-313.
   **Confirmed working by the user for both D1 and D2, independently
   tested.** This is very likely the fix that also resolved the
   originally-reported "trajectory after the red diamond stays visible"
   bug that was chased on and off most of the session before this was
   found.

## Also fixed earlier in the session (before the Stage 1-3 refactor)

A large batch of `state.pendingLostDrones` / `state.liveEditDisabledDrones`
clearing-pair gaps were found and fixed across ~10 reset/accept/import
sites in `webapp/isr.js` — every place that cleared one of the two without
clearing the other. This was the root cause of the original "moving red
diamond" bug (C1 shows red briefly during replay, should stay white until
the actual segment where a drone was lost). **Confirmed working by the
user.** If re-auditing this area, grep for `state.pendingLostDrones = \[\]`
and confirm every hit has a matching `state.liveEditDisabledDrones = [];`
line immediately adjacent.

## Known open issues — NOT fixed, need a fresh, deliberate repro

### 1. Intermittent freeze on Accept ("Run Planner does nothing")
Reported once, did not reproduce on a second attempt with identical steps
(same env file, same sequence: import → edit/add target → accept → edit
again, kill drone on last leg → accept). Consistent with a timing-dependent
issue, not a deterministic code bug — but not investigated at all beyond
that one non-repro. If it recurs: get the browser console output captured
**at the moment of the freeze**, not after re-testing (a fresh page load
loses the exact state that caused it).

### 2. Target double-assigned to two drones (found via mixed C-key + Edit cuts)

**RESOLVED 2026-09-23.** Real cause was not the `buildCheckpointEnv` filter.
Every visited check looked targets up via
`state.initialEnvSnapshot?.targets || state.env.targets`, which never falls
back to `state.env` once the snapshot exists. A target added after a C-key cut
was never merged into the snapshot (the merge in `acceptSolution` filtered
snapshot targets against the snapshot, so always came back empty), so the
drone flew through it with no green cross, it stayed out of `visitedTargets`,
and the next cut's replan handed it to another drone. Fix: the three lookups
use `getAllKnownTargets()` (snapshot ∪ env ∪ segments), and the accept merge
adds any `state.env` target the snapshot lacks. Confirmed by live repro with
an env holding only the original targets. Notes below kept for history.
**Confirmed real** via live console dump — not a display bug, a genuine
data bug:
```json
{
  "visitedTargets": ["T1","T2","T3","T4","T5"],
  "visitedTargetByDrone": {"T1":"1","T2":"2","T3":"1","T4":"2","T5":"2"},
  "d1Route": ["A1","T1","A2","T3"],
  "d2Route": ["A2","T2","A1","T4","T5","T3"]
}
```
T3 appears in **both** `d1Route` and `d2Route` — both drones' accepted,
committed segment routes include the same target.

From the ISR Planner debug tab (NOT dev console — the user has an
in-app debug log, separate from browser console, that's often more useful
and doesn't need DevTools open): at `12:58:14 PM`, accepting a segment gave
D2 the allocation `{"2":["T4","T5","T3"], ...}`. Later, during a **replay
from segment 0** (triggered by re-clicking Animate after a prior mission
completed), the log shows `🎬 Animation starting. Visited targets: [T1, T2]`
at `1:01:36 PM` — **T3 is not yet in the visited list** at that point, even
though T3 was already committed into D2's segment-3 route ~3 minutes
earlier in the same working session.

**Working theory, NOT confirmed:** the double-assignment was likely baked
in at accept time, not a replay-time bug — i.e., when D1's segment
(whichever one ends up containing T3) was solved, T3 may not yet have been
excluded from D1's candidate target list, because D2 hadn't been given T3
yet at that point in the session. This would mean the bug is in the
target-exclusion filter inside `buildCheckpointEnv()` (three branches, each
independently building `env2.targets` — see the Stage 2 write-up above for
why these branches have a history of drifting) — specifically: does the
filter correctly exclude a target that's already assigned to a **different
segment's already-accepted route**, or only targets in `state.visitedTargets`
in the strict sense of "a drone's flown trajectory geometrically passed
this point"? These are NOT necessarily the same set — a target can be
**allocated** to a drone's accepted route without any drone having
literally flown past it yet (e.g., it's later in that drone's route, not
yet reached).

**Next step:** redo the mixed C-key/Edit repro **narrating each action as
it's taken** ("pressed C", "opened Edit, unchecked nothing, added target
X, hit Done", etc.) so the exact order of accepts is unambiguous — the
previous attempt at diagnosing this could not pin down whether D1's or
D2's segment containing T3 was accepted first, which is the key fact
needed to confirm or rule out the theory above. Once order is known, check
`buildCheckpointEnv`'s console debug lines (search the browser console
filter box for `buildCheckpointEnv:` — prints `visitedTargets=[...]` and
`targets=[...]` on every call) for the specific solve that (wrongly)
included T3 in the losing drone's candidate list.

## 3D work — reverted, not in current code

All 3D (Cesium) rendering fixes explored earlier in the session (checkpoint
diamond altitude, animation flicker, drone marker altitude ramp, etc.) were
reverted via `git checkout -- webapp/isr.js webapp/cesium_view.js
webapp/index.html` partway through the session, after the user judged the
compounding 3D patches were making things too unpredictable to continue
safely. `webapp/cesium_view.js` and the 3D-related parts of
`webapp/index.html` are back to their last-committed state — none of that
work is present. If 3D is picked up again, start fresh rather than trying
to recover the reverted patches; the session's own retrospective (see
"Scope/priority decision" below) concluded the right approach is giving
trajectories a real `[lat, lng, alt]` third coordinate computed once
server-side, rather than the client-side altitude-ramp heuristics that were
being patched repeatedly.

## Scope/priority decision made this session (for context on future work)

The user is delivering this to a funding agency in ~4-6 weeks (October),
needs to be feature-complete in ~3 weeks. Explicit priority order set by
the user: **(1) clean 2D including live mid-flight editing — hard
requirement, (2) agent capable of learning — believed already built (RAG
Phase 2, retrieving past missions into the v4 agent's prompt) and just
needs verification, not new work, (3) 3D — explicitly optional, "only
strengthens our chances for future funding," ships only if 1 and 2 are
solid with room to spare.**

A background audit agent mapped the full "accept a checkpoint replan"
architecture before this session's refactor work began. Its top-line
finding: nearly all the duplication in this area traces to one implicit,
never-made-explicit distinction — "was this mission imported from a
pre-solved file, or built live in the browser" — re-detected independently
by proxy conditions (`segmentedImport.isActive()`,
`state.importedSegmentCuts`, `state.checkpoint.active`, etc.) in every
function instead of being a single passed-down flag. Given imported-mission
replay was descoped, Stage 2/3 of this session's refactor deliberately did
**not** touch the imported-mission branches (`segmentedImport.isActive()`
/ `state.importedSegmentCuts` gated code in `buildCheckpointEnv` and
`acceptSolutionWithManager`) — only the live-mission branches were unified.
If imported-mission replay is ever brought back into scope, that work was
explicitly left undone and the audit agent's original full report (not
preserved verbatim in this file — it was a conversation message, not
written to disk) estimated reconciling both mechanisms at 2-4 days of
focused, higher-risk work.
