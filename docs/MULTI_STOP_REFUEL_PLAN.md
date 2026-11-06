# Multi-Stop Refueling with Tanker Support — Implementation Plan

> **Purpose**: Detailed implementation plan for adding multi-stop refueling (up to 4 stops) with mid-air tanker support. This document is designed to be self-contained so it can be handed off to Claude Code for implementation.

> **Date**: 2026-06-08

---

## Overview

### Current State
- Single refuel stop per drone
- Options: NONE, ANY, or specific airport (A1, A2, ...)
- "ANY" triggers greedy airport search (complex)
- Data: `droneConfigs[did].refuel_airport` (single value)

### Target State
- Up to 4 refuel stops per drone
- Options: NONE or specific airport/tanker (no "ANY")
- New tanker type (TK1, TK2, ...) for mid-air refueling
- Virtual drones (legs) treated as first-class drones for allocation/solving
- Data: `droneConfigs[did].refuel_stops` (array)

### Key Design Decisions
1. **No "ANY" option** — user must select specific airports/tankers
2. **Virtual drones at allocation time** — legs are expanded before target allocation
3. **Tankers** — mid-air refuel points, no runway, cannot be drone start/end
4. **Sequential activation** — Stop 2 disabled until Stop 1 selected, etc.

---

## Part 1: Data Model Changes

### 1.1 Frontend State (`webapp/isr.js`)

**Current drone config:**
```javascript
state.droneConfigs[did] = {
  start_airport: "A1_L",
  end_airport: "A1_L",
  refuel_airport: "A2_M",  // Single value: "", "-", or airport ID
  // ... other fields
}
```

**New drone config:**
```javascript
state.droneConfigs[did] = {
  start_airport: "A1_L",
  end_airport: "A1_L",
  refuel_stops: ["A2_M", "TK1", "A3_L"],  // Array of airport/tanker IDs, max 4
  // ... other fields
}
```

**Migration**: On load, convert old `refuel_airport` to new format:
```javascript
if (config.refuel_airport && config.refuel_airport !== "" && config.refuel_airport !== "-") {
  config.refuel_stops = [config.refuel_airport];
} else {
  config.refuel_stops = [];
}
delete config.refuel_airport;
```

### 1.2 Tanker Data Structure

**Add tankers to environment:**
```javascript
state.tankers = [
  { id: "TK1", lat: 37.5, lng: -122.1, name: "Tanker 1" },
  { id: "TK2", lat: 37.8, lng: -121.9, name: "Tanker 2" },
];
```

**Or extend airports with type field (recommended):**
```javascript
state.airports = [
  { id: "A1_L", lat: 37.6, lng: -122.0, runway: "L", type: "airport" },
  { id: "A2_M", lat: 37.7, lng: -121.8, runway: "M", type: "airport" },
  { id: "TK1",  lat: 37.5, lng: -122.1, runway: null, type: "tanker" },
];
```

**Recommendation**: Use single collection with `type` field. Simpler queries, unique IDs guaranteed.

### 1.3 Backend Models (`server/main.py`, `server/solver/`)

**Update DroneConfig model:**
```python
class DroneConfig(BaseModel):
    start_airport: str
    end_airport: str
    refuel_stops: List[str] = []  # New: array of airport/tanker IDs
    # Remove: refuel_airport
```

**Update Environment model:**
```python
class Airport(BaseModel):
    id: str
    lat: float
    lng: float
    runway: Optional[str] = None  # "L", "M", "S", or None for tankers
    type: str = "airport"  # "airport" or "tanker"
```

---

## Part 2: UI Changes — Refuel Box

### 2.1 Location
Asset tab, below the existing Loiter box.

### 2.2 HTML Structure
```html
<div id="refuel-box" class="config-box">
  <div class="box-header">REFUEL STOPS</div>
  <table id="refuel-table">
    <thead>
      <tr>
        <th>Drone</th>
        <th>Stop 1</th>
        <th>Stop 2</th>
        <th>Stop 3</th>
        <th>Stop 4</th>
      </tr>
    </thead>
    <tbody>
      <!-- Rows generated dynamically for D1-D5 -->
    </tbody>
  </table>
</div>
```

