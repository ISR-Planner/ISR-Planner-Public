# Detection Radius Trajectory Optimization

## Overview

This document describes the **rubber-band trajectory optimization** algorithm that leverages detection radii to generate shorter flight paths. Instead of flying directly to each target's center, the drone flies through the optimal point within each target's detection circle, minimizing total trajectory distance.

---

## Concept: The Floating Peg Analogy

Imagine each target as a **peg with a hole** that can float anywhere within its detection circle. The trajectory is a **string threaded through these holes**. When you **pull the string taut**, each peg slides to the position within its circle that minimizes total string length.

**Current behavior (without optimization):**
- Drone flies to exact target positions
- Trajectory: A → T1_center → T2_center → T3_center → B

**Optimized behavior (with rubber-band):**
- Drone flies through optimal points within detection circles
- Trajectory: A → T1_edge → T2_interior → T3_edge → B
- Result: Shorter total distance

---

## Key Insight: Waypoint Elimination

If the straight line between two waypoints **already passes through** an intermediate target's detection circle, that target is visited "for free" — no dedicated waypoint is needed.

Example: If line A→C passes through target B's detection circle:
- Original: A → B → C (two segments)
- Optimized: A → C (one segment, B visited en route)

---

## Algorithm: Rubber-Band Relaxation

### Input
- **Sequence**: Ordered list of waypoints from Held-Karp solver
  - `[Airport_start, T1, T2, ..., Tn, Airport_end]`
- **Targets**: Each with position (lat, lng) and detection_radius (km)
- **Fixed anchors**: Airports (radius = 0) and targets with radius = 0

### Output
- **Optimized waypoints**: Position within each detection circle that minimizes total path length

### Algorithm Steps

```
1. Initialize all waypoints at target centers

2. Convert to local coordinates:
   - Pick reference point (e.g., first airport)
   - x = (lng - ref_lng) × cos(ref_lat) × 111.32 km
   - y = (lat - ref_lat) × 110.57 km

3. Iterate until convergence (or max iterations):
   For each waypoint i (skip fixed anchors):
     a. Get neighbors: prev = waypoint[i-1], next = waypoint[i+1]
     b. Find closest point Q on line segment prev→next to target center
     c. Calculate distance d from Q to target center
     d. If d ≤ radius:
          waypoint[i] = Q  (line passes through circle)
        Else:
          waypoint[i] = center + radius × (Q - center) / d
          (point on circle edge closest to the line)

4. Check convergence: if max movement < threshold (e.g., 0.001 km), stop

5. Convert optimized waypoints back to lat/lng
```

### Geometric Details

**Finding closest point Q on line segment prev→next to center C:**

```
D = next - prev                              // direction vector
t = clamp([(C - prev) · D] / [D · D], 0, 1)  // projection parameter
Q = prev + t × D                             // closest point on segment
```

**Distance from center to line:**
```
distance = |Q - C|
```

**Optimal waypoint position:**
```
if distance ≤ radius:
    waypoint = Q                             // on the line, inside circle
else:
    direction = (Q - C) / distance           // unit vector toward line
    waypoint = C + radius × direction        // on circle edge
```

---

## Pipeline Integration

```
┌─────────────────┐
│   Held-Karp     │  → Target sequence (ordered list)
│   + Optimizers  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Rubber-Band    │  → Optimized waypoints (positions within circles)
│  Optimization   │     [NEW COMPONENT]
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Trajectory    │  → SAM-aware flight path
│   Generator     │
└─────────────────┘
```

The rubber-band optimization runs **after** sequence generation and **before** trajectory generation. The trajectory generator receives optimized waypoints instead of target centers.

---

## Implementation Location

### Option A: Backend (Python) — Recommended
- Add function in `server/main.py` or new module `server/rubber_band.py`
- Called after Held-Karp returns sequence, before trajectory generation
- Returns optimized waypoints to frontend

### Option B: Frontend (JavaScript)
- Add function in `webapp/isr.js` or new file `webapp/rubber_band.js`
- Called after receiving sequence from backend
- Modifies waypoints before drawing/animation

