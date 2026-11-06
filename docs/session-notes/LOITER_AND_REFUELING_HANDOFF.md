# Loiter & Refueling Feature Handoff

**Author:** Kamal Ali (with Claude Code)
**Date:** 2026-04-08 (updated)
**For:** Mr. Mahlangu

---

## What Was Done

### 1. Two-Leg Refueling System

Drones can now refuel at an airport mid-route, splitting their mission into two legs.

**How it works:**
- In the Env tab Config section, each drone has a **Refuel** dropdown
- Set to a specific airport (e.g., A4) or "-" (Any — solver picks the best airport)
- The solver splits the route: Leg 1 (start → targets → refuel airport) and Leg 2 (refuel airport → targets → end)
- Each leg must fit within the drone's fuel budget (tank capacity)
- Fuel budget = tank size per leg, NOT total fuel for both legs
- Total fuel pumped = initial tank + fuel consumed in Leg 1 (refueling tops up the tank)

**Key files:**
- `server/solver/solver_bridge.py` — `_solve_two_leg_refuel()` and `_solve_one_leg()` functions
- The solver tries all possible target splits across legs (exhaustive search, capped at N=8 targets per drone)
- For "Any" mode: greedy scan finds the best airport, then exhaustive split on the winner

**Stat box display:**
- Refueling drones show: `⛽leg1+leg2 / budget` (e.g., `⛽131+113 / 281`)
- Budget = tank capacity + leg1 fuel (since refueling adds back what leg 1 consumed)
- Non-refueling drones show: `fuel / budget` as before

**Known issues / areas for improvement:**
- When N > 8 targets per drone, exhaustive split falls back to greedy (may be suboptimal)
- The No Cross optimizer can reorder targets across the refuel boundary; if a leg exceeds budget after reordering, the change is rejected for that drone (warning shown in debug output)
- Swap optimizer now accounts for loiter fuel (May 2026), but Insert optimizer still skips refueling drones
- The reasoning agent has a `solve_with_leg_split` tool for manual leg split control, but it's not reliable for autonomous optimization

### 2. Loiter System (UI Only — Solver Not Yet Integrated)

Drones can now be configured to loiter (stay) at targets for a specified time.

**UI components added:**

#### Env Tab — New Config Columns
- **Spd** (Speed): Number input, default 1. Speed 2 = twice as fast. Speed affects time but NOT travel fuel (all drones burn the same fuel for A→B regardless of speed)
- **Ltr** (Loiter): Checkbox to enable loitering capability for the drone

#### Loiter Tab (New Tab)
- Appears next to the Env tab
- Shows a matrix: rows = drones (D1-D5), columns = target types (A-E) with colored dots matching the canvas legend
- Each cell is a loiter time input (default 0 = no loitering, just visit)
- Only drones with Ltr checked AND enabled are active; others are greyed out
- Only target types the drone can access (from Config tab) are enabled; others are greyed out

**Data model additions to `state.droneConfigs`:**
```javascript
{
  speed: 1,              // default 1, affects time not travel fuel
  loiter_enabled: false,  // must be checked to enable loitering
  loiter_times: {         // time units per target type
    a: 0, b: 0, c: 0, d: 0, e: 0
  }
}
```

**Fuel cost of loitering:**
- Loiter fuel = loiter_time × speed
- A speed-1 drone loitering 5 time units burns 5 fuel
- A speed-2 drone loitering 5 time units burns 10 fuel
- This needs to be integrated into the solver's fuel budget calculation

**Status update (May 19, 2026):**

The following loiter features are now complete:

1. **Solver integration**: The solver accounts for loiter fuel. Total fuel = travel distance + sum of (loiter_time[target_type] × speed) for each visited target.

2. **Animation**: Loiter is injected into the trajectory as extra distance at each loiter target. The drone "loiters" by traveling this extra distance (fuel burns correctly). Visual pause/circling not implemented.

3. **Stat box**: Real-time stats during animation. Fuel numbers increment as drones fly. For refueling drones, shows leg1+leg2 progress. Values are independent of animation playback speed.

4. **Swap optimizer**: Now accounts for loiter fuel when swapping targets between drones.

5. **Export/Import**: The `speed`, `loiter_enabled`, and `loiter_times` fields persist through export/import.