### 2.3 Row Generation (JavaScript)
```javascript
function populateRefuelBox() {
  const tbody = document.querySelector('#refuel-table tbody');
  tbody.innerHTML = '';

  for (let d = 1; d <= 5; d++) {
    const did = String(d);
    const config = state.droneConfigs[did] || {};
    const stops = config.refuel_stops || [];
    const droneRunway = config.min_runway || "S";  // Drone's minimum runway requirement

    const row = document.createElement('tr');
    row.innerHTML = `<td>D${d}</td>`;

    for (let s = 0; s < 4; s++) {
      const cell = document.createElement('td');
      const select = document.createElement('select');
      select.id = `refuel-d${d}-s${s}`;
      select.dataset.drone = did;
      select.dataset.stop = s;

      // Determine if this column should be active
      const prevSelected = s === 0 || (stops[s - 1] && stops[s - 1] !== "");
      select.disabled = !prevSelected;

      // Populate options
      populateRefuelDropdown(select, droneRunway, stops[s] || "");

      select.addEventListener('change', onRefuelStopChange);
      cell.appendChild(select);
      row.appendChild(cell);
    }

    tbody.appendChild(row);
  }
}

function populateRefuelDropdown(select, droneRunway, currentValue) {
  select.innerHTML = '';

  // NONE option
  const noneOpt = document.createElement('option');
  noneOpt.value = "";
  noneOpt.textContent = "NONE";
  select.appendChild(noneOpt);

  // Tankers (always valid for refuel)
  const tankers = state.airports.filter(a => a.type === "tanker");
  tankers.forEach(tk => {
    const opt = document.createElement('option');
    opt.value = tk.id;
    opt.textContent = tk.id;
    select.appendChild(opt);
  });

  // Airports (filtered by runway requirement)
  const airports = state.airports.filter(a =>
    a.type === "airport" && meetsRunwayRequirement(a.runway, droneRunway)
  );
  airports.forEach(apt => {
    const opt = document.createElement('option');
    opt.value = apt.id;
    opt.textContent = apt.id;
    select.appendChild(opt);
  });

  select.value = currentValue;
}

function onRefuelStopChange(event) {
  const select = event.target;
  const did = select.dataset.drone;
  const stopIdx = parseInt(select.dataset.stop);
  const value = select.value;

  // Update state
  if (!state.droneConfigs[did].refuel_stops) {
    state.droneConfigs[did].refuel_stops = [];
  }

  if (value === "") {
    // NONE selected — truncate array at this index
    state.droneConfigs[did].refuel_stops =
      state.droneConfigs[did].refuel_stops.slice(0, stopIdx);
  } else {
    // Airport/tanker selected
    state.droneConfigs[did].refuel_stops[stopIdx] = value;
  }

  // Re-render to update enabled/disabled states
  populateRefuelBox();
}

function meetsRunwayRequirement(airportRunway, droneRequirement) {
  const order = { "L": 3, "M": 2, "S": 1 };
  return order[airportRunway] >= order[droneRequirement];
}
```

### 2.4 Styling (CSS)
```css
#refuel-box {
  margin-top: 10px;
}

#refuel-table {
  width: 100%;
  border-collapse: collapse;
}

#refuel-table th, #refuel-table td {
  border: 1px solid #444;
  padding: 4px;
  text-align: center;
}

#refuel-table select {
  width: 80px;
}

#refuel-table select:disabled {
  background-color: #333;
  color: #666;
}
```

---

## Part 3: Tanker Support

### 3.1 Adding Tankers to the Map

**Placement**: Same as airports — click to place, drag to move.

**UI**: Add "Add Tanker" button or mode toggle in the Env tab.

**Rendering** (`drawEnvironment` function):
```javascript
// Draw tankers as orange diamonds
state.airports.filter(a => a.type === "tanker").forEach(tk => {
  ctx.save();
  ctx.translate(tk.screenX, tk.screenY);
  ctx.rotate(Math.PI / 4);  // Rotate square to diamond
  ctx.fillStyle = '#FFA500';  // Orange
  ctx.fillRect(-8, -8, 16, 16);
  ctx.restore();

  // Label
  ctx.fillStyle = '#FFA500';
  ctx.fillText(tk.id, tk.screenX + 12, tk.screenY + 4);
});
```

### 3.2 Tanker ID Generation
```javascript
function generateTankerId() {
  const existing = state.airports.filter(a => a.type === "tanker");
  const nums = existing.map(t => parseInt(t.id.replace("TK", ""))).filter(n => !isNaN(n));
  const next = nums.length > 0 ? Math.max(...nums) + 1 : 1;
  return `TK${next}`;
}
```

### 3.3 Validation Rules

