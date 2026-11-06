# Agent Constraint Roadmap

What verbal constraints the ISR planner agent could accept, grouped by the
**machinery** each needs (which sets build order). The agent (the single
Reasoning Agent — `server/agents/reasoning_agent.py`) is the front-end: it reads
natural language and maps it to tools. There is no separate constraint parser —
**the tools are the vocabulary**, so a constraint only works if a tool can
express it.

Legend:
- ✅ **works today** — a tool/param already exists
- 🔶 **needs a solver hook + agent tool** — solver-expressible, incremental
- 🔴 **needs a rule store + event engine** — contingency, fires at animation time

> The agent silently **fakes or drops** any 🔶/🔴 constraint today: with no tool
> to express it, the LLM falls back to hand-building a route via `commit_route`
> (the UNOPTIMIZED path — diverges from Run Planner) or just acknowledges it in
> text without enforcing. Missing constraint support is *why* an agent solve can
> differ from Run Planner. See "One solve path" below.

---

## 1. Ordering / sequence
- ✅ "Start with T20 and T31 before anything else." (`solve_full_mission(start_with=[...])`)
- 🔶 "Visit T5 immediately before T8."
- 🔶 "The last target before landing must be priority 10."
- 🔶 "The first target each drone hits must be its closest."
- 🔶 "Visit all priority-10 targets before any priority-5."
- 🔶 "T12 must be the final target D2 visits."

## 2. Inclusion / exclusion
- ✅ "Only visit T1, T3, T5." (`solve_full_mission(only_targets=[...])`)
- 🔶 "Never route through the SAM corridor near T14."
- 🔶 "Skip all type-e targets."
- 🔶 "Must visit T9 — non-negotiable, even at fuel cost."
- 🔶 "Visit at least 8 targets per drone."

## 3. Assignment (target → drone)
- 🔶 "D1 takes the northern targets, D2 the southern."
- 🔶 "Give all priority-10 targets to D1."
- 🔶 "No drone visits more than 6 targets."
- 🔶 "T7 must be visited by D3 specifically."
- 🔶 "Balance points evenly across drones."

## 4. Fuel / range
- ✅ "Keep every leg under its fuel budget." (always enforced; `verify_solution_basic`)
- 🔶 "Leave 10% fuel reserve on landing."
- 🔶 "D2 must refuel at A3 specifically." (refuel_stops exists; not agent-exposed as a verbal constraint)
- 🔶 "No drone flies more than 80 km total."
- 🔶 "Prefer fewer refuels even if longer."

## 5. Spatial / geometric
- 🔶 "Stay at least 5 km from every SAM."
- 🔶 "No route crossings between drones." (No-Cross optimizer exists per-drone; cross-drone is new)
- 🔶 "Keep D1 and D2 paths from overlapping."
- 🔶 "Approach T8 from the east."

## 6. Priority / objective
- 🔶 "Maximize points, ignore fuel."
- 🔶 "Minimize total distance even if fewer points."
- 🔶 "Cover the most high-priority targets, drop low ones if needed."
- 🔶 "Every drone must score at least 30 points."

## 7. Timing / loiter (if modeled)
- 🔶 "Loiter 5 min at each type-d target." (loiter modeled in solver; not a verbal constraint)
- 🔶 "Finish the whole mission under 2 hours."
- 🔶 "Visit T15 within the first 20 minutes."

## 8. Contingency rules (event-triggered) 🔴
Needs a **rule store + event trigger** evaluated at animation time — NOT a solve
constraint. Ties to the "agent during animation" goal (Mission Executive is
currently a stub).
- 🔴 "If any drone is disabled, all abort and land at A5."
- 🔴 "If a drone drops below 15% fuel, return to nearest airport immediately."
- 🔴 "If a new SAM appears, reroute around it."
- 🔴 "If D1 fails, D2 takes its highest-priority targets."
- 🔴 "On reaching a checkpoint, re-evaluate and replan the rest."

## 9. Multi-condition (compound)
The agent must parse, split, and route each clause to the right mechanism.
- 🔶 "D1 visits northern priority-10s starting with T20, lands at A2 with 10%
  reserve; if disabled, D2 covers its targets."
- Any combination of the above.

---

## Build order (stacks)

1. **One optimized solve path (prereq).** Today `commit_route` / `solve_route`
   solve with `post_optimize=False` (no Insert→Swap→Or-Opt→No-Cross), so an agent
   solve can differ from Run Planner. Only `solve_full_mission` runs the pipeline
   (`run_pipeline_on_solution`). Unify so every constrained solve = Run Planner's
   process. (See [[two-solve-fns-dedup]] — related "one path" theme.)
2. **Solver-expressible constraints (🔶).** Extend the solver + add agent tools:
   ordering (end-property, before/after), assignment (drone affinity, caps),
   reserves, objective weights. Incremental — each is one solver hook + one tool.
3. **Contingency engine (🔴, group 8).** A stored rule + an event trigger at
   animation time. Biggest lift; ties to "agent during animation."

## Status today
- ✅ wired: start-with, only-targets, fuel-per-leg.
- 🔶 most others: need solver hook + agent tool.
- 🔴 contingencies: need rule + event engine.

The end goal is a **constraint-compiler**: verbal → structured constraint → the
right mechanism (solver param, route post-filter, or standing rule).