### Recommended: Backend
- Keeps optimization logic with solver
- Single source of truth for waypoints
- Frontend receives final optimized trajectory

---

## Data Structures

### Input Structure
```python
sequence = ["A1", "T1", "T12", "T8", "T2", "T14", "A1"]  # from solver

targets = {
    "T1":  {"lat": 32.125, "lng": -90.229, "type": "a"},
    "T12": {"lat": 32.158, "lng": -90.342, "type": "d"},
    # ...
}

detection_radius = {
    "a": 2,   # km
    "b": 3,
    "c": 5,
    "d": 0,   # must fly to exact position
    "e": 6
}

airports = {
    "A1": {"lat": 32.171, "lng": -90.140}
}
```

### Output Structure
```python
optimized_waypoints = [
    {"id": "A1",  "lat": 32.171, "lng": -90.140, "is_fixed": True},
    {"id": "T1",  "lat": 32.127, "lng": -90.225, "is_fixed": False},  # shifted
    {"id": "T12", "lat": 32.158, "lng": -90.342, "is_fixed": True},   # radius=0
    # ...
]
```

---

## Python Implementation

```python
import math
from typing import List, Dict, Tuple

def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two points in kilometers."""
    R = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def to_local(lat: float, lng: float, ref_lat: float, ref_lng: float) -> Tuple[float, float]:
    """Convert lat/lng to local x,y coordinates (km) relative to reference."""
    x_scale = math.cos(math.radians(ref_lat)) * 111.32
    y_scale = 110.57
    x = (lng - ref_lng) * x_scale
    y = (lat - ref_lat) * y_scale
    return x, y

def to_latlng(x: float, y: float, ref_lat: float, ref_lng: float) -> Tuple[float, float]:
    """Convert local x,y (km) back to lat/lng."""
    x_scale = math.cos(math.radians(ref_lat)) * 111.32
    y_scale = 110.57
    lat = ref_lat + y / y_scale
    lng = ref_lng + x / x_scale
    return lat, lng

def closest_point_on_segment(px: float, py: float,
                              ax: float, ay: float,
                              bx: float, by: float) -> Tuple[float, float]:
    """Find closest point on segment AB to point P."""
    dx, dy = bx - ax, by - ay
    len_sq = dx*dx + dy*dy
    if len_sq < 1e-10:
        return ax, ay
    t = max(0, min(1, ((px - ax)*dx + (py - ay)*dy) / len_sq))
    return ax + t*dx, ay + t*dy

def rubber_band_optimize(
    sequence: List[str],
    positions: Dict[str, Tuple[float, float]],  # id -> (lat, lng)
    radii: Dict[str, float],                     # id -> radius in km
    max_iterations: int = 20,
    convergence_threshold: float = 0.001
) -> List[Dict]:
    """
    Optimize waypoint positions using rubber-band algorithm.

    Args:
        sequence: Ordered list of waypoint IDs
        positions: Dict mapping ID to (lat, lng)
        radii: Dict mapping ID to detection radius in km (0 = fixed)
        max_iterations: Maximum optimization iterations
        convergence_threshold: Stop when max movement < this (km)

    Returns:
        List of optimized waypoints with id, lat, lng, is_fixed
    """
    if len(sequence) < 2:
        return [{"id": s, "lat": positions[s][0], "lng": positions[s][1],
                 "is_fixed": True} for s in sequence]

    # Reference point for local coordinates
    ref_lat, ref_lng = positions[sequence[0]]

    # Initialize waypoints
    waypoints = []
    for sid in sequence:
        lat, lng = positions[sid]
        x, y = to_local(lat, lng, ref_lat, ref_lng)
        radius = radii.get(sid, 0)
        waypoints.append({
            "id": sid,
            "x": x,
            "y": y,
            "center_x": x,
            "center_y": y,
            "radius": radius,
            "is_fixed": radius == 0
        })

    # Iterate until convergence
    for iteration in range(max_iterations):
        max_move = 0

        for i in range(1, len(waypoints) - 1):
            wp = waypoints[i]
            if wp["is_fixed"]:
                continue

            prev = waypoints[i - 1]
            next_wp = waypoints[i + 1]

            # Find closest point on line prev->next to this target's center
            qx, qy = closest_point_on_segment(
                wp["center_x"], wp["center_y"],
                prev["x"], prev["y"],
                next_wp["x"], next_wp["y"]
            )

            # Distance from center to closest point
            dx = qx - wp["center_x"]
            dy = qy - wp["center_y"]
            dist = math.sqrt(dx*dx + dy*dy)

            # Determine new position
            if dist <= wp["radius"]:
                # Line passes through circle - use point on line
                new_x, new_y = qx, qy
            else:
                # Move to circle edge toward the line
                new_x = wp["center_x"] + wp["radius"] * dx / dist
                new_y = wp["center_y"] + wp["radius"] * dy / dist

            # Track movement
            move = math.sqrt((new_x - wp["x"])**2 + (new_y - wp["y"])**2)
            max_move = max(max_move, move)

            wp["x"] = new_x
            wp["y"] = new_y

        # Check convergence
        if max_move < convergence_threshold:
            break

    # Convert back to lat/lng
    result = []
    for wp in waypoints:
        lat, lng = to_latlng(wp["x"], wp["y"], ref_lat, ref_lng)
        result.append({
            "id": wp["id"],
            "lat": lat,
            "lng": lng,
            "is_fixed": wp["is_fixed"]
        })

    return result

def calculate_path_distance(waypoints: List[Dict]) -> float:
    """Calculate total path distance in km."""
    total = 0
    for i in range(len(waypoints) - 1):
        total += haversine_km(
            waypoints[i]["lat"], waypoints[i]["lng"],
            waypoints[i+1]["lat"], waypoints[i+1]["lng"]
        )
    return total
```

