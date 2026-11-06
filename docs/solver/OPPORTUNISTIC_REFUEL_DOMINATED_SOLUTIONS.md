# Opportunistic Refuel: Dominated-Solution Follow-Up

## Summary

Opportunistic refueling can currently return a legal route that is worse than a
known alternative with the same priority score. The observed case was an
environment where all targets could be covered at roughly 106 fuel units, but
allowing home-airport refuel at `A1` produced a route around 124 fuel units while
covering the same priority points.

That result violates the mission objective:

1. Maximize total priority points.
2. Among equal-priority solutions, minimize total fuel.
3. Use refuel count and target count only as lower-order tie-breakers if needed.

This is not an `A1`-specific issue. `A1` simply expands the feasible refuel
structure enough for the weakness to become visible.

## Expected Behavior

Opportunistic refuel means the solver may use refuel points when they help the
mission objective. It does not mean "use refuels whenever they are legal."

If a no-extra-refuel or fewer-refuel route already covers the same priority
points with less fuel, the refuel-heavy route is dominated and should lose.

Examples:

- More points with more fuel: acceptable if the priority gain is real.
- Same points with less fuel: lower-fuel route must win.
- Same points and same fuel: fewer refuels should usually win as a stability
  tie-breaker.

## Current Root Cause

The current solver pipeline is staged:

1. Allocate targets to drones.
2. Solve each drone independently.
3. Run post-optimization.
4. Return the resulting mission.

The opportunistic refuel code optimizes within one drone's assigned target set.
Inside `server/solver/opportunistic_refuel.py`, each DFS state asks the leg solver
for one best leg for a given start/end/available-target set. That result is then
used to continue the refuel-structure search.

The problem is early pruning: a locally best leg can be globally worse. It may
collect targets in an order that forces extra refuels later, while an alternate
leg that looks slightly worse at that state would preserve a lower-fuel global
mission.

So the failure is:

```text
legal local refuel plan != globally best mission plan
```

This is why simply adding an outer "try no-refuel too" candidate is not a deep
fix. It can guard some cases, but it does not address the real search problem:
the opportunistic refuel solver is not keeping enough alternate route/refuel
structures to compare globally.

## Why This Matters

The user expectation is correct: if all targets are already covered, adding more
refuel stops should not make the mission "better." A refuel-heavy route that
returns the same priority points for more fuel should be rejected.

This matters especially when:

- home-airport refueling is allowed;
- multiple tankers are allowed;
- max refuels is high;
- all targets have similar priority;
- allocation gives one drone a target set that can be solved in several legal
  refuel structures.

Those conditions make the route-structure space larger. A local-choice algorithm
can then miss lower-fuel alternatives.

## Correct Long-Term Direction

Move opportunistic refuel toward bounded multi-candidate search.

Instead of asking the leg solver for only one best leg per state, keep a small
set of viable candidates and compare complete mission candidates by the mission
objective:

```text
rank(candidate) = (
  total_priority_points,
  -total_fuel,
  -total_refuel_count,
  optional_stability_tie_breaker
)
```

Possible implementation approaches:

- Beam search over refuel structures, retaining top K partial routes per DFS
  state.
- Pattern enumeration as described in `docs/OPPORTUNISTIC_REFUEL_PLAN.md`,
  scoring completed patterns by points first and fuel second.
- A future fuel-reset dynamic program where fuel remaining is part of solver
  state. This is cleaner but larger and should be treated as a separate solver
  project.

The bounded candidate/beam approach is the likely practical next step because it
improves correctness without requiring a full solver rewrite.

## Guardrail Needed

Regardless of the search approach, every solver stage that proposes a replacement
solution should pass through one central comparator. The comparator must enforce
the mission objective and prevent a stage from overwriting a better known route
with a dominated one.

Required comparator behavior:

- Higher priority always beats lower priority.
- For equal priority, lower total fuel beats higher total fuel.
- For equal priority and fuel, fewer refuels is preferred.
- Target count should not outrank priority points.

Checkpoint replans already have a candidate-selection pattern that follows this
idea. The broader solver should adopt the same principle as an invariant, not as
an environment-specific patch.

## Reproduction Notes

Baseline environment used during discussion:

```text
/Users/kamalali/Downloads/isr_env_20260715-1226_N1_1.json
```

Known good fresh solve before additional edits:

```text
D1: A1,T5,T10,T11,TK2,T3,T6,T2,T12,TK1,T13,T1,T7,A1
D2: A1,T8,T9,T14,T4,A1
Fuel: approximately 107-108 / 120
```

Observed dominated case:

```text
Allow A1 refueling in addition to tankers.
Solver covers the same priority points but returns roughly 124 fuel with extra
refuel stops.
```

That should be treated as dominated if the lower-fuel solution covers the same
priority points.

## Future Work Checklist

- Add a route/mission comparator helper in one shared solver module.
- Add tests for dominated solution rejection:
  - same points, lower fuel wins;
  - higher points wins even with higher fuel;
  - same points/fuel, fewer refuels wins.
- Add a focused opportunistic-refuel regression using a small synthetic env where
  a local best leg creates an extra refuel but an alternate leg yields the same
  points with less fuel.
- Upgrade opportunistic refuel to retain multiple leg/structure candidates.
- Revisit allocation/refuel coupling after the candidate search is improved.

