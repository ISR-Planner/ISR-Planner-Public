# Handoff — Cut / Refuel / Fuel work (2026-07-10)

Paste this whole file to Claude at home to continue. It has no dependency on the work
machine — everything is in git (commit `f527cb6` on `main` and `feat/or-opt-optimizer`)
and on Railway.

---

## 1. Where we are

A long session fixed a chain of **correctness** bugs in the dynamic cut → replan →
opportunistic-refuel path. All are committed and pushed to **`main`** (Railway
auto-deploys from main) and to **`feat/or-opt-optimizer`**. Commit: **`f527cb6`**.

**Verified working in the browser (by the user):**
- Cut → replan route ENDS at a real airport (not the cut node / mid-air).
- Refuel actually executes at the tanker (imported-segmented replay no longer flies
  through TK1).
- Fuel CARRIES across cuts — a cut is fuel-neutral, no "free tank" refill.
- Fuel stat: LIVE while animating (starts at `0 / tank`, a leg accumulates across
  cuts and advances to a new `+` leg ONLY at a real refuel; budget = tank × legs so
  far). Before animating it shows the PLANNED solve. Mission row == drone box.
- Whole-mission refuel cap: `max_refuels` counts across the WHOLE mission, not per
  replan (each replan gets `max_refuels − refuels_used`).
- Unchecked refuel box (empty `refuel_options`) → drone never refuels.
- Stranded-but-can-go-home: when no target fits the remaining fuel but an airport is
  reachable, the drone FLIES HOME instead of parking / freezing.

**Fuel model the user confirmed as correct** (so don't "fix" it): tank e.g. 70; a leg
accumulates flown distance until a real refuel, always ≤ tank; budget denominator =
tank × number-of-legs-so-far (5 legs → 350). Mission fuel used = cumulative flown.

---

## 2. THE ONE THING LEFT — the C2 "double-back" sub-optimal replan (BANKED)

This is the next task. It is a **quality/optimality bug**, not a correctness bug
(routes are valid, just wasteful). The user cares a lot — provable optimality is the
product's whole pitch.

### The bug, in the user's words
> Original trajectory before the cut was `… TK1 → T2 → T11 → T8 → TK1 …`. The cut
> (C2) lands in the `TK1 → T2` segment — the drone just refueled at TK1 and is heading
> to T2. Instead of CONTINUING forward (`C2 → T2 → T11 → T8 → TK1 → …`), the replan
> breaks off BACKWARD to `T5, T10, T7 → TK1`, then goes `TK1 → T8 → T11 → T2`, and
> because T2 is adjacent to C2 it re-covers the `TK1 ↔ C2` stretch — **TK1↔C2 is
> travelled twice.**

### Reproduced deterministically (raw opportunistic solver output)
```
route = [C2-1, T5, T10, T7, TK1, T8, T11, T2, TK1, T15, T16, T17, A1, T18, T13, A1]
        └─ backward detour ─┘        └ back over C2↔TK1 ┘
```

### Root cause (confirmed)
The opportunistic DFS (`server/solver/opportunistic_refuel.py`) starts at the cut node
with **no directional / momentum context**. Its first leg may fly anywhere, including
reversing back toward the refuel base (TK1) it effectively just left. It optimizes
points-then-distance with no penalty for reversing the drone's committed heading, and
the two traversals of TK1↔C2 sit in DIFFERENT legs so the per-leg distance accounting
and the (per-leg) crossing-remover never flag them (`crossings_fixed=0`).

Also relevant: the post-optimizer pipeline (`server/solver/post_optimizer.py` +
`virtual_drones.py`) splits a refuel route into per-leg "virtual drones" and optimizes
each leg IN ISOLATION, so a crossing that spans a refuel boundary is invisible to
No-Cross / OR-OPT.

### Fix direction (to try next session)
Make the replan honor the drone's committed direction out of the cut node. Options:
1. Penalize / forbid the FIRST post-cut leg from immediately returning to the base the
   drone just departed (seed the cut node with an "arrived-from" heading so a reversal
   costs). Cheapest, most targeted.
2. Make the crossing-remover / OR-OPT refuel-AWARE (able to see and undo a crossing
   that spans a refuel leg boundary). Bigger, risks the leg-fuel invariants just fixed.

Start with (1); verify against the repro (below). Do NOT casually rewrite the leg model
— the whole-mission fuel + cut-neutral invariants are delicate and were just stabilized.

---

## 3. How to reproduce at home (NO work machine needed)

A deterministic repro script is committed: **`scripts/repro_c2_doubleback.py`**.

⚠️ It reads a local env file `~/Downloads/isr_env_20260710-1804_N1_1.json` that only
exists on the work machine. At home, do ONE of these:

**A. Fastest — rebuild the env inline.** Ask Claude to read
`scripts/repro_c2_doubleback.py`, then re-create a small equivalent env from the
matrix/coords embedded in this session's notes (or export a fresh env from the running
app: load any multi-target env with an A1 airport + TK1 tanker + 1 SAM, drone fuel 70,
`refuel_options=[TK1,A1]`, `max_refuels=4`, and save it). Point the script's `ENV_FILE`
at your file.