---

## JavaScript Implementation

```javascript
function haversineKm(lat1, lng1, lat2, lng2) {
    const R = 6371.0088;
    const phi1 = lat1 * Math.PI / 180;
    const phi2 = lat2 * Math.PI / 180;
    const dphi = (lat2 - lat1) * Math.PI / 180;
    const dlam = (lng2 - lng1) * Math.PI / 180;
    const a = Math.sin(dphi/2)**2 + Math.cos(phi1)*Math.cos(phi2)*Math.sin(dlam/2)**2;
    return 2 * R * Math.asin(Math.sqrt(a));
}

function toLocal(lat, lng, refLat, refLng) {
    const xScale = Math.cos(refLat * Math.PI / 180) * 111.32;
    const yScale = 110.57;
    return {
        x: (lng - refLng) * xScale,
        y: (lat - refLat) * yScale
    };
}

function toLatLng(x, y, refLat, refLng) {
    const xScale = Math.cos(refLat * Math.PI / 180) * 111.32;
    const yScale = 110.57;
    return {
        lat: refLat + y / yScale,
        lng: refLng + x / xScale
    };
}

function closestPointOnSegment(px, py, ax, ay, bx, by) {
    const dx = bx - ax, dy = by - ay;
    const lenSq = dx*dx + dy*dy;
    if (lenSq < 1e-10) return { x: ax, y: ay };
    const t = Math.max(0, Math.min(1, ((px-ax)*dx + (py-ay)*dy) / lenSq));
    return { x: ax + t*dx, y: ay + t*dy };
}

function rubberBandOptimize(sequence, positions, radii, maxIter = 20) {
    if (sequence.length < 2) {
        return sequence.map(id => ({
            id, lat: positions[id].lat, lng: positions[id].lng, isFixed: true
        }));
    }

    const refLat = positions[sequence[0]].lat;
    const refLng = positions[sequence[0]].lng;

    // Initialize waypoints
    const waypoints = sequence.map(id => {
        const pos = positions[id];
        const local = toLocal(pos.lat, pos.lng, refLat, refLng);
        const radius = radii[id] || 0;
        return {
            id,
            x: local.x,
            y: local.y,
            centerX: local.x,
            centerY: local.y,
            radius,
            isFixed: radius === 0
        };
    });

    // Iterate
    for (let iter = 0; iter < maxIter; iter++) {
        let maxMove = 0;

        for (let i = 1; i < waypoints.length - 1; i++) {
            const wp = waypoints[i];
            if (wp.isFixed) continue;

            const prev = waypoints[i - 1];
            const next = waypoints[i + 1];

            const q = closestPointOnSegment(
                wp.centerX, wp.centerY,
                prev.x, prev.y,
                next.x, next.y
            );

            const dx = q.x - wp.centerX;
            const dy = q.y - wp.centerY;
            const dist = Math.sqrt(dx*dx + dy*dy);

            let newX, newY;
            if (dist <= wp.radius) {
                newX = q.x;
                newY = q.y;
            } else {
                newX = wp.centerX + wp.radius * dx / dist;
                newY = wp.centerY + wp.radius * dy / dist;
            }

            const move = Math.sqrt((newX - wp.x)**2 + (newY - wp.y)**2);
            maxMove = Math.max(maxMove, move);

            wp.x = newX;
            wp.y = newY;
        }

        if (maxMove < 0.001) break;
    }

    // Convert back
    return waypoints.map(wp => {
        const ll = toLatLng(wp.x, wp.y, refLat, refLng);
        return { id: wp.id, lat: ll.lat, lng: ll.lng, isFixed: wp.isFixed };
    });
}
```

