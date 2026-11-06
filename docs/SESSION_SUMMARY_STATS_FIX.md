# Session Summary: Animation Stats Fix

**Date:** April 15, 2026
**Commit:** 975a089
**Branch:** main

---

## Problem Statement

The mission stats panel showed different values after animation compared to after accepting a solve:

| Metric | After Solve | After Animate |
|--------|-------------|---------------|
| D3 Fuel | 207 / 250 | 283 / 250 (OVER!) |
| Total Fuel | 1051 / 1215 | 1849 / 1215 |

This caused confusion because:
1. D3 appeared to be within budget after solve but over budget after animation
2. The total mission fuel jumped from 1051 to 1849 (798 unit difference)

---

## Root Cause Analysis

### Two Different Distance Calculations

The application had two functions calculating stats:

1. **`updateStatsFromRoutes()`** - Used after accepting a solve
   - Uses `routeInfo.distance` (solver's computed value)
   - Includes SAM-aware shortest path distances + loiter fuel

2. **`updateStatsFromAnimation()`** - Used during/after animation
   - Was using `droneState.distanceTraveled` (trajectory-based)
   - Calculated by walking through trajectory points

### Why They Differed

The solver computes distances using a pre-calculated SAM-aware distance matrix. The animation, however, was calculating distance by summing up Euclidean distances between all trajectory points (which includes detailed SAM boundary-following paths).

```
Solver Distance = Matrix-based shortest paths + loiter fuel
Animation Distance = Sum of all trajectory segment lengths
```

The trajectory includes many intermediate points for smooth SAM avoidance, making the animation distance longer than the solver's distance.

---

## Solution

Modified `updateStatsFromAnimation()` to use `routeInfo.distance` (the solver's value) instead of `droneState.distanceTraveled`:

### Changes Made

**File:** `webapp/isr.js`

1. **Individual drone display** now uses `routeInfo.distance`:
```javascript
const routeDistanceForDisplay = Number(routeInfo.distance || 0);
setText(`stat-d${did}-fuel`, `${Math.round(routeDistanceForDisplay)} / ${Math.round(budget)}`);
```

2. **Mission total** now uses `routeInfo.distance`:
```javascript
const routeDistance = Number(routeInfo.distance || 0);
missionDistanceTraveled += routeDistance;
```

3. **P/F ratio** now uses consistent distance:
```javascript
const pf = routeDistanceForDisplay > 0 ? points / routeDistanceForDisplay : 0;
```

4. **Removed unused variables** (`distanceTraveled`, `droneState` per drone)

---

## Additional Feature: Map View Export/Import

Also implemented in this session:

When exporting an environment JSON, the map view is now saved:
```json
{
  "map_view": {
    "bounds": { "south": 37.70, "north": 37.85, "west": -122.51, "east": -122.36 },
    "center": { "lat": 37.775, "lng": -122.435 },
    "zoom": 13
  }
}
```

When importing, `restoreMapView()` restores the geographic area so users see the same map location.

---

## Testing

After the fix:
- **After Solve:** D3 = 207/250, Total = 1051/1215
- **After Animate:** D3 = 207/250, Total = 1051/1215 (now consistent!)

---

## Files Modified

| File | Changes |
|------|---------|
| `webapp/isr.js` | Fixed `updateStatsFromAnimation()`, added `restoreMapView()`, added map_view to exports |

---

## Commit Details

```
fix: Animation stats now consistent with solver stats

- Animation stats were showing different fuel values than solve stats
- Root cause: animation used trajectory-based distanceTraveled while
  solve stats used routeInfo.distance (solver's computed value)
- Fixed updateStatsFromAnimation() to use routeInfo.distance consistently
- Also added map view export/import (saves center lat/lng, zoom, bounds)
- Added debug warning for distance mismatches in refueling drones

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
```
