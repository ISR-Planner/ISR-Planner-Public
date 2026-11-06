# Session Summary - May 1, 2026

## Issues Fixed

### 1. Agent "Failed to Fetch" / Hanging
**Problem:** The reasoning agent was timing out when called from the browser, returning "Failed to fetch" errors.

**Root Causes:**
- Agent was using `claude-opus-4-6` model which is very slow
- Distance matrix was being recalculated on every strategy attempt

**Fixes:**
- Changed model from `claude-opus-4-6` to `claude-sonnet-4-20250514` in `reasoning_agent.py`
- Added distance matrix caching in `solver_bridge.py` using environment hash comparison

### 2. Slow Drone Missing Target Checkmarks
**Problem:** During animation, the slower drone (D1, speed=1) was not placing green checkmarks on visited targets, while the fast drone (D2, speed=3) worked correctly.

**Root Cause:** Target visit detection tolerance was speed-dependent:
```javascript
const EPS_KM = Math.min(0.5, 0.15 * droneSpeed);
```
This gave D1 only 150m tolerance vs D2's 450m tolerance.

**Fix:** Changed to fixed 500m tolerance for all drones in `webapp/isr.js`:
```javascript
const EPS_KM = 0.5;
```

### 3. Drone Loitering Before Target
**Problem:** Drone would stop to loiter BEFORE reaching the target, then jump over it when continuing.

**Root Cause:** Loiter injection tolerance was 2.0km, allowing loiter points too far from targets.

**Fix:** Reduced loiter injection tolerance from 2.0km to 0.5km in `webapp/isr.js`:
```javascript
if (bestIdx >= 0 && bestD < 0.5) {  // Was 2.0
```

### 4. Run Planner vs Agent Producing Different Results
**Problem:** Run Planner missed target T14 that the Agent successfully included in its solution.

**Root Cause:** Different default allocation strategies:
- Run Planner: `"efficient"` (priority/distance ratio)
- Agent: `"geographic"` (airport proximity)

**Fix:** Changed Run Planner default from `"efficient"` to `"geographic"` in `webapp/isr.js` to match agent behavior.

## Files Modified

| File | Changes |
|------|---------|
| `server/agents/reasoning_agent.py` | Model change to Sonnet 4 |
| `server/solver/solver_bridge.py` | Distance matrix caching with env hash |
| `webapp/isr.js` | Fixed EPS_KM tolerance, loiter injection tolerance, default allocation strategy |
| `server/agents/mission_orchestration_tools.py` | Minor lat/lng related updates |

## Commit
```
a844c31 fix: Align agent and Run Planner allocation strategy, fix animation bugs
```

## Technical Details

### Distance Matrix Caching
The solver now computes a hash of the environment (airports, targets, SAMs) and reuses the cached distance matrix when the hash matches. This significantly speeds up the agent when trying multiple allocation strategies.

### Allocation Strategies
- `geographic`: Assigns targets to drone whose starting airport is closest
- `efficient`: Maximizes priority/distance ratio (auction-style)
- `greedy`: Assigns highest priority targets to nearest capable drone
- `balanced`: Distributes targets evenly by count
- `exclusive`: Prioritizes targets only one drone can reach

Both Run Planner and Agent now default to `geographic` for consistent behavior.