**B. Drive it against Railway.** The script POSTs to `http://localhost:8893`. Change
that URL to your Railway URL and run it — the checkpoint replan reproduces the tangle
server-side. Read the `🩺 D1 OPP RAW` and `🩺 D1 SUBOPT` lines in the Railway logs.

The script asserts the bug (first move out of C2-1 is the backward `T5`). When the fix
lands, that assertion flips to pass and the route continues forward to T2 first.

### Diagnostics already in the code (server logs)
- `🩺 D{did} OPP RAW: …` — the raw opportunistic route per solve (start, budgets,
  max_refuels, route, visited, refuel_idx).
- `🩺 D{did} SUBOPT: left=[…] refuels_used=N/M reachability=[…]` — targets left
  unvisited + whether they were reachable. A skipped-but-reachable target with refuels
  REMAINING = a real bug; skipped with refuels SPENT = the cap (working as intended).
- `🏠 D{did} stranded … flying HOME to …` — the go-home fallback firing.

These are safe to keep, or remove once the sub-optimal is fixed.

---

## 4. Key files + line anchors

- `server/solver/opportunistic_refuel.py` — the DFS. `bases` construction (cut node
  excluded), `consider_finish` (finish only at real end), the stranded go-home
  fallback, and `start_budget` (leg-1 reduced fuel). **The double-back fix goes here.**
- `server/solver/solver_bridge.py` — `_solve_opportunistic_drone` (OPP RAW + SUBOPT
  prints, whole-mission cap via `refuels_used`, `usable_options` filtered by
  `is_real_airport`), the plain-solve stranded→fly-home redirect, `is_real_airport`
  (THE predicate for "a drone may land/refuel here").
- `webapp/isr.js` — `updateStatsFromAnimation` (whole-mission fuel stat, live vs
  planned), the refuel-crossing detection (`_stops` derived from refuel_indices),
  `buildCheckpointEnv` (passes `refuels_used`, sanitizes end_airport), `startAnimation`
  (durable `state.legFuel` seed — must NOT zero it), `state.legFuel` / `legFuelHistory`
  are the durable per-leg fuel stores that survive cuts.
- `tests/test_opportunistic_start_budget.py` — start_budget, cut-node-never-an-end,
  stranded-flies-home. Run: `pytest tests/test_opportunistic_start_budget.py -q`.

## 5. Ground rules the user set this session
- Find the REAL root cause, don't patch symptoms. The user rejects band-aids.
- Reproduce deterministically (script), don't ask the user to hand-cut mid-animation.
- A cut node is a VIRTUAL airport — a seam flown through; no drone lands on it.
- Don't touch the confirmed-correct fuel model.
- Railway deploys from `main`; user tests there. Hard-refresh (Cmd/Ctrl+Shift+R) to
  load fresh JS; bump the `isr.js?v=` cachebust in `webapp/index.html` on frontend
  changes.

## 6. First move at home
1. `git pull` (or clone) — be on `main` (or `feat/or-opt-optimizer`), at/after
   `f527cb6`.
2. Read `scripts/repro_c2_doubleback.py` and this file.
3. Reproduce the double-back (§3), then implement fix direction (1) in
   `opportunistic_refuel.py`, verify against the repro, run the test suite.
