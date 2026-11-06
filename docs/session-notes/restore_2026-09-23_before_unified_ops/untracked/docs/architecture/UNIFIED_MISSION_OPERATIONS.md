# Unified Mission Operations — one toolset for the operator, Run Planner and the agent

**Status:** proposal for review (2026-09-23). No code has been changed for this yet.

## The problem

The browser side is already unified: Run Planner, Edit→Done, C-key cuts and the
agent chat all build their request with `buildSolveRequest()` and apply the
result with `applySolveResult()` (`webapp/isr.js`).

The agent's **server-side tools** are not. The reasoning agent
(`server/agents/reasoning_agent.py`) has its own planning tools, each with its
own way of building the problem:

| Agent tool | How it plans today | Problem |
|---|---|---|
| `solve_full_mission` | `run_solve_with_allocation` — same as Run Planner | OK, but ~500 lines of its body (the two-phase `start_with` block) are unreachable |
| `commit_ordered_plan` | Own SAM matrix + per-drone solve (538 lines) | Always visits every remaining target; can't "go home" |
| `solve_route` / `commit_route` (`_solve_and_store`) | Own mini-environment (airports + listed targets only) | Drops the cut's start nodes, ignores the requested end airport, refuses an empty target list |
| `solve_with_leg_split`, `commit_mission_plan` | Own paths (355 lines) | Defined but not given to the agent — dead code |
| `compare_routes` | Ranks `solve_route` candidates | Only exists to serve `solve_route` |

Every time the agent picks a different tool it gets different rules. The
"abort, all land at A1" failure (2026-09-23) was the agent calling
`commit_ordered_plan`, which planned full routes, and then describing the plan
it was asked for instead of the one it committed.

## The principle

**One set of mission operations. The operator, Run Planner and the agent all use
the same ones, through the same code.** The agent chooses *which* operations to
run; it never has its own way of planning.

**Route edits vs. replanning.** Most commander orders are *route edits*: they
replace or change a drone's remaining sequence from where it is now and leave
the environment untouched. Visited targets stay visited, unvisited targets stay
in the mission, and nothing is reassigned. Only "solve", "continue" and
"re-optimize" ask the planner to choose routes.

## The drone sequence is the whole mission

A drone's sequence shows everything it has done and will do, including every
diamond where its plan changed:

```
A1,T2,T5,C1,T9,T1,C2,A3
└──── flown (history) ────┘└ remaining ┘
```

- **History** is everything up to the drone's latest diamond. It is read-only:
  the drone already flew it.
- **Remaining** is the part after the latest diamond. This is what route edits
  change. Abort at C2 turns the sequence above into `A1,T2,T5,C1,T9,T1,C2,A1`.
- Diamonds show as `C1`, `C2`, matching the map labels; the app maps them to
  the drone's own node (`C1-1` for D1, `C1-2` for D2, …).
- The history is built from what was **actually flown**: each committed
  segment's route up to its cut, then the diamond. Stored segment routes are
  not trimmed at the cut today (they still list targets the drone never
  reached), so they can't be concatenated as they are.
- The sequence bar has shown only the current segment since commit `d947d9b`
  (2026-05-09). It goes back to showing the whole mission.

## 1. The operations

These already exist in the app. The agent will get exactly these.

| # | Operation | Operator does it with | Code that runs |
|---|---|---|---|
| O1 | **Change drone settings** — enable/disable, start/end airport, fuel, refuel airport, target types | Asset tab / Edit | Drone configs → `buildSolveRequest()` |
| O2 | **Change which targets a drone may visit** (skip, only-these, none) | *— missing (gap G1)* | Allocator per-drone allowed set (exists internally as `_priority_allowed_targets`, `server/solver/target_allocator.py:952`) |
| O3 | **Ordering rules** — first / last / before / adjacent, per drone | *— agent only today* | `constraints` on `/api/solve_with_allocation`; per-drone form `{"1": {"first": "T5"}}` already supported and forces those targets onto that drone (`server/solver/solve_mission.py:75`) |
| O4 | **Plan** (Run Planner) | Run Planner button, Edit→Done | `buildSolveRequest()` → `run_solve_with_allocation` → `applySolveResult()` |
| O5 | **Edit a drone's route** — replace its remaining sequence, or insert/remove a target in it. The central operation for commander orders | Sequence bar (`A1,T1,T2,A1`) | `/api/apply_sequence` |
| O6 | **Optimize** — Insert / Swap / No-Cross / Or-Opt | Optimize buttons | `/api/insert_missed_optimize`, `/api/trajectory_swap_optimize`, `/api/crossing_removal_optimize`, `/api/or_opt_optimize` |
| O7 | **Change the world** — add/move/delete targets, SAMs, airports | Edit mode | Browser state only |

O7 stays operator-only in this change (open question Q1).

## 2. Agent tools after the change

