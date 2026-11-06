# Session Summary - May 19, 2026

**Branch:** `feat/optimizer-pipeline`
**Commit:** `e36ebbc` - feat: real-time animation stats + loiter fuel fixes

---

## Problems Solved

### 1. Stats 2x Discrepancy (940 vs 461)

**Symptom:** Animation stats showed ~940 fuel consumed while solver reported ~461.

**Root Cause:** Animation calculated loiter fuel using `speedUnitsPerSec` (animation playback speed, default 2) instead of `droneSpeed` (drone config speed, default 1). This doubled the loiter fuel contribution.

**Fix:** Changed loiter calculation in animation setup (line ~10668-10669 in isr.js):
```javascript
// Before (wrong):
const loiterDist = loiterTime * speedUnitsPerSec;

// After (correct):
const droneSpeed = droneCfg.speed || 1;
const loiterDist = loiterTime * droneSpeed;
```

### 2. Stats Not Updating During Animation

**Symptom:** Stats box showed final values immediately, didn't increment as drones flew.

**Root Cause:** For refueling drones, the display used static solver leg distances instead of animation's `distanceTraveled`.

**Fix:**
- Added `refuelDistance` tracking during animation setup (cumulative distance at refuel airport)
- Changed per-drone display to calculate real-time leg1/leg2 progress from `distanceTraveled` vs `refuelDistance`
- Changed mission total to use `animDist` for all drones

### 3. T31 Wrong Drone Attribution

**Symptom:** T31 was on D3's route but got marked as visited by D1 during animation.

**Root Cause:** `getRouteTargetLabels()` checked trajectory proximity (0.2km), not route membership. D1's trajectory passed near T31, so it was incorrectly added to D1's target set.

**Fix:** Removed the `getRouteTargetLabels()` call from animation route check. Now uses direct route membership:
```javascript
for (const wp of segRoute) {
  if (wp && String(wp).startsWith('T')) {
    routeTargetIds.add(wp);
  }
}
```

### 4. Seg JSON Import Broken for N1 Files

**Symptom:** N1 (single-segment) files failed to import as segmented missions.

**Root Cause:** `isSegmentedMissionJson()` used `isSegmentedByFilename()` which checked if segment count > 1. For N1 files, `1 > 1` is false.

**Fix:** Added `hasVersionField` check:
```javascript
function isSegmentedMissionJson(data, filename = "") {
  const hasSegmentsArray = data && Array.isArray(data.segments) && data.segments.length > 0 && data.segments[0].env;
  const hasVersionField = data && data.version;
  const isSegmentedByFile = isSegmentedByFilename(filename);
  return hasSegmentsArray && (hasVersionField || isSegmentedByFile);
}
```

### 5. Swap Optimizer Ignoring Loiter Fuel

**Symptom:** Swap optimizer moved targets without considering loiter fuel, potentially exceeding fuel budgets.

**Fix:** Updated `post_optimizer.py` to calculate loiter fuel for both source (saved) and receiver (added) drones:
```python
# Source saves loiter fuel when target is removed
source_loiter_saved = loiter_time * source_speed

# Receiver pays loiter fuel when target is added
loiter_extra = loiter_time * receiver_speed

# Net savings now includes loiter
net_savings = (remove_delta + source_loiter_saved) - (insertion_cost + loiter_extra)
```

### 6. Loiter Tab Not Grayed Out When Edit Off

**Symptom:** Loiter tab remained editable even when Edit mode was off.

**Fix:** Added `env-edit-gated` class to Loiter tab section in index.html.

---

## Files Changed

| File | Changes |
|------|---------|
| `webapp/isr.js` | Loiter fuel fix, real-time stats, T31 fix, seg JSON import fix, metadata preservation |
| `webapp/index.html` | Loiter tab edit-gated, JS version bump |
| `webapp/isr.css` | Larger edit mode buttons |
| `server/solver/post_optimizer.py` | Swap optimizer loiter fuel calculation |

---

## Key Technical Details

### Animation Stats Architecture

```
updateStatsFromAnimation() - called during animation
├── For each drone:
│   ├── animDist = droneState.distanceTraveled (real-time)
│   ├── refuelDist = droneState.refuelDistance (set during setup)
│   │
│   ├── Non-refueling: show animDist / budget
│   └── Refueling:
│       ├── if animDist < refuelDist: leg1=animDist, leg2=0
│       └── else: leg1=refuelDist, leg2=animDist-refuelDist
│
└── Mission total: sum of all animDist values
```

### Loiter Fuel Calculation

The trajectory's total distance is computed during animation setup:
1. Haversine distance between trajectory points
2. Plus loiter distance at each target: `loiterTime * droneCfg.speed`

This total is fixed. Animation playback speed only affects how fast we traverse the trajectory, not the final values.

---

## Testing Notes

- Test at different animation speeds (1x, 5x, 10x) - final values should be identical
- Test refueling drones - should see leg1 increment, then leg2 after refuel point
- Test T31-like scenarios - targets on one drone's trajectory but another's route should not cross-attribute
- Test N1 seg JSON files - should import correctly
