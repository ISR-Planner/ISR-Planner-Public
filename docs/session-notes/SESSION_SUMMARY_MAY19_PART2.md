# Session Summary — May 19, 2026 (Part 2)

**Branch:** `feat/optimizer-pipeline`
**Latest commit:** `1673d60` — fix: post-pipeline preserves loiter fuel in route_data["distance"]
**Pushed to:** `origin/feat/optimizer-pipeline`

---

## What was fixed

The Mission Fuel display after Run Planner was showing the route's **travel distance only**, while the animation correctly added `loiter_time × speed` for each visited target. On a 37-target two-drone mission with loiter enabled, the displayed total was 88/300 km but the actual fuel consumed (matching animation) was ~190/300 km — a ~100 km invisible gap.

### Root cause

A three-step chain:

1. `solver_bridge.py:2547` writes `route_data["distance"] = travel + loiter` on the first solve. Correct.
2. The post-optimizer pipeline (Insert → Swap → No-Cross, in [server/solver/post_optimizer.py](server/solver/post_optimizer.py)) runs after. **Swap's and No-Cross's terminal recalc steps were overwriting `distance` with a travel-only sum** computed via `_calculate_route_distance(route)` — silently stripping the loiter component.
3. With `distance` now travel-only, any second-pass optimizer (swap auto-iterate, agent re-pass, replan-from-checkpoint) would compute "remaining fuel" off the wrong number and could approve routes that genuinely exceed budget.

### The fix (already committed in `1673d60`)

In [server/solver/post_optimizer.py](server/solver/post_optimizer.py):

- Added `compute_route_loiter_fuel(route, drone_cfg, target_by_id)` at module top — single source of truth for the formula, mirrors `solver_bridge.py:2458` and the animation's loiter math in `isr.js`.
- Added `_calculate_route_distance_with_loiter` method on both `TrajectorySwapOptimizer` and `CrossingRemovalOptimizer`. The existing travel-only `_calculate_route_distance` stays for sites that need pure travel distance.
- Replaced every site that writes back to `route_data["distance"]` with the loiter-inclusive helper (auto-regen, single-pass terminal, auto-iterate terminal, budget-check fallbacks, No-Cross terminal).
- Persist `loiter_fuel` as a separate field on `route_info`. `TrajectorySwapOptimizer`'s route-copy step now preserves `loiter_fuel` and `trajectory` instead of dropping them. Insert increments `loiter_fuel` by the inserted target's contribution via `best_loiter_extra`.

### Verification

After the fix, on the same env (`isr_env_20260519-1150_N1_1.json` from Downloads), running Run Planner:

- Browser `state.routes`: D1 `distance=90.50` (21 wp), D2 `distance=93.02` (20 wp), total **183.52**
- Stat box displays **184 / 300**
- Animation total: **~190 / 300** (the ~6 km residual is per-waypoint pause distance that animation includes and solver does not — separate, not in scope)

---

## How we got here (debugging journey)

This was a long chase. Worth recording because the false leads tell you what NOT to investigate next time:

1. **Initial guess: loiter calculation in animation was double-counting.** Ruled out by trajectory-haversine sums matching solver output before loiter is added.
2. **Then: my fix wasn't being loaded** (browser cache, stale state, server reload). Spent a chunk of time confirming the server WAS reloading my edits.
3. **Real breakthrough**: an inline `fetch` from the browser console returned **143.95 km** (loiter-inclusive, correct) but the UI showed **88 km**. Different requests, same endpoint.
4. **Why the difference**: a fetch-interceptor showed the UI sends `target_access` restrictions (D1 = c/d/e types only, D2 = a/b/c types only) — partitioning targets by type. The UI's allocation produces a 21-waypoint route per drone vs my manual fetch's 14-waypoint routes. Apples to oranges.
5. **Then: server crashed silently mid-pipeline** with `AttributeError: 'TrajectorySwapOptimizer' object has no attribute '_calculate_route_distance_with_loiter'` — I had defined the helper method inside the wrong class (`PostOptimizer` at line 887, but called it from `TrajectorySwapOptimizer` at line 1920). The pipeline died after the first swap; the UI fell back to a separate two-endpoint flow (`/api/insert_missed_optimize` → `/api/apply_sequence`) which recomputes distance as travel-only.
6. **Final fix**: added a copy of the helper method on `TrajectorySwapOptimizer` (and earlier had one on `CrossingRemovalOptimizer`). Pipeline completes cleanly, distance stays loiter-inclusive.