| Keep (read-only) | New (operations) | Delete |
|---|---|---|
| `get_targets`, `get_airports`, `get_drones`, `get_sams`, `get_current_mission_status`, `get_distance` | `set_drone(drone, enabled?, end_airport?, fuel?, refuel_airport?)` → O1 | `commit_ordered_plan` |
| | `set_allowed_targets(drone, targets \| "none" \| "all")` → O2 | `solve_route`, `commit_route`, `_solve_and_store` |
| | `set_order(drone, first?, last?, before?, adjacent?)` → O3 | `compare_routes` |
| | `plan(strategy?)` → O4 (the same `run_solve_with_allocation` call Run Planner makes, including the cut fields) | `solve_with_leg_split`, `commit_mission_plan` |
| | `set_route(drone, sequence)` → O5; `insert_in_route(drone, target, after?)` and `remove_from_route(drone, target)` are shorthands that edit the current sequence and call `set_route` | Dead two-phase block in `solve_full_mission` (the rest becomes `plan`) |
| | `optimize(kind)` → O6 (optional) | |

The `set_*` tools only change the agent's working copy of the mission for this
turn. Nothing is planned until `plan` or `set_route` runs, the same as the
operator editing settings and then pressing Run Planner.

About 1,550 lines of agent planning code are removed. The new tools are thin
wrappers over existing code.

## 3. Operator requests expressed as operations

| Request | Operations |
|---|---|
| "Abort. All drones go directly to A1." | every drone: `set_route(drone, "<its diamond>,A1")`. Environment unchanged |
| "D1 visit T8, then go to A2." | `set_route(D1, "<diamond>,T8,A2")` |
| "Add T9 to D1's route." | `insert_in_route(D1, T9)` (placed where it costs least, unless told where) |
| "D2 skip T4." / "Drop T4 from D2." | `remove_from_route(D2, T4)` |
| "Visit T9 first." (D1) | `set_order(D1, first=T9)`; `plan` |
| "Keep T4 and T5 together." | `set_order(adjacent=[[T4,T5]])`; `plan` |
| "Disable D1, continue." | `set_drone(D1, enabled=false)`; `plan` |
| "D1: C3 → T8 → A2 exactly." | `set_route(D1, "C3-1,T8,A2")` |
| "Only visit T1, T3, T5." | `set_allowed_targets(every drone, [T1,T3,T5])`; `plan` |
| "Solve." / "Continue from here." | `plan` |

Mid-mission turns need nothing special: the request already comes from
`buildSolveRequest()`, so the drones start at their diamonds, visited targets
are excluded and fuel is carried, exactly as for Run Planner.

## 4. Gaps — capabilities to add for everyone

- **G0 — Whole-mission sequence.** Build each drone's sequence from its flown
  history plus remaining route, with diamonds (section above). The sequence bar,
  `get_current_mission_status` and the agent's "Committed plan" all show this
  same sequence; an edit may change only the remaining part.
- **G3 — Route edits are not cut-aware (most important).** The sequence bar
  (`attachSequenceBar()`) sends the uncut `state.env` and writes `state.routes`
  directly, skipping the draft. It must build its request with
  `buildSolveRequest()` and apply with `applySolveResult()`, so a route from a
  diamond (e.g. `C2-1,A1`) works for the operator and for `set_route`. A route
  with no targets (diamond → airport) must be accepted and flown SAM-safe.
- **G1 — Per-drone allowed targets (only for "only visit T1, T3, T5" when
  replanning).** An `allowed_targets` drone setting the allocator honors,
  reusing the existing per-drone allowed set. Not needed for abort or route
  edits; can wait until after the demo.
- **G4 — The agent's reply must match what it committed.** The server appends a
  "Committed plan" section built from the routes actually returned, so the text
  can't contradict the map.
- **G5 — Agent setting changes must reach the UI.** The agent's reply returns
  the drone settings it planned with; `applySolveResult()` stores them on the
  draft, and Accept applies them like operator edits. After "land at A1", the
  Asset tab shows end = A1.

## 5. Also removed

- `replanFromCheckpoint()` in `webapp/isr.js`: never called, and it calls
  `/api/mission/replan_checkpoint`, which doesn't exist on the server.

## 6. Acceptance tests

Each runs from (a) a fresh mission, (b) a C-key cut and (c) a paused Edit, and
must give the same result whether the operator or the agent does it:

1. Abort, all land at A1: every drone goes diamond → A1, no targets; the
   environment is unchanged (unvisited targets still shown, not green).
0. After two cuts, each drone's sequence reads like `A1,T2,T5,C1,T9,T1,C2,A3`:
   flown targets, diamonds, remaining route — and matches the map.
2. Add a target, "D1 visit it then A2": D1 goes diamond → target → A2.
3. "D2 skip T4": T4 is not in D2's route.
4. "T9 first" on D1: T9 is D1's first target.
5. Disable D1: D1 stays in its red diamond; D2 replans.
6. Exact route typed in the sequence bar from a diamond: accepted and drawn.
7. The agent's reply lists exactly the routes on the map.

## 7. Open questions for Kamal

- **Q1** Should the agent also be able to change the world (add or move targets
  and SAMs), or only the mission settings above?
- ~~Q2 Abort semantics~~ — answered: abort is a route edit. The environment is
  untouched; only each drone's remaining sequence is replaced.
- **Q3** `allowed_targets` (G1) is now only needed for "only visit …" replans.
  Build it after the demo?

## Estimate

About 1.5–2 days, including tests: G1–G5, the new agent tools, and deleting the
old ones.
