# Multi-Stop Refuel via Virtual Drones — Architecture Spec

Branch: `feat/multi-stop-refuel`. Supersedes the solver design in
[MULTI_STOP_REFUEL_PLAN.md](../MULTI_STOP_REFUEL_PLAN.md) Part 4 (this is simpler
and user-agreed 2026-06-09). Data-model / UI / tanker parts of that plan still apply.

---

## The core idea (user's model)

**A refuel stop is just a fixed airport that splits a drone into legs; each leg is
a first-class virtual drone.** No "ANY", no greedy, no special two-leg solver. The
existing pipeline — **allocate → Held-Karp → optimize → merge** — handles it
unchanged, with more drones.

```
D1, refuel_stops=[A2, A3]   →   D1_L1: start→A2
                                D1_L2: A2→A3
                                D1_L3: A3→end
5 drones (2 real + 3 virtual) ≡ 5 real drones. Linear. Held-Karp optimal per leg.
```

The allocator is ALREADY drone-ID-keyed with per-drone start/end/fuel
([target_allocator.py:716](../../server/solver/target_allocator.py#L716)), so
virtual drones (`"1_L1"`, `"1_L2"`, …) slot in as distinct drones with no allocator
change in principle.

## What dies (after the new path is proven)

- The **"ANY" greedy airport scan** (`refuel_airport == "-"` blocks in
  `solve_mission` ~1617 and `solve_mission_with_allocation` ~mirror).
- **`_solve_two_leg_refuel`** (the whole function ~785) — a refuel is now two
  virtual drones, not a special two-leg solve.
- The exhaustive target-split logic inside it.

What STAYS: `_solve_one_leg` (Held-Karp single-leg) — virtual drones use it.

## Sequencing (user-agreed: build new, THEN delete old; never break refuel)

1. **Backend virtual-drone slice (FIRST, de-risk):** `expand_to_virtual_drones`
   → allocate → Held-Karp per virtual drone → `merge_virtual_to_physical`. Unit-
   tested on a 2-stop case. Old two-leg path still present/working in parallel.
2. **Wire it** into `solve_mission_with_allocation` behind the new `refuel_stops`
   data (fall back to the old path while both exist).
3. **Data model + migration:** `refuel_airport` → `refuel_stops: []` (array);
   compat shim reads old single value as `[value]` (drop "-"/"" → []).
4. **UI:** refuel-stops grid (Stop 1..4, sequential activation), per the plan Part 2.
5. **Tankers:** `type` field, orange-diamond render, placement, validation
   (no tanker as start/end), per plan Part 3. (Can be a later sub-phase.)
6. **N-leg optimizer:** generalize the `_L1`/`_L2` virtual-leg awareness in
   post_optimizer to `_L{N}`.
7. **leg_distances[] :** merge emits `leg_distances[]` + `refuel_indices[]` (the
   deferred array rename lands HERE — see [VARIABLE_GLOSSARY.md](VARIABLE_GLOSSARY.md)).
8. **DELETE** the ANY-greedy + `_solve_two_leg_refuel` (final clean step).

Commit + test between phases. Each phase keeps refuel working.

## Wrinkles (tracked)

- **Allocation reachability:** a target assigned to `D1_L2 (A2→A3)` must be
  reachable on that leg's fuel. Allocator is geographic + per-drone budget — verify
  it won't hand a leg an unreachable target (else that leg's Held-Karp drops it).
- **One leg per target:** distinct virtual-drone IDs make the allocator assign each
  target to exactly one leg — no double count.
- **Fuel per leg (§11.10):** every virtual leg = full tank (`fuel_budget`); the
  FIRST leg of a dynamic replan inherits the physical drone's transient `start_fuel`.
- **Merge:** concatenate `D1_L1 + D1_L2 + …`, drop duplicate refuel-airport joints,
  emit `route`, `leg_distances[]`, `refuel_indices[]`, unioned `visited_targets`.

## Migration: dropping "ANY"

Old envs with `refuel_airport="-"` (Any) have no specific airport to convert to.
Decision: migrate `"-"` → `[]` (no refuel) — the user re-picks specific stops. A
specific `"A2"` migrates to `["A2"]`. Empty/`""` → `[]`.