**Remaining work:**
- Visual loiter animation (drone circling at target) — currently just burns fuel without visual effect

### 3. Agent Memory System

The reasoning agent now loads persistent memories into its context before responding.

**How it works:**
- Memories stored in `server/agents/agent_memory.json` (categories: instruction, correction, mission_command)
- `get_memory_for_prompt()` formats them for injection into the agent's system prompt
- Mission commands are auto-stored when the user sends a mission-related message
- Questions (starting with "what", "how", etc.) are not stored as commands
- Both the reasoning agent and v4 strategist agent load memories

**Key files:**
- `server/agents/agent_memory.py` — storage and formatting
- `server/agents/reasoning_agent.py` — `_store_mission_command()`, `_is_mission_command()`

### 4. Agent Tools

The reasoning agent has these tools:
- `get_targets()`, `get_airports()`, `get_drones()`, `get_sams()` — read environment
- `get_distance(from, to)` — SAM-aware distance between waypoints
- `solve_full_mission(strategy, start_with, only_targets)` — full solver pipeline
- `solve_with_leg_split(drone_id, leg1_targets, leg2_targets)` — explicit refueling leg control
- `compare_routes(routes)` — compare multiple route options

The agent is good at understanding commands and calling the solver, but unreliable at spatial/geometric reasoning (detecting crossings, optimizing leg splits manually).

### 5. Frontend Changes

- Agent solutions set mode to `DRAFT_READY` → user clicks Accept → `READY_TO_ANIMATE` → Animate works
- Refueling metadata preserved through all optimizer paths (`snapshotRefuelMeta` / `restoreRefuelMeta`)
- `updateStatsFromAnimation()` shows refueling format during animation
- No Cross optimizer recalculates per-leg fuel after reordering and rejects changes that violate budget

---

## Key Files Reference

| File | What it does |
|------|-------------|
| `webapp/index.html` | Tab structure, config table, loiter tab HTML |
| `webapp/isr.js` | All frontend logic — state management, UI, animation |
| `webapp/isr.css` | Styling including config grid and loiter table |
| `server/main.py` | API endpoints, validation, optimizer wrappers |
| `server/solver/solver_bridge.py` | Core solver — `_solve_two_leg_refuel`, `_solve_one_leg`, allocation |
| `server/agents/reasoning_agent.py` | Reasoning agent tools and prompt |
| `server/agents/agent_memory.py` | Agent memory persistence |
| `server/solver/post_optimizer.py` | No Cross, Swap, Insert optimizers |

---

## How to Run

```bash
./run_planner.sh
# Server at http://localhost:8893
```

## How to Test Refueling

1. Load an environment with multiple airports
2. In Env tab, set a drone's Refuel dropdown to an airport or "-" (Any)
3. Run Planner — observe the route splits at the refuel airport
4. Check stat box shows `⛽leg1+leg2 / budget` format
5. Try No Cross — should fix crossings without violating per-leg fuel

## How to Test Loiter (Solver Integrated)

1. In Env tab, check the "Ltr" checkbox for a drone, set Speed (e.g., 3)
2. Switch to Loiter tab — that drone's row should be active
3. Set loiter times for target types (e.g., 10 for D, 10 for E)
4. Run Planner — solver accounts for loiter fuel (loiter_time × speed per target)
5. Accept, Animate — drone pauses (hovers) at loiter targets
6. Faster drones animate faster along their trajectory

**Loiter fuel model:**
- Fuel cost = loiter_time × speed per visited target
- Solver runs with full budget, then checks travel + loiter feasibility
- If over budget, drops lowest-priority loiter targets and re-solves
- Each refueling leg is checked independently

**Animation:**
- Drone hovers (stays still) at loiter targets for loiter_time seconds
- Duplicate trajectory points injected at target positions with distance gap
- Loiter pause is absolute time, independent of drone speed
- Per-drone speed: faster drones animate faster (distanceTraveled = missionDistance × speed)

---

## Update: 2026-04-08 — Solver Loiter Integration

### What Was Done

1. **Loiter fuel in solver** — solver runs with full budget, visits targets optimally, then checks if travel + loiter fits. Iteratively drops lowest-priority targets if over budget.

2. **Animation loiter pause** — drones hover at loiter targets. Implemented by inserting duplicate trajectory points with loiter distance gaps.