**Drone start airport dropdown**: Exclude tankers
```javascript
const startOptions = state.airports.filter(a =>
  a.type === "airport" && meetsRunwayRequirement(a.runway, droneRunway)
);
```

**Drone end airport dropdown**: Exclude tankers (same filter)

**Refuel stop dropdown**: Include both tankers AND valid airports (already shown in 2.3)

---

## Part 4: Solver Changes — Virtual Drone Expansion

### 4.1 Expand Physical Drones to Virtual Drones

**Location**: `server/solver/solver_bridge.py` — new function

```python
def expand_to_virtual_drones(drone_configs: List[DroneConfig], airports: List[Airport]) -> List[VirtualDrone]:
    """
    Expand physical drones with refuel stops into virtual drones (legs).

    D1 with refuel_stops=["A2_M", "TK1"] becomes:
      D1_L1: start=D1.start, end=A2_M
      D1_L2: start=A2_M, end=TK1
      D1_L3: start=TK1, end=D1.end

    D2 with refuel_stops=[] becomes:
      D2: start=D2.start, end=D2.end (unchanged)
    """
    virtual_drones = []

    for config in drone_configs:
        did = config.drone_id
        stops = config.refuel_stops or []

        if not stops:
            # No refueling — single leg
            virtual_drones.append(VirtualDrone(
                id=did,
                parent_drone=did,
                leg_number=1,
                total_legs=1,
                start_airport=config.start_airport,
                end_airport=config.end_airport,
                fuel_capacity=config.fuel_capacity,
                min_runway=config.min_runway,
                # Copy other relevant fields
            ))
        else:
            # Multiple legs
            all_points = [config.start_airport] + stops + [config.end_airport]
            num_legs = len(all_points) - 1

            for leg in range(num_legs):
                virtual_drones.append(VirtualDrone(
                    id=f"{did}_L{leg + 1}",
                    parent_drone=did,
                    leg_number=leg + 1,
                    total_legs=num_legs,
                    start_airport=all_points[leg],
                    end_airport=all_points[leg + 1],
                    fuel_capacity=config.fuel_capacity,  # Full tank each leg
                    min_runway=config.min_runway,
                    is_virtual=True,
                ))

    return virtual_drones
```

### 4.2 Modify Target Allocator

**Location**: `server/solver/target_allocator.py`

**Change**: Accept virtual drones instead of physical drones.

```python
def allocate_targets(
    targets: List[Target],
    drones: List[VirtualDrone],  # Changed from DroneConfig
    airports: List[Airport],
    distance_matrix: Dict,
    strategy: str = "geographic"
) -> Dict[str, List[str]]:
    """
    Allocate targets to virtual drones (which may be legs of physical drones).

    Returns: { "1_L1": ["T1", "T3"], "1_L2": ["T2", "T4"], "2": ["T5", "T6"] }
    """
    # Existing allocation logic works — each virtual drone has start/end airports
    # Geographic strategy will assign targets based on proximity to leg's airports
    ...
```

**Key insight**: The allocator doesn't need to know about refueling. Each virtual drone looks like a regular drone with start/end airports. Geographic allocation naturally assigns targets to the leg whose airports are closest.

### 4.3 Solve Each Virtual Drone

**Location**: `server/solver/solver_bridge.py`

```python
def solve_with_virtual_drones(
    env: Environment,
    drone_configs: List[DroneConfig],
    strategy: str = "geographic"
) -> Dict:
    """
    Main solving entry point with virtual drone support.
    """
    # Step 1: Expand to virtual drones
    virtual_drones = expand_to_virtual_drones(drone_configs, env.airports)

    # Step 2: Allocate targets to virtual drones
    allocation = allocate_targets(
        targets=env.targets,
        drones=virtual_drones,
        airports=env.airports,
        distance_matrix=env.distance_matrix,
        strategy=strategy
    )

    # Step 3: Solve each virtual drone with Held-Karp
    routes = {}
    for vdrone in virtual_drones:
        assigned_targets = allocation.get(vdrone.id, [])

        route = solve_orienteering(
            start=vdrone.start_airport,
            end=vdrone.end_airport,
            targets=assigned_targets,
            fuel_budget=vdrone.fuel_capacity,
            distance_matrix=env.distance_matrix
        )

        routes[vdrone.id] = {
            "route": route.sequence,
            "distance": route.distance,
            "visited_targets": route.visited,
            "parent_drone": vdrone.parent_drone,
            "leg_number": vdrone.leg_number,
            "total_legs": vdrone.total_legs,
        }

    # Step 4: Run optimizer pipeline on all virtual drones
    if post_optimize:
        routes = apply_optimizer_pipeline(routes, env, virtual_drones)

    # Step 5: Merge virtual drones back to physical drones
    final_routes = merge_virtual_to_physical(routes, drone_configs)

    return final_routes
```

