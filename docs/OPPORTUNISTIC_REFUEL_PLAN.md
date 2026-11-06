# Opportunistic Refueling — Design & Plan

> **Goal**: A drone maximizes collected points by refueling at AVAILABLE points
> only when it helps reach more targets. The user designates WHICH refuel points
> a drone may use (and an optional max-refuel count); the SOLVER decides whether,
> when, where, and how many times to refuel. No pointless refuels, no empty
> sorties, no drone ever landing on a tanker.
>
> Date: 2026-06-24. Branch: feat/or-opt-optimizer. Target: working before the
> 2-week meeting; refactored for DoW delivery.

---

## 1. Model shift: prescriptive → opportunistic

**Before (the bug source):** `refuel_stops` = an ORDERED list of MANDATORY stops.
Expanded into fixed virtual legs (start→stop1→stop2→…→end). The drone was forced
to visit every configured stop in order, even when useless — producing empty
sorties (`TK1,TK1`), strands (`A1,TK1`), and an allocator that piled all targets
on one leg. Every refuel bug traced to this forced-leg model.

**After:** `refuel_options` = a SET of refuel points the drone MAY use. The solver
chooses the refuel plan (order, count, repeats) that maximizes points within fuel.

## 2. Data model

Per drone config:
- `refuel_options: List[str]` — allowed refuel-point ids (a SET; order irrelevant).
  Includes runway-compatible airports + all tankers (tankers have no runway).
- `max_refuels: int` — cap on how many times this drone may refuel (default 4).
  A pure count filter; does NOT prescribe where/when.

**Migration:** an imported `refuel_stops` (legacy ordered list) → de-duplicate to a
set → `refuel_options`. Keep reading `refuel_stops` as a fallback so old envs work.

## 3. UI — Assets tab refuel box → checkbox grid

Columns = every airport + every tanker. One row per drone:
- Compatible airports (runway ≥ drone runway): **checkbox enabled**.
- Incompatible airports: **greyed/disabled** (informative — you see why).
- Tankers (no runway): **always enabled** for every drone.
- A `Max refuels` number input per drone row.

```
REFUEL POINTS (check = may refuel here; greyed = runway too short)   Max
        A1(L)  A2(L)  A3(S)  TK1   TK2                                refuels
 D1(L)   ☐      ☑     ▨      ☑     ☐                                  [ 2 ]
 D2(S)   ☐      ☐     ☑      ☑     ☑                                  [ 3 ]
```

Re-renders when a drone's runway, any airport, or any tanker changes (same
triggers as today's populateRefuelStopsBox). A drone with no compatible point =
all-grey row = cannot refuel (no-refuel drone).

Writes `cfg.refuel_options` (the checked set) + `cfg.max_refuels`.

## 4. Solver — pattern enumeration (NOT a DP rewrite)

The Held-Karp DP state is `(visited, current_node)` with fuel checked once at the
end — it has NO fuel-remaining in state. Adding refuel-reset to the DP needs
fuel in the state (~100× state blow-up, 10–15 day rewrite). REJECTED for this
window. Instead, keep the virtual-leg model but make the LEG STRUCTURE
opportunistic via bounded enumeration:

```
best = None
for pattern in candidate_refuel_patterns(refuel_options, max_refuels):
    # pattern = a sequence of refuel points, e.g. [], [TK1], [TK1, TK1], [A2, TK1]
    legs = expand(start, pattern, end)          # virtual legs (existing machinery)
    sol  = solve + allocate + optimize(legs)    # existing pipeline per leg
    drop legs that earn 0 targets               # opportunistic: no empty sorties
    score = (total_points, -total_fuel)
    best = max(best, (score, sol))
return best
```

`candidate_refuel_patterns`: enumerate refuel sequences from `refuel_options`,
length 0..max_refuels, allowing repeats (sorties: refuel→targets→same point→
refuel). Bounded by max_refuels and a small options set — tractable at this scale
(≤15 targets, few refuel points). If the pattern space is large, prune by
reachability (only patterns whose points are within tank range of start/each
other).

**Opportunistic guarantees, by construction:**
- Refuel only if it adds targets → the score (points first) prefers patterns that
  unlock more targets; a refuel that adds nothing loses to the no-refuel pattern.
- No empty sorties → drop earn-0 legs before scoring.
- Unreachable targets dropped → orienteering already drops infeasible targets.
- Never end/land on a tanker → is_real_airport endpoint filter (already fixed).

## 5. One runway predicate everywhere (refactor win)

`_airport_accepts_drone` / `is_real_airport` already govern start validity, end
candidates, and (now) refuel availability. Same rule, one place — replaces the
scattered checks. Tankers: no runway → refuel-only, never endpoint.

## 6. Deferred (future / DoW handoff as future work)

- **Fuel-reset-DP** — the "elegant" model (fuel as a DP-state resource that resets
  at refuel nodes). 10–15 day solver rewrite. Documented as future funded work.
- **Tanker capacity** — a tanker servicing only K refuels TOTAL (shared across
  drones) couples drones into a joint resource problem. Deferred.
- **Pinned refuel order** — forcing a specific refuel sequence (the old slot
  semantics). Most missions want "use what's available"; add later if needed.
- **Dominated-solution guard/search quality** — the implementation must not accept
  a refuel-heavy route with the same priority points and higher fuel than a known
  lower-fuel route. See `docs/solver/OPPORTUNISTIC_REFUEL_DOMINATED_SOLUTIONS.md`.

## 7. Build order (Week 1)

1. UI: refuel box → checkbox grid + max-refuels input; write refuel_options +
   max_refuels; migrate legacy refuel_stops on import.
2. Solver: candidate_refuel_patterns + the enumeration loop; drop earn-0 legs.
3. Verify on the failing envs (isr_env_20260624-1320 / -1445): correct multi-sortie
   routes, no empty sorties, no pointless refuels, unreachable targets dropped,
   ends at a real airport.