---

## Local repo state (work machine, May 19 evening)

- Working tree clean (just verified)
- On branch `feat/optimizer-pipeline`
- Synced with `origin/feat/optimizer-pipeline` at `1673d60`
- Server running on port 8893 (you can leave it or kill it)

---

## How to continue at home

### Step 1: Open Terminal, navigate to the project

```bash
cd ~/isr-planner    # or wherever the repo lives on the home machine
```

### Step 2: Make sure you're on the right branch and pull the latest

```bash
git checkout feat/optimizer-pipeline
git pull
```

You should see `Updating ...1673d60` (or "Already up to date" if you've pulled since the push).

### Step 3: Start the planner server

```bash
./run_planner.sh
```

Wait for `Application startup complete`. Leave that terminal window open.

### Step 4: Open the app

In a browser, go to `http://localhost:8893`.

Hard refresh: `Cmd+Shift+R` (Mac) or `Ctrl+Shift+R` (Windows/Linux) — picks up the latest CSS/JS.

### Step 5: Verify the fix is live

1. Import the env file `isr_env_20260519-1150_N1_1.json` (from your Downloads folder, if it's also on the home machine — otherwise any env with loiter enabled will demonstrate)
2. Click **Run Planner**
3. Check the Mission Fuel display — it should match the animation's total (or close to it, within a few km of pause-distance overhead)

If you want to confirm loiter is being counted, paste this in the browser console after a solve:

```javascript
let total = 0;
for (const [did, r] of Object.entries(state.routes || {})) {
  if (!r?.route?.length) continue;
  console.log(`D${did}: distance=${r.distance?.toFixed(2)}, route_len=${r.route.length}`);
  total += r.distance || 0;
}
console.log(`SUM = ${total.toFixed(2)}`);
console.log(`UI displays: ${document.getElementById('stat-mission-fuel')?.textContent}`);
```

The `SUM` and `UI displays` numbers should match (within rounding).

---

## What's NOT done yet (open items for future sessions)

1. **`loiter_fuel` field is not in the API response whitelist.** The server's pipeline now computes it correctly and writes it on `route_data`, but [server/main.py:1490-1496](server/main.py#L1490-L1496) strips it out before sending to the browser. Adding it to the whitelist would let the UI display a travel-vs-loiter breakdown. Not urgent — Mission Fuel display works without it.

2. **Refuel-route loiter_fuel is not persisted as a separate field.** For drones with `refuel_airport` set, `_solve_one_leg` computes loiter internally and folds it into `distance`, but never sets `route_info["loiter_fuel"]`. Currently zero refuel drones in the test env so this hasn't bitten us. Adding `route_info["loiter_fuel"] = actual_loiter` in the refuel branch would close the gap.

3. **Allocator (`server/solver/target_allocator.py`) is loiter-blind.** All five strategies ignore fuel and loiter when deciding which targets go to which drone. The OP solver does correct loiter-aware pruning at solve time, so the allocator's blindness doesn't cause infeasible routes — but it can lead to suboptimal allocations where one drone gets too many loiter-heavy targets. Lower priority.

4. **`apply_sequence` endpoint computes distance as travel-only.** When the UI re-applies a sequence manually (e.g., after edits), the distance shown reflects matrix sum without loiter. Should call the loiter-aware helper too, but lives in a different endpoint not in scope for this fix.

5. **Larger context that's been building this session**: this is the second loiter-related fix in 24 hours (May 19 commit `e36ebbc` fixed animation loiter calc; this commit fixes pipeline loiter preservation). The pattern suggests loiter handling is fragmented across multiple subsystems. Worth considering a refactor where `loiter_fuel` lives on a dedicated `RouteMetrics` dataclass that travels with the route through every stage, making it impossible to lose. Not for this session — flagging for later.

---

## Files changed in `1673d60`

- `server/solver/post_optimizer.py` — 160 insertions, 10 deletions

That's the entire commit. No UI changes, no env changes, no test changes (would be nice to add a regression test that catches the "Swap strips loiter" case, but skipped to keep the commit focused).