### 4.4 Merge Virtual Drones Back

```python
def merge_virtual_to_physical(
    virtual_routes: Dict[str, Dict],
    drone_configs: List[DroneConfig]
) -> Dict[str, Dict]:
    """
    Merge virtual drone routes back into physical drone routes.

    D1_L1: [A1, T1, T3, A2]
    D1_L2: [A2, T2, A3]
    D1_L3: [A3, T4, A1]

    Becomes D1: [A1, T1, T3, A2, T2, A3, T4, A1]
    With: refuel_indices=[3, 5], leg_distances=[d1, d2, d3]
    """
    physical_routes = {}

    for config in drone_configs:
        did = config.drone_id
        stops = config.refuel_stops or []

        if not stops:
            # No refueling — route is unchanged
            physical_routes[did] = virtual_routes[did]
        else:
            # Merge legs
            num_legs = len(stops) + 1
            merged_route = []
            leg_distances = []
            refuel_indices = []
            visited_targets = []

            for leg in range(1, num_legs + 1):
                vdrone_id = f"{did}_L{leg}"
                vroute = virtual_routes[vdrone_id]

                leg_route = vroute["route"]
                leg_distances.append(vroute["distance"])
                visited_targets.extend(vroute["visited_targets"])

                if leg == 1:
                    merged_route.extend(leg_route)
                else:
                    # Skip first point (it's the refuel airport, already in route)
                    merged_route.extend(leg_route[1:])

                # Mark refuel index (except for last leg)
                if leg < num_legs:
                    refuel_indices.append(len(merged_route) - 1)

            physical_routes[did] = {
                "route": merged_route,
                "distance": sum(leg_distances),
                "visited_targets": visited_targets,
                "refuel_stops": stops,
                "refuel_indices": refuel_indices,
                "leg_distances": leg_distances,
            }

    return physical_routes
```

---

## Part 5: Optimizer Pipeline Changes

### 5.1 Virtual Drone Awareness

The current optimizer pipeline already uses virtual drone splitting for 2-leg refueling. Extend it to N legs.

**Location**: `server/solver/post_optimizer.py`

**Changes to `split_refueling_drones`:**
```python
def split_refueling_drones(routes: Dict, drone_configs: List) -> Dict:
    """
    Split multi-leg drones into virtual drones for optimization.
    Already done if using expand_to_virtual_drones() before solving.
    This function may not be needed if virtual drones are first-class.
    """
    # If routes already keyed by virtual drone ID (e.g., "1_L1"), no split needed
    # Just pass through
    return routes
```

**Key insight**: If we expand to virtual drones BEFORE allocation/solving, the optimizer receives virtual drone routes directly. No additional splitting needed.

### 5.2 Merge After Optimization

Same `merge_virtual_to_physical` function used after solving.

### 5.3 SWAP Optimizer Constraints

SWAP can move targets between any virtual drones, but add constraint:
- A target can only move to a virtual drone whose airports are compatible (runway requirements)
- This is already handled by fuel budget checks

---

## Part 6: API Endpoint Changes

### 6.1 Update `/api/solve` and `/api/solve-with-allocation`

**Location**: `server/main.py`

```python
@app.post("/api/solve-with-allocation")
async def solve_with_allocation(req: SolveRequest):
    # Validate refuel_stops (no tankers as start/end, runway compatibility)
    validate_refuel_stops(req.drone_configs, req.env.airports)

    # Use new solver with virtual drone support
    result = solve_with_virtual_drones(
        env=req.env,
        drone_configs=req.drone_configs,
        strategy=req.allocation_strategy or "geographic"
    )

    return result
```

### 6.2 Validation Function

