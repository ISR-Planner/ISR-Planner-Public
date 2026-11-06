# Virtual Sortie Refactor Plan

## Goal

Refueled missions should scale beyond the Held-Karp target cap without treating
missed targets as afterthoughts. A physical drone with refuel capacity should be
modeled as multiple virtual sorties, each with its own fuel budget, target
cluster, and route solve.

This is not a patch to one environment. It is a solver architecture change.

## Current Problem

The current solver flow does this:

1. Allocate targets to physical drones.
2. Cap each drone to `MAX_TARGETS_PER_SOLVE = 12`.
3. Run the solver/opportunistic refuel logic on only those capped targets.
4. Try to insert missed targets afterward.

That fails for high-refuel missions because the remaining targets were not part
of the real refuel planning problem. The generic insertion optimizer also does
not fully reason over fresh fuel per refuel leg or add new sorties when refuel
capacity remains.

## Preferred Model

One physical drone expands into virtual sortie slots:

```text
D1 physical drone
  D1_S1: start/base -> targets -> refuel/base
  D1_S2: base -> targets -> refuel/base
  D1_S3: base -> targets -> final airport or best airport
```

For target selection, these sortie slots behave like multiple drones. After
solving, they merge back into one physical route.

## Core Rules

Mission objective order stays:

1. Maximize priority points.
2. Minimize fuel for equal priority.
3. Prefer fewer refuels/sorties only when priority and fuel are equal.

Command constraints stay hard:

1. Required command segments are frozen.
2. Frozen prefixes burn fuel/time first.
3. Solving starts from the post-command state.
4. Optimizers cannot alter frozen route segments.

Refuel rules:

1. A sortie has one fuel budget.
2. A sortie may end at a valid refuel base if another sortie follows.
3. The final sortie must end at the configured final airport, unless final is
   `ANY`, in which case endpoint choice is part of candidate selection.
4. Tankers are valid refuel bases, not final mission endpoints unless explicitly
   allowed later.

## Architecture

### 1. Build Sortie Slots

For each physical drone:

1. Determine start airport or checkpoint.
2. Determine allowed refuel bases from `refuel_options`.
3. Determine `max_refuels`.
4. Create up to `max_refuels + 1` sortie slots.

Examples:

Fixed final airport:

```text
D1_S1: A1 -> A1
D1_S2: A1 -> A1
D1_FINAL: A1 -> A2
```

Any final airport:

```text
D1_S1: A1 -> A1
D1_S2: A1 -> A1
D1_FINAL: A1 -> best compatible airport
```

Multiple refuel bases:

```text
D1_S1: A1 -> chosen refuel base
D1_S2: chosen base -> chosen refuel base
D1_FINAL: chosen base -> final/best airport
```

The first version should keep base choice simple: use the start/home airport
when it is an allowed refuel base, and evaluate nearby compatible bases/tankers
as candidate sortie depots later.

### 2. Allocate Targets To Sorties

Do not use plain nearest-drone allocation when sortie slots share the same base.
All distances tie and the first slot can receive too many targets.

Use sortie clustering:

1. Group targets by reachable refuel base.
2. For same-base sorties, split targets into route-sized clusters.
3. Seed clusters with high-priority and hard-to-fit targets first.
4. Grow clusters with nearby targets while approximate round-trip cost fits fuel.
5. Keep each cluster near the Held-Karp cap.

This is equivalent to drawing sectors/wedges around the base, then letting exact
solves optimize inside each sector.

### 3. Solve Sorties

Each sortie slot gets a normal bounded solve:

```text
sortie start -> assigned targets -> sortie end
```

Held-Karp remains capped. The cap applies to each sortie, not to the entire
physical drone.

### 4. Merge Back To Physical Route

After all sortie routes are solved:

1. Concatenate sortie routes.
2. Drop duplicate join bases.
3. Record `refuel_indices`.
4. Record `refuel_stops`.
5. Record `leg_distances`.
6. Rebuild trajectory and waypoint indices.

The frontend should see one physical drone route.

### 5. Optimize Across Sorties

Run refuel-aware optimization after merge:

1. Insert missed targets into existing sortie legs.
2. Move targets between sortie legs.
3. Swap boundary targets between sorties.
4. Remove dominated sorties/refuels when they add no priority.

Every optimizer must validate each sortie leg against its own fuel budget.

## Implementation Phases

### Phase A: Pure Model

Add pure data structures and tests:

1. `SortieSlot`
2. `SortiePlan`
3. `build_sortie_slots`
4. `split_targets_same_base`
5. `merge_sorties_to_physical_route`

No wiring into the live solver yet.

### Phase B: Same-Base Refuel

Support the simplest high-value case:

1. Refuel base is the start/home airport.
2. Fixed final airport is respected on the final sortie.
3. Same-base target clusters are solved as virtual sorties.

This should cover the class of failures where many targets near A2 are missed
despite allowing many A2 refuels.

### Phase C: ANY Final Airport

For the final sortie:

1. Evaluate all compatible real airports as endpoints.
2. Choose by priority first, then fuel.
3. Preserve endpoint in route metadata.

### Phase D: Tankers And Multiple Bases

Add base selection:

1. Assign target clusters to nearest feasible refuel base or tanker.
2. Permit transit legs between bases only if they fit fuel.
3. Never use tankers as final mission endpoints.

### Phase E: Command/Frozen Prefix Integration

A command such as `D1 starts with T17` creates a frozen prefix:

1. Validate `start -> T17`.
2. Burn fuel/time.
3. Make T17 the effective sortie start.
4. Sortie planning begins after the frozen prefix.

## Testing Strategy

Unit tests:

1. Same-base sortie splitting produces multiple bounded clusters.
2. Fixed final airport is assigned to the final sortie.
3. ANY final airport chooses the best endpoint by objective.
4. Merge produces correct route/refuel metadata.
5. A target outside the first 12 seed targets can still be assigned to another
   sortie.

Integration tests:

1. Use `isr_env_20260709-1250_N1_1.json` shape with D1/D2, no tankers, many
   refuels.
2. Confirm targets such as `T25`, `T29`, `T30`, `T32`, `T33` are not excluded
   merely because they fall outside the first 12 target seed.
3. Confirm each leg respects its fuel budget.
4. Confirm refuel count does not exceed `max_refuels`.

Regression tests:

1. `refuel_airport="-"` with `refuel_options=[]` means no refuel.
2. Existing no-refuel solves remain unchanged.
3. Existing explicit `refuel_stops` virtual-drone behavior remains unchanged.

## Open Decisions

1. Should same-base sortie splitting use angular sectors, nearest-neighbor
   clustering, or farthest-first cluster growth first?
2. Should unused sortie slots be created up front or lazily added only when
   targets remain?
3. Should refuel count be optimized as a tertiary objective immediately or only
   after route quality is stable?
4. How should user-visible debug explain virtual sorties without confusing them
   with physical drones?

## Recommendation

Start with Phase A and Phase B. Do not wire tankers, ANY endpoints, and frozen
prefixes at the same time. The first milestone is:

> A physical drone with refuels at its own start/base can cover more than 12
> assigned targets by expanding into multiple virtual same-base sorties, then
> merging back into one physical route.
