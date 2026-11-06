# Refuel-Aware Expansion Design

Note: the preferred full architecture is now captured in
`docs/solver/VIRTUAL_SORTIE_REFACTOR_PLAN.md`. This note describes helper
primitives that can support that architecture; it should not be treated as a
separate patch-first solver design.

## Problem

Held-Karp is intentionally capped to keep exact solves tractable. That cap must
only limit the seed route, not discard the rest of the mission.

In refuel-enabled missions today, targets outside the seed cap can be missed
because the generic insertion optimizer evaluates the merged physical route as a
single spent budget. It does not fully reason over fresh fuel after each refuel
or create extra sorties when refuel capacity remains.

## Contract

Mission selection remains:

1. Maximize priority points.
2. Minimize fuel for equal priority.
3. Prefer fewer refuels only when priority and fuel are equal.

The target cap contract is:

1. Held-Karp may see at most a bounded target set.
2. Targets outside that bounded set remain candidates.
3. Refuel-enabled expansion must try those candidates after the seed solve.
4. Expansion must evaluate each fuel leg independently.
5. Expansion may create new base-to-base sorties if `max_refuels` allows it.

## Pipeline Shape

1. Seed solve:
   - Select a bounded candidate set for Held-Karp.
   - Produce a legal route with route/refuel metadata.

2. Expansion:
   - Build the remaining target pool from allocated targets minus visited targets.
   - Split each route into fuel legs using `refuel_indices`.
   - Try inserting remaining targets into existing legs using per-leg fuel budget.
   - If a target does not fit existing legs, try adding a new sortie from a valid
     refuel base when refuel budget remains.

3. Final optimization:
   - Run route cleanup and swap optimizers without violating command constraints,
     refuel budgets, or frozen prefixes.

## Initial Implementation Scope

The first implementation should be pure helper logic in
`server/solver/refuel_expansion.py`, covered by unit tests. Wiring into
`solve_mission.py` should happen only after the helper behavior is stable.
