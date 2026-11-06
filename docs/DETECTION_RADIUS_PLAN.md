# Detection Radius Feature — Implementation Plan

> **Purpose**: Allow drones to "detect" or "visit" targets from a distance without flying directly to them. Each drone can have different detection radii for different target types.

> **Date**: 2026-06-08

---

## Overview

### Current Behavior
- A drone must fly to a target's exact position to visit it
- The route includes the target as a waypoint
- Green cross appears when drone reaches the target point

### New Behavior
- Each drone has a **detection radius** per target type (in km)
- A target is "visited" when the drone's trajectory passes **within that radius**
- Drone doesn't need to fly directly over the target
- Default radius: 0 km (current behavior — must fly to exact position)

### Use Case
Some sensors can detect targets from a distance. A drone with a long-range camera might "observe" a target from 5km away, while another drone with short-range sensors needs to get within 1km.

---

## Part 1: UI — Detection Radius Box

### 1.1 Location
Assets Tab, below the Loiter box (or adjacent to it).

### 1.2 HTML Structure

```html
<div id="detection-radius-box" class="config-box">
  <div class="box-header">DETECTION RADIUS (km)</div>
  <table id="detection-radius-table">
    <thead>
      <tr>
        <th>Drone</th>
        <th>Type a</th>
        <th>Type b</th>
        <th>Type c</th>
        <th>Type d</th>
        <th>Type e</th>
      </tr>
    </thead>
    <tbody>
      <!-- Rows generated dynamically for D1-D5 -->
    </tbody>
  </table>
</div>
```

### 1.3 JavaScript Implementation

```javascript
function populateDetectionRadiusBox() {
  const tbody = document.querySelector('#detection-radius-table tbody');
  tbody.innerHTML = '';

  const targetTypes = ['a', 'b', 'c', 'd', 'e'];

  for (let d = 1; d <= 5; d++) {
    const did = String(d);
    const config = state.droneConfigs[did] || {};
    const detectionRadius = config.detection_radius || {};

    const row = document.createElement('tr');
    row.innerHTML = `<td>D${d}</td>`;

    for (const type of targetTypes) {
      const cell = document.createElement('td');
      const input = document.createElement('input');
      input.type = 'number';
      input.min = '0';
      input.step = '0.5';
      input.value = detectionRadius[type] || 0;
      input.id = `detection-d${d}-${type}`;
      input.dataset.drone = did;
      input.dataset.type = type;
      input.style.width = '50px';
      input.addEventListener('change', onDetectionRadiusChange);
      cell.appendChild(input);
      row.appendChild(cell);
    }

    tbody.appendChild(row);
  }
}

function onDetectionRadiusChange(event) {
  const input = event.target;
  const did = input.dataset.drone;
  const targetType = input.dataset.type;
  const value = parseFloat(input.value) || 0;

  // Initialize if needed
  if (!state.droneConfigs[did]) {
    state.droneConfigs[did] = {};
  }
  if (!state.droneConfigs[did].detection_radius) {
    state.droneConfigs[did].detection_radius = {};
  }

  state.droneConfigs[did].detection_radius[targetType] = value;
}
```

### 1.4 Styling

```css
#detection-radius-box {
  margin-top: 10px;
}

#detection-radius-table {
  width: 100%;
  border-collapse: collapse;
}

#detection-radius-table th,
#detection-radius-table td {
  border: 1px solid #444;
  padding: 4px;
  text-align: center;
}

#detection-radius-table input {
  width: 50px;
  text-align: center;
  background: #222;
  color: #fff;
  border: 1px solid #555;
}
```

---

## Part 2: Data Model

### 2.1 Frontend State

```javascript
state.droneConfigs[did] = {
  // Existing fields
  start_airport: "A1_L",
  end_airport: "A1_L",
  fuel_capacity: 50,
  target_access: { a: true, b: true, ... },
  loiter_enabled: true,
  loiter_times: { a: 2, b: 3, ... },
  refuel_stops: [],

  // New field
  detection_radius: {
    a: 3,    // 3 km detection radius for type a
    b: 0,    // 0 = must fly to exact position
    c: 5,
    d: 0,
    e: 0
  }
}
```

### 2.2 Backend Model

**Location**: `server/main.py`

```python
class DroneConfig(BaseModel):
    drone_id: str
    start_airport: str
    end_airport: str
    fuel_capacity: float
    min_runway: str = "S"
    target_access: Dict[str, bool] = {}
    loiter_enabled: bool = False
    loiter_times: Dict[str, float] = {}
    refuel_stops: List[str] = []
    detection_radius: Dict[str, float] = {}  # NEW: {target_type: radius_km}
```

---

## Part 3: Animation — Proximity-Based Visiting

### 3.1 Current Logic

```javascript
// Current: target visited when drone reaches waypoint
if (currentWaypointIndex === targetWaypointIndex) {
  markTargetVisited(target);
}
```

### 3.2 New Logic

```javascript
function checkProximityVisits(droneId, dronePosition) {
  const config = state.droneConfigs[droneId];
  const detectionRadius = config.detection_radius || {};

  for (const target of state.targets) {
    if (state.visitedTargets.includes(target.id)) continue;

    const targetType = target.type || 'a';
    const radius = detectionRadius[targetType] || 0;

    if (radius > 0) {
      const distance = haversineDistance(dronePosition, target);
      if (distance <= radius) {
        markTargetVisitedByDrone(target.id, droneId);
      }
    }
  }
}

// Call during animation frame update
function updateAnimationFrame() {
  // ... existing animation logic ...

  for (const droneId of activeDrones) {
    const position = getDronePosition(droneId);
    checkProximityVisits(droneId, position);
  }
}
```