3. **Per-drone speed in animation** — `distanceTraveled = missionDistance × droneSpeed`. Faster drones complete routes sooner.

4. **Target type colors** — updated for better contrast: blue(A), green(B), amber(C), red(D), purple(E). Consistent across canvas, legend, and Loiter tab.

5. **Reset fix** — Reset now shows ALL targets (including skipped/unvisited), not just route targets.

6. **No Cross + refueling** — crossing removal allowed for refueling drones. After optimization, per-leg fuel is recalculated. Changes rejected if a leg exceeds budget.

### Known Issues / TODO for Mr. Mahlangu

1. **Insert Missed optimizer + refueling drones**: The `_exclude_refuel_drones` function removes refueling drones' routes before the optimizer runs. This means:
   - The optimizer doesn't see targets visited by refueling drones → may insert duplicates
   - The optimizer can't insert missed targets into refueling drones
   - Fix: pass full solution to optimizer, mark refueling drones as read-only (visible but not modifiable)

2. **Swap optimizer + refueling**: Same exclusion issue. Swapping targets between a refueling and non-refueling drone is not supported.

3. **Loiter-aware Insert Missed**: The insert optimizer needs to add `loiter_time × speed` to the insertion cost check. Code was written but reverted — needs clean implementation.

4. **Target allocation not loiter-aware**: The geographic allocator doesn't consider loiter costs. A drone with high loiter costs may get too many targets and drop them. Fix: factor loiter fuel into allocation decisions.

5. **Pre-segmented JSON replay**: Importing a segmented mission file (N5+) discards the pre-solved routes. The import creates empty segments expecting re-solve. Fix: load existing routes from the JSON into segments and go directly to READY_TO_ANIMATE.

6. **Exhaustive split cap**: `MAX_EXHAUSTIVE = 12`. Drones with > 12 targets fall back to greedy leg split which may be suboptimal. Consider increasing or using a smarter heuristic.

### Architecture Notes

**Loiter data flow:**
```
UI (Loiter tab) → state.droneConfigs[did].loiter_times
                → sent to server as drone_configs
                → solver_bridge.py _solve_one_leg() uses loiter_cost per target
                → route_info includes loiter_fuel and speed
                → frontend animation injects loiter pauses into trajectory
```

**Key solver change (solver_bridge.py `_solve_one_leg`):**
- Solver runs with FULL fuel_budget (no upfront deduction)
- After solving, calculates actual_loiter = sum(loiter_cost for visited targets)
- If travel + actual_loiter > fuel_budget: drops lowest-priority loiter target, re-solves
- Returns total distance = travel + actual_loiter

---

## Update: 2026-04-09 — Insert Optimizer Rewrite & Undo

### Insert Missed Optimizer (Rewritten)
The Insert optimizer was rewritten from scratch in `server/main.py` (endpoint `/api/insert_missed_optimize`). Key changes:

1. **No drone exclusions** — all drones visible, visited targets collected from ALL routes
2. **Per-leg fuel checks** — for refueling drones, determines which leg the insertion falls into and checks that leg's remaining fuel
3. **Loiter-aware** — adds `loiter_time × speed` to insertion cost
4. **Updates per-leg distances** — `leg1_distance`/`leg2_distance` updated after insertion so stat box stays correct
5. **No duplicates** — visited set built from all drones before any insertion

### Optimizer Undo (Z key)
- Snapshot of `state.routes` and `state.sequences` saved before each optimizer
- Press **Z** to revert to pre-optimizer state
- One level of undo
- Works for Insert, Swap, and No Cross

### Swap Optimizer
Still uses the old `_exclude_refuel_drones` approach. Known issues:
- Refueling drones excluded → their targets invisible → potential duplicates
- Does not check per-leg fuel for refueling drones
- TODO: Rewrite similar to Insert (build visited set from all drones, per-leg fuel checks)

### Apply Sequence
The `/api/apply_sequence` endpoint now:
- Accepts `drone_config` in request
- Calculates per-leg distances for refueling drones
- Adds loiter fuel to total distance
- Returns `refuel_airport`, `leg1_distance`, `leg2_distance`, `loiter_fuel`, `speed`

### Reset Fix
Both reset code paths now show ALL targets (including unvisited) instead of filtering to only route targets.