```python
def validate_refuel_stops(drone_configs: List, airports: List):
    airport_map = {a.id: a for a in airports}

    for config in drone_configs:
        # End airport cannot be tanker
        end_apt = airport_map.get(config.end_airport)
        if end_apt and end_apt.type == "tanker":
            raise HTTPException(400, f"Drone {config.drone_id} end airport cannot be a tanker")

        # Start airport cannot be tanker
        start_apt = airport_map.get(config.start_airport)
        if start_apt and start_apt.type == "tanker":
            raise HTTPException(400, f"Drone {config.drone_id} start airport cannot be a tanker")

        # Refuel stops: check runway compatibility for airports (tankers always OK)
        for stop_id in (config.refuel_stops or []):
            stop = airport_map.get(stop_id)
            if stop and stop.type == "airport":
                if not meets_runway_requirement(stop.runway, config.min_runway):
                    raise HTTPException(400,
                        f"Drone {config.drone_id} cannot refuel at {stop_id}: runway too short")
```

---

## Part 7: Testing Plan

### 7.1 Unit Tests

**File**: `tests/test_multi_refuel.py`

```python
def test_expand_virtual_drones_no_refuel():
    """Drone without refuel stops stays as single drone"""

def test_expand_virtual_drones_one_stop():
    """Drone with 1 stop becomes 2 legs"""

def test_expand_virtual_drones_four_stops():
    """Drone with 4 stops becomes 5 legs"""

def test_allocation_to_virtual_drones():
    """Targets allocated based on leg geography"""

def test_merge_virtual_to_physical():
    """Legs merge correctly with refuel_indices"""

def test_tanker_validation():
    """Tankers rejected as start/end airports"""

def test_runway_validation():
    """Runway requirements enforced for refuel stops"""
```

### 7.2 Integration Tests

```python
def test_solve_with_two_refuel_stops():
    """Full solve with 2 refuel stops produces valid route"""

def test_solve_with_tanker_refuel():
    """Solve with tanker as refuel stop"""

def test_optimizer_with_multi_leg():
    """Optimizer moves targets between legs correctly"""
```

### 7.3 Manual Testing Checklist

- [ ] Create environment with 3 airports (L, M, S) and 2 tankers
- [ ] Configure D1 with 2 refuel stops (airport + tanker)
- [ ] Configure D2 with no refuel stops
- [ ] Solve mission — verify routes are valid
- [ ] Verify optimizer runs without errors
- [ ] Verify animation plays correctly across refuel points
- [ ] Verify tanker shown as orange diamond on map
- [ ] Verify refuel box UI enables/disables correctly
- [ ] Verify runway filtering works in dropdowns

---

## Part 8: Migration & Backwards Compatibility

### 8.1 Saved Missions

Old missions have `refuel_airport` field. Handle during load:

```javascript
function migrateDroneConfig(config) {
  if ('refuel_airport' in config) {
    if (config.refuel_airport && config.refuel_airport !== "" && config.refuel_airport !== "-") {
      config.refuel_stops = [config.refuel_airport];
    } else {
      config.refuel_stops = [];
    }
    delete config.refuel_airport;
  }
  return config;
}
```

### 8.2 API Compatibility

Support both old and new formats during transition:

```python
class DroneConfig(BaseModel):
    refuel_stops: List[str] = []
    refuel_airport: Optional[str] = None  # Deprecated, for backwards compat

    @validator('refuel_stops', pre=True, always=True)
    def migrate_refuel_airport(cls, v, values):
        if v:
            return v
        old = values.get('refuel_airport')
        if old and old not in ("", "-"):
            return [old]
        return []
```

---

## Part 9: Implementation Order

### Phase 1: Data Model (Day 1)
1. Add `refuel_stops` array to frontend state
2. Add `type` field to airports for tanker support
3. Update backend models
4. Add migration logic for old saved missions

### Phase 2: UI — Refuel Box (Day 1-2)
1. Add Refuel Box HTML structure
2. Implement `populateRefuelBox()` function
3. Implement progressive activation logic
4. Add styling

### Phase 3: Tanker Support (Day 2)
1. Add tanker placement UI
2. Implement tanker rendering (orange diamond)
3. Filter tankers from start/end dropdowns
4. Include tankers in refuel dropdowns

### Phase 4: Solver Changes (Day 2-3)
1. Implement `expand_to_virtual_drones()`
2. Modify allocator to accept virtual drones
3. Update solving loop for virtual drones
4. Implement `merge_virtual_to_physical()`

### Phase 5: Optimizer Pipeline (Day 3)
1. Verify optimizer handles N-leg virtual drones
2. Update any 2-leg assumptions
3. Test INSERT/SWAP/NO-CROSS with multi-leg

### Phase 6: Testing & Polish (Day 4)
1. Write unit tests
2. Manual testing checklist
3. Fix bugs
4. Update documentation