### 3.3 Visual Feedback (Optional)

Show detection radius circles on map when drone is selected or during animation:

```javascript
function drawDetectionRadius(ctx, drone, config) {
  const detectionRadius = config.detection_radius || {};

  for (const target of state.targets) {
    const targetType = target.type || 'a';
    const radius = detectionRadius[targetType] || 0;

    if (radius > 0) {
      ctx.beginPath();
      ctx.arc(target.screenX, target.screenY, radiusToPixels(radius), 0, 2 * Math.PI);
      ctx.strokeStyle = 'rgba(0, 255, 0, 0.3)';
      ctx.setLineDash([5, 5]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }
}
```

---

## Part 4: Solver Integration (Phase 2 — Optional Optimization)

### 4.1 Current Solver Behavior
- Route includes all allocated targets as waypoints
- Drone flies directly to each target position
- Distance = sum of point-to-point distances

### 4.2 Enhanced Solver Behavior (Future)

If detection radius is significant, the solver could optimize by:
- Not requiring the drone to fly to the exact target position
- Planning routes that "pass by" targets within detection range
- Reducing total distance by allowing tangential passes

**Concept**: "Covering path" instead of "visiting path"

```
Without detection radius:     With 3km detection radius:

    A ──── T1 ──── T2         A ────────────── T2
                                    ↑
                               (passes within 3km of T1)
                               T1 is detected without detour
```

### 4.3 Implementation Approach

This is complex and would require significant solver changes:

1. **Modify distance matrix**: Account for detection radius in reachability
2. **Route planning**: Allow "fly-by" instead of "fly-to" waypoints
3. **Trajectory generation**: Generate paths that enter detection zones

**Recommendation**: Defer to Phase 2. For Phase 1, solve routes normally and use proximity checking during animation.

---

## Part 5: Validation & Edge Cases

### 5.1 Validation Rules

1. **Detection radius ≥ 0**: Negative values not allowed
2. **Detection radius consistency**: If drone cannot access target type (target_access[type] = false), detection_radius[type] is ignored
3. **SAM interaction**: Detection radius does not affect SAM avoidance — drone trajectory must still avoid SAMs

### 5.2 Edge Cases

| Case | Behavior |
|------|----------|
| Detection radius = 0 | Current behavior — must fly to exact position |
| Detection radius > distance to target | Target detected immediately when route passes nearby |
| Multiple drones can detect same target | First drone to enter radius gets credit |
| Drone path touches detection zone edge | Target is detected (inclusive boundary) |
| Target in SAM zone with large detection radius | Drone can detect from outside SAM if radius extends out |

---

## Part 6: Export/Import

### 6.1 JSON Structure

```json
{
  "drone_configs": {
    "1": {
      "start_airport": "A1_L",
      "end_airport": "A1_L",
      "fuel_capacity": 50,
      "detection_radius": {
        "a": 3,
        "b": 0,
        "c": 5,
        "d": 0,
        "e": 0
      }
    }
  }
}
```

### 6.2 Migration

For existing saved missions without `detection_radius`:
```javascript
function migrateDroneConfig(config) {
  if (!config.detection_radius) {
    config.detection_radius = { a: 0, b: 0, c: 0, d: 0, e: 0 };
  }
  return config;
}
```

---

## Part 7: Testing Plan

### 7.1 Manual Testing

- [ ] Detection radius box appears in Assets tab
- [ ] Values save to droneConfigs correctly
- [ ] Default is 0 for all types
- [ ] Animation marks target visited when drone enters radius
- [ ] Works with radius = 0 (current behavior)
- [ ] Works with large radius (10+ km)
- [ ] Export includes detection_radius
- [ ] Import restores detection_radius
- [ ] Drone with no access to type ignores detection_radius for that type

### 7.2 Edge Case Testing

- [ ] Target inside SAM zone with detection radius extending outside
- [ ] Two drones approaching same target — first to enter radius wins
- [ ] Drone trajectory tangent to detection circle (edge touch)

---

## Part 8: Implementation Order

### Phase 1: Basic Implementation (Day 1)

1. Add Detection Radius box UI to Assets tab
2. Store `detection_radius` in droneConfigs state
3. Modify animation to check proximity for visits
4. Update export/import to include detection_radius
5. Test basic functionality

### Phase 2: Visual Feedback (Day 2 — Optional)

1. Draw detection radius circles on map
2. Highlight when drone enters detection zone
3. Show detection radius in target info popup

### Phase 3: Solver Optimization (Future)

1. Modify solver to account for detection radius
2. Allow "fly-by" waypoints instead of "fly-to"
3. Optimize routes for covering path

---

## Appendix A: File Changes Summary

| File | Changes |
|------|---------|
| `webapp/isr.js` | Detection Radius box UI, proximity visit checking |
| `webapp/isr.css` | Detection Radius box styling |
| `server/main.py` | Add `detection_radius` to DroneConfig model |

---

## Appendix B: Haversine Distance Function

If not already available:

```javascript
function haversineDistance(pos1, pos2) {
  const R = 6371; // Earth's radius in km
  const dLat = toRad(pos2.lat - pos1.lat);
  const dLng = toRad(pos2.lng - pos1.lng);
  const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
            Math.cos(toRad(pos1.lat)) * Math.cos(toRad(pos2.lat)) *
            Math.sin(dLng/2) * Math.sin(dLng/2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
  return R * c;
}

function toRad(deg) {
  return deg * Math.PI / 180;
}
```

---

**End of Implementation Plan**
