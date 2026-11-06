# ONE Fuel Value — Canonical Names Spec

Branch: `refactor/one-fuel-value`. North-star refactor (kill redundant fuel
names). Re-applies the orphaned commit `4c66ad3` (lost — not on `main`) by hand,
reconciled with this week's `fuel_capacity`-based stat box.

---

## The model (one concept → one name)

"How much gas" was fractured: `fuel_budget`, `fuel_capacity`, `full_tank`,
`tank`, `leg2_budget`, `remainingFuel`. Collapse to:

- **`fuel_budget` = THE TANK.** Immutable per drone. Full at the start of EVERY
  leg (a refuel tops up to it). The stats-box denominator. The ONLY stored fuel
  field on a drone config.
- **`start_fuel` = TRANSIENT reduced leg-1 fuel.** Rides the SOLVE REQUEST only
  (a dynamic replan after a cut has less than a full tank). NEVER stored on the
  config, NEVER overwrites the tank. Absent on a fresh solve (→ leg 1 = full tank).
- **`fuel_capacity` = DELETED.** It existed ONLY as an immutable 2nd copy of the
  tank, because the cut used to overwrite `fuel_budget` with the reduced remaining.
  Once the reduced value moves to the transient `start_fuel`, `fuel_capacity` has
  no reason to exist. (Legacy fallback: when loading an OLD env that has
  `fuel_capacity` but the tank is missing, read it AS the tank — read-only compat.)

## Leg fuel rule

- Leg 1 budget = `start_fuel ?? fuel_budget` (reduced on replan, else full tank).
- Every post-refuel leg budget = `fuel_budget` (full tank).
- A cut is fuel-neutral (§11). Legs = refuels + 1.

## The key move

DELETE a variable, don't rename. The fix is removing the STORED intermediate
(`fuel_capacity`) that forced the duplicate — not picking one name over another.

---

## Change surface (from the 4c66ad3 blueprint + current-main reconcile)

### Backend (the contract — do FIRST)
- `solver_bridge.py` `_solve_two_leg_refuel`: leg2_budget fallback reads
  `fuel_budget` not `fuel_capacity`.
- `solver_bridge.py` `solve_mission` + `solve_mission_with_allocation`:
  `leg1_budget = cfg.get("start_fuel", fuel_budget)`; `leg2_budget = fuel_budget`;
  then `fuel_budget = leg1_budget` so downstream single-leg/feasibility use the
  start fuel. Pass `leg2_budget=leg2_budget` to the 4 `_solve_two_leg_refuel`
  call sites.
- `main.py` `verify_solution_basic`: leg-1 vs `start_fuel`, leg-2 vs
  `full_tank = fuel_budget`.
- `post_optimizer.py` `_get_remaining_fuel` (~4051): read `fuel_budget` (legacy
  `fuel_capacity` fallback for old data).
- `mission_ledger.py` (~641): read `fuel_budget` (legacy fallback).

### Frontend (`isr.js`) — reconcile with THIS WEEK's stat box
- Replan paths (`acceptEditsWithCheckpoint` ~2420, both `buildCheckpointEnv`
  ~9560/9591): STOP writing the reduced remaining into `fuel_budget`/
  `fuel_capacity`; write transient `start_fuel` instead. `fuel_budget` stays = tank.
- This week's stat box (~7615/7706, ~2420): the tank denominator must read
  `fuel_budget` (NOT `fuel_capacity`). This is the deliberate reconcile — this
  week's code read `fuel_capacity` as the tank; now there's just `fuel_budget`.
- This week's Assets fuel-edit handler (~7047-7051): currently sets BOTH
  `fuel_budget` and `fuel_capacity`; now set ONLY `fuel_budget` (the tank).
- Config load (~6959/6974) + `readDroneConfigsFromUI` (~6855): stop emitting
  `fuel_capacity`; on load, an old env's `fuel_capacity` falls back AS the tank.
- Reset/import: clear `start_fuel` so a fresh leg-1 uses the full tank.
- `index.html`: cache-bust bump.

### Tests
- `test_refuel_floor_invariant.py` / `test_start_fuel_contract`: lock leg-2 =
  full tank independent of a reduced leg-1 `start_fuel`.

---

## Sequencing
1. **Backend** (solver contract + verify + optimizer + ledger) → `pytest` green.
2. **Frontend** (replan→start_fuel, stat box→fuel_budget, drop fuel_capacity
   emit, clear start_fuel on reset/import) → `node --check`.
3. **User browser test:** (a) fresh mission shows `/ N×budget`; (b) post-cut
   replan still gives a refueled leg-2 a FULL tank; (c) no-op edit resume works;
   (d) this week's ⛽leg1+leg2 box still correct.

Commit between backend and frontend.

## Risk
Touches the solver fuel contract + the stat box we just stabilized. Mitigate:
backend first with tests, then frontend, then browser-verify the full
solve/cut/refuel/replay matrix before merge.