---

## Appendix A: File Change Summary

| File | Changes |
|------|---------|
| `webapp/isr.js` | Refuel Box UI, tanker rendering, state migration |
| `webapp/isr.css` | Refuel Box styling |
| `server/main.py` | API validation, model updates |
| `server/solver/solver_bridge.py` | Virtual drone expansion, merge logic |
| `server/solver/target_allocator.py` | Accept virtual drones |
| `server/solver/post_optimizer.py` | Verify N-leg support |
| `tests/test_multi_refuel.py` | New test file |

---

## Appendix B: Key Functions to Implement

### Frontend (isr.js)
- `populateRefuelBox()` — Render refuel grid UI
- `onRefuelStopChange()` — Handle dropdown changes
- `populateRefuelDropdown()` — Populate dropdown options
- `meetsRunwayRequirement()` — Runway compatibility check
- `generateTankerId()` — Create new tanker ID
- `drawTanker()` — Render tanker on map
- `migrateDroneConfig()` — Convert old format to new

### Backend (solver_bridge.py)
- `expand_to_virtual_drones()` — Physical → Virtual drones
- `merge_virtual_to_physical()` — Virtual → Physical routes
- `solve_with_virtual_drones()` — Main solve entry point

### Backend (main.py)
- `validate_refuel_stops()` — Validate tanker/runway rules

---

## Appendix C: Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           FRONTEND                                       │
├─────────────────────────────────────────────────────────────────────────┤
│  Refuel Box UI                                                          │
│  ┌─────────┬─────────┬─────────┬─────────┬─────────┐                   │
│  │ Drone   │ Stop 1  │ Stop 2  │ Stop 3  │ Stop 4  │                   │
│  ├─────────┼─────────┼─────────┼─────────┼─────────┤                   │
│  │ D1      │ A2_M    │ TK1     │ NONE    │ (grey)  │                   │
│  │ D2      │ NONE    │ (grey)  │ (grey)  │ (grey)  │                   │
│  └─────────┴─────────┴─────────┴─────────┴─────────┘                   │
│                              │                                          │
│                              ▼                                          │
│  state.droneConfigs = {                                                │
│    "1": { refuel_stops: ["A2_M", "TK1"], ... },                        │
│    "2": { refuel_stops: [], ... }                                      │
│  }                                                                      │
└─────────────────────────────────────────────────────────────────────────┘
                               │
                               │ POST /api/solve-with-allocation
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           BACKEND                                        │
├─────────────────────────────────────────────────────────────────────────┤
│  1. VALIDATE                                                            │
│     - No tankers as start/end                                           │
│     - Runway compatibility for refuel stops                             │
│                                                                         │
│  2. EXPAND TO VIRTUAL DRONES                                            │
│     D1 → D1_L1 (A1→A2_M), D1_L2 (A2_M→TK1), D1_L3 (TK1→A1)            │
│     D2 → D2 (unchanged)                                                 │
│                                                                         │
│  3. ALLOCATE TARGETS                                                    │
│     Geographic strategy assigns targets to virtual drones               │
│     { "1_L1": [T1,T3], "1_L2": [T2], "1_L3": [T4,T5], "2": [T6,T7] }   │
│                                                                         │
│  4. SOLVE EACH (Held-Karp)                                              │
│     D1_L1: [A1, T1, T3, A2_M]                                          │
│     D1_L2: [A2_M, T2, TK1]                                             │
│     D1_L3: [TK1, T4, T5, A1]                                           │
│     D2:    [A2, T6, T7, A2]                                            │
│                                                                         │
│  5. OPTIMIZE (INSERT → SWAP → NO-CROSS)                                 │
│     Operates on all virtual drones together                             │
│                                                                         │
│  6. MERGE TO PHYSICAL                                                   │
│     D1: [A1, T1, T3, A2_M, T2, TK1, T4, T5, A1]                        │
│         refuel_indices: [3, 5]                                          │
│         leg_distances: [d1, d2, d3]                                     │
│     D2: [A2, T6, T7, A2]                                               │
└─────────────────────────────────────────────────────────────────────────┘
                               │
                               │ Response
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           FRONTEND                                       │
├─────────────────────────────────────────────────────────────────────────┤
│  - Display routes on map                                                │
│  - Animate with refuel stops (tank refills at each stop)                │
│  - Show leg distances in stats                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

**End of Implementation Plan**