---

## Expected Results

Based on the test scenario (from session discussion):

| Metric | Original | Optimized | Savings |
|--------|----------|-----------|---------|
| Leg 1  | 115.6 km | 93.5 km   | 22.1 km (19%) |
| Leg 2  | 68.1 km  | 51.8 km   | 16.3 km (24%) |
| **Total** | **183.7 km** | **145.3 km** | **38.4 km (21%)** |

Larger detection radii yield greater savings. Targets with radius = 0 remain fixed anchors.

---

## Edge Cases

1. **Radius = 0**: Target is a fixed anchor; waypoint stays at center
2. **Overlapping circles**: Algorithm handles naturally; each optimizes independently
3. **Large radius**: Waypoint may end up far from center if geometry favors it
4. **Collinear targets**: Optimal path may pass through multiple circles in a straight line
5. **Single target**: No optimization needed; return as-is

---

## Future Enhancements

1. **Waypoint elimination**: If line prev→next passes through a circle, consider removing that waypoint entirely (target visited "for free")

2. **SAM-aware rubber band**: Constrain optimized waypoints to avoid SAM zones

3. **Sequence re-evaluation**: After rubber-band optimization, the original Held-Karp sequence may no longer be optimal; consider re-running sequence optimization with detection-aware distance matrix. *Partly done (2026-09-24):* the fuel the shorter path saves is used — unassigned targets are inserted where they add the least flown distance while every leg fits its tank (`server/solver/detection_radius.py`, step (b)). Allocation and ordering are still chosen on centre-to-centre distances; re-ordering with a detection-aware matrix is still open.

4. **Fuel impact**: Shorter trajectory = less fuel consumed; update fuel calculations accordingly. *Done (2026-09-24):* for drones with a radius, route distance, per-hop and per-leg distances, feasibility and the animation frame come from the flown path (`server/solver/detection_radius.py` step (a), shared `server/solver/route_evaluation.py`). Applies to Run Planner, the agent (solve and ordered plans) and route edits (sequence bar, agent route tools).

---

## Visualization

The `trajectory_visualization.html` file (in Downloads) demonstrates:
- Red line: Original trajectory through target centers
- Green line: Optimized trajectory through detection circles
- Purple circles: Detection radii
- Green dots: Optimized waypoint positions

---

## References

- Traveling Salesman Problem with Neighborhoods (TSPN)
- Rubber-band path optimization
- Haversine formula for geographic distances
