# Bug hunt — drone disabled on final leg flies past its red diamond

**Status:** root cause narrowed to one specific mechanism, NOT yet confirmed live.
Confirmed facts below are read from code + one real console capture. The
remaining step needs a browser (I can't drive one) — see "Next verification
step" at the bottom.

## Symptom (user report)

Grid/2D view. Drone disabled (unchecked) via live-edit while on its FINAL
leg (no further cut planned after this one). The red diamond appears at
the correct cut point, but the drone's trajectory continues past it to the
original end airport instead of stopping there.

Contrast: the SAME kind of disable, done earlier in the mission (with a
real next segment created), works correctly — red diamond stays, drone
stops. That case was verified working in this session (Grid "all works
perfectly" per user, 2026-09-18).

## Confirmed facts (from a live console capture this session)

Console command run live, right after accepting the final-segment replan
with D2 disabled:

```js
console.log(JSON.stringify(missionReplay.getSegments().map((s,i)=>({i, lostDrones:s.lostDrones, cutDistance:s.cutDistance})), null, 2))
```

Result:
```json
[
  { "i": 0, "lostDrones": null, "cutDistance": null },
  { "i": 1, "lostDrones": null, "cutDistance": 7.13 },
  { "i": 2, "lostDrones": ["2"], "cutDistance": 14.43 }
]
```
(A later capture showed a 4-segment version with `lostDrones: ["2"]` at
segment 3, `cutDistance: 19.4` — same shape, one more segment.)

**This proves the data is correct.** `missionReplay`'s per-segment
`lostDrones` and `cutDistance` are written correctly for the final-segment
disable case, via the merge logic in `acceptSolutionWithManager()`
(`webapp/isr.js` ~3286-3306, unconditional, runs regardless of whether
there's a next segment). This rules out every "the derivation never runs
for the last segment" theory I chased first — the write path is fine.

## The real mechanism found: `acceptSolutionWithManager`'s final-segment branch resets replay to segment 0

`webapp/isr.js`, inside `acceptSolutionWithManager()`, the `else` branch
taken when there's no next segment (`hasMore === false`, i.e. this WAS the
last segment) — around line 4061-4063:

```js
// Reset segment manager to start (so animation starts from segment 0)
segmentedImport._currentSegmentIndex = 0;
missionReplay.resetToStart();
```

So after the final Accept, the mission is set up to **replay from segment
0 on the next Animate click**, not "resume from where we are." This is
almost certainly intentional in general (it's how "Replay Segments" is
supposed to work — walk 0→1→2→3, stopping at each cut diamond via the
segment-switch handler at ~13438-13466, which DOES correctly apply
`_lostAtCutDistance`/truncation for a drone in `seg.lostDrones` — this is
the exact mechanism that worked for the EARLIER, successful D1-at-C3 test).

**What's different in the final-leg-disable repro and not yet explained:**
this reset sets the segment INDEX back to 0, but does NOT touch
`state.animation.drones[did].distanceTraveled` (still holds the live
mid-flight pause value) and does NOT set
`state.animation.forceStartDistanceZero` (unlike the two `missionComplete`
branches earlier in the same function, which explicitly do
`state.animation.forceStartDistanceZero = true` when resetting for a
replay). Also neither `state.pendingCutDistance` nor
`state.checkpoint.active` get cleared anywhere in this branch (grepped,
zero hits) — both still hold their live-pause values.

**Working theory (unconfirmed):** on the next Animate click, the
segment-switch mechanism (`checkSegmentBoundary`, driven by
`state.animation.missionDistance`) either (a) never fires the switch-into
the final segment because the carried-over distance is already past that
segment's `cutDistance`, so the boundary check silently skips straight to
"just keep animating," never reaching the lost-drone stop logic at
~13444-13466 — or (b) some other guard (`hasFrozenCheckpoint`,
`isCheckpointResume` in `startAnimation()`, ~12581-12615) short-circuits
before that logic is even reached, because `state.checkpoint.active` /
`state.pendingCutDistance` are stale leftovers from the pause rather than
being cleared by this accept path.

## What's already ruled out

- **Not a data bug.** `lostDrones`/`cutDistance` are correct in
  `missionReplay` (see console capture above).
- **Not the `resumeAnimation()` stale-restore path.**
  `missionState.pauseContext` is set to `null` at the very start of
  `prepareCutFromCurrentState()` (line ~2500), before the disable/replan
  even happens. By the time Animate is clicked again, `pauseContext` is
  `null`, so `resumeAnimation()`'s own guard makes it a no-op, and
  `getUiPermissions().canResume` should be `false`. `startAnimation()` is
  the function actually invoked.
- **Not the `_fullTrajectory` restore-to-full-path bug either** — added a
  temporary debug line (`[RESUME CHECK]`, `webapp/isr.js` ~12608, still in
  the file, harmless, prints unconditionally right after the
  `isCheckpointResume` guard in `startAnimation()`) and the user confirmed
  it **never printed** on their repro. That means execution never reaches
  line 12608 in `startAnimation()` at all — one of the earlier
  returns/guards is firing, OR the Animate button was disabled and never
  actually called `startAnimation()`. This was NOT re-verified after
  clarifying — session ended before a follow-up capture.

## Next verification step (needs a live browser — I can't drive one)

1. Hard-refresh, redo the exact repro: import multi-segment mission,
   animate, pause on the final leg, disable a drone, Accept.
2. **Before** clicking Animate again, search console for `startAnimation
   called` (the very first line of that function, unconditional,
   already existed before this session's edits) — confirm it's present or
   absent. This alone tells us whether the button click even reaches the
   function.
3. If present: search for `⚠️ Cannot animate` right after it — tells us
   whether the top-level `canAnimate`/`canResume` guard (line ~12434) is
   blocking.
4. If neither blocks: the `[RESUME CHECK]` line (~12608) should print —
   paste its exact text (`isCheckpointResume=`, `missionReplayIdx=`,
   `pendingCutDistance=`).
5. Also worth checking directly in console right after Accept, before
   clicking Animate:
   ```js
   console.log(JSON.stringify({
     segIdx: missionReplay.getCurrentSegmentIndex(),
     checkpointActive: state.checkpoint?.active,
     pendingCutDistance: state.pendingCutDistance,
     missionDistance: state.animation?.missionDistance,
     droneDistTraveled: state.animation?.drones?.["2"]?.distanceTraveled, // swap id
   }, null, 2))
   ```
   This directly confirms or kills the "stale distance vs. reset index"
   theory above.

## Fix direction once confirmed

Most likely fix, once the exact blocking point is confirmed: in
`acceptSolutionWithManager()`'s final-segment branch (`webapp/isr.js`
~4044-4067), clear the pause-era leftovers the same way the
`missionComplete` replay branches already do — `state.pendingCutDistance =
null`, `state.checkpoint.active = false; state.checkpoint.segments = {}`,
and set `state.animation.forceStartDistanceZero = true` so the next
Animate click is unambiguously a fresh, from-segment-0 replay rather than
a hybrid of "reset index" + "stale distance/checkpoint state."

## Debug line left in place

`webapp/isr.js` ~12608 and ~12612 (`[RESUME CHECK]` tag) — harmless,
unconditional `appendDebugLine` calls inside `startAnimation()`. Safe to
remove once this is resolved, or keep if useful for future segment-switch
debugging (gate it behind `window.ISR_DEBUG` like the rest of the
`dbg()`-tagged lines if keeping it long-term).
