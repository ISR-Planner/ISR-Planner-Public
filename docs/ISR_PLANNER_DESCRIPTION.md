# ISR Planner — Living Description

> **Purpose of this document.** A growing, authoritative description of what the
> ISR Planner *is* — its concepts, features, and facts — written in plain terms.
> It is built up incrementally: the user adds understanding and facts over time,
> and these are folded into a coherent whole. This is a living document; sections
> will be added, refined, and reorganized as we go.
>
> **Status:** 🌱 Growing — core workflow and dynamic-mission behavior captured.
> **Last updated:** 2026-06-03

---

## 1. What the ISR Planner Is (the user's understanding)

The ISR Planner is a tool an **ISR Officer** uses to **simulate a future mission**.

The basic workflow:

1. **Create.** The officer lays out the mission on a canvas/map — placing the
   environment (targets, SAM zones, airports, drones, etc.).
2. **Solve.** The planner generates a candidate solution.
3. **Accept or reject.** The officer reviews the proposed solution and either
   accepts or rejects it.
4. **Animate.** If accepted, the officer can animate the solution to watch the
   mission play out on screen.

### 1a-0. The Mission (identity, start, and end) — foundational

Everything else — segments, the constraint ledger, "full memory within a given
mission" (§1e) — is **scoped to a Mission**. For that scoping to mean anything, a
Mission must be a **first-class, identifiable, durable thing**, with:

- An **identity**: a mission **name and/or number** (or other identifying feature)
  that the officer can **declare**.
- A **defined start** and a **defined end** — an explicit lifecycle, not an
  accidental one.

**Agreed lifecycle & ownership rules:**

- **A mission ends by an explicit officer action** (e.g. an "End Mission"
  command/button). A mission does **not** end implicitly — in particular,
  environment/config edits mid-mission must **stay inside the same mission** rather
  than silently forking a new one. (This is the opposite of today's hash-mismatch
  behavior, and is what lets the constraint ledger accumulate across edits/turns.)
- **The Mission owns the environment.** The map/scenario (targets, SAMs, airports,
  drones, etc.) is a property **of** the named mission. The mission is the
  top-level container; its environment, solutions, segments, conversation, and
  constraint ledger all belong to it.
- **Reload / replay of a mission** — how a saved mission is re-opened and/or
  replayed — is deliberately deferred. See §6 (Open Questions).

> **Current reality (gap — the Mission is implicit and ephemeral today):**
> - `mission_id` is **auto-generated** as `f"m_{uuid4().hex}"` on the first solve
>   (`server/main.py`). There is **no user-facing name/number, no declared start,
>   no declared end**.
> - The mission record lives in `MISSION_STORE`, which is **RAM-only and resets on
>   every server restart/redeploy** (`server/agents/mission_store.py`).
> - A mission "ends" only **implicitly**: changing the environment/configs trips an
>   `env_hash` / `cfg_hash` mismatch that **silently discards the old `mission_id`
>   and mints a new one**. The boundary is an artifact of hashing, not a deliberate
>   act.
> - `mission_name` exists in `mission_ledger.create_mission_run(...)` but is
>   hardcoded to `"isr-run"`, and that function is now a **legacy no-op**.
>
> **Why this is a prerequisite for §1e.** "Full memory of all conversations and
> solutions within a given mission" requires a mission that is **stable,
> identifiable, and durable**. Today's mission is anonymous, RAM-only, and
> dissolves the instant the environment changes — so a constraint ledger cannot
> accumulate across turns (a geometry edit silently forks a new mission). **Defining
> the Mission is therefore a prerequisite for the memory/ledger work, not a side
> feature.**

### 1b. Two ways to solve: Run Planner vs. Agent Solve

There are two ways to produce a solution, and the difference is **conditions**:

- **Run Planner** — a **direct, unconditional** solve. It solves exactly what is
  on the canvas, with no extra conditions attached.
- **Agent Solve** — a **conditional** solve. The officer can express constraints
  in natural language and the agent produces a solution that honors them.
  Examples:
  - *"Start the mission by visiting targets Tx and Ty, and only then complete the
    mission."* → the drone flies to Tx and Ty first, then optimally completes.
  - *"The last target you visit before completing the mission should be a
    priority-10 target."*
  - …or any other condition.

Run Planner = solve only; Agent = solve **with** officer-specified conditions.

---

## 1a. Dynamic Mission (changes while a mission is ongoing)

A **Dynamic Mission** is what happens when the situation changes *while a mission
is already underway* (i.e. during animation).

Current behavior (via the **Editor**):

- At **any point during animation** — whether it is **playing or paused** — the
  officer can activate the **Editor** and make changes to the environment
  (add or remove items).
- Turning the Editor back **off**, or accepting the edit via the **Done** button,
  **automatically triggers a new solve** — but only for the **un-frozen part** of
  the environment, including the newly added or removed items. (The already-flown /
  frozen portion of the mission is preserved.)
- Accepting this new solution re-activates the **Animate** button, so the mission
  animation can be **continued** from where it was.

**Cut points (the diamond markers).** The point in a drone's trajectory where a
new solve happened is marked with a **diamond (a "Cut point", `Cx`)**:

- **White diamond** — a normal cut point: the new solve happened here and the
  drone continues.
- **Red diamond** — a cut point where the solve **de-selected (dropped) that
  drone**, i.e. the drone is disabled from this checkpoint onward.

(Code: cut points are created via `freezeAtCheckpoint` in `webapp/isr.js`, which
captures per-drone cut positions; dropped drones are tracked as `lostDrones` /
"drones disabled during live edit (marked with red diamond at checkpoint)". The
flown-up-to-cut portion is stored as the **frozen** trajectory/targets; the solve
applies only to the **un-frozen** remainder.)

> **Today's task (goal).** This dynamic re-planning flow currently works **only
> through the Editor**. The goal is to give the **Agent** the same ability — so the
> officer can drive mid-mission changes and re-solving conversationally through the
> Agent, just as they can with the Editor today.

### 1c. Agent in the Dynamic Mission (today's task — agreed scope)

The goal is to bring the **conditional** power of Agent Solve (§1b) into the
**Dynamic Mission** (§1a). That is: while a solution is *animating*, the officer
can ask the Agent to **apply a condition** to the **remainder** of the mission, and
the Agent re-solves the un-frozen part under that condition.

Example mid-animation requests:

- *"Go to A2 and get refueled, then visit a priority-10 target before you complete
  the mission."*
- *"Do not land at A5 at the end of this mission."*
- …or any other condition.

**The key equivalence — Agent SEND behaves exactly like Editor DONE.** Pressing
**SEND** in the Agent tab during animation must:

1. **Halt** the animation (freeze the flown portion → create the cut point `Cx`).
2. **Apply** the condition the officer typed.
3. **Re-solve** the un-frozen remainder under that condition.
4. Surface a solution the officer can **Accept**.
5. On **Accept** → re-enable **Animate** → **continue** from the cut point.

**Division of labor — Editor owns geometry, Agent owns conditions.**

- **Geometry edits (add/remove items) are Editor-only.** If a geometry change is
  needed mid-mission, the **Editor must be invoked** to make it. The Agent does
  **not** do geometry edits.
- **The Agent owns conditions.** Agent SEND applies a natural-language condition to
  the (possibly already-edited) environment and re-solves.
- **Both DONE and SEND are valid re-solve triggers at a cut point, and they
  compose** — used alone or in sequence:
  - *Editor DONE first* → a solution reflecting the **geometry change**, with **no
    Agent condition** yet.
  - *Then Agent SEND* → a **second solve** that layers the **condition** on top of
    that edited environment; this can then be accepted and animated.
  - *Agent SEND alone* (no prior edit) → applies a condition to the **unchanged**
    environment.

**Full supported flow:**

```
Solve (Run Planner OR Agent)
   → Animate
      → [optional] Editor edit → DONE      (geometry change → 1st re-solve, no condition)
      → Agent SEND with a condition         (== Editor DONE; layers condition)
         → halt + apply condition + re-solve un-frozen remainder
            → Accept solution
               → continue animating from the cut point
```

Each step (DONE, SEND) independently yields an acceptable / animatable solution.

**Staging an edit + a condition into one SEND.** The officer can **activate the
Editor, make geometry changes, *and* type a condition for the Agent**, then press
**SEND** — and SEND starts a single solve that honors **both** the geometry change
and the condition. So the officer is not forced to DONE-then-SEND as two separate
solves; the edit and the condition can be staged together and resolved by one SEND.

### 1d. Behavior rules (agreed)

- **Condition is mandatory on SEND.** When SEND is applied, the condition **must be
  respected and fulfilled** by the resulting solution.
- **Infeasible condition → best-effort + explanation.** If the condition **cannot
  be satisfied**, the Agent returns a **best-effort solution accompanied by an
  explanation** of why the condition couldn't be met. The officer can then
  **accept or reject** it.
- **Zero-fuel drone → red diamond + notification.** If the solve results in a
  **0-fuel drone**, that drone is marked with a **red diamond** at the cut point
  (consistent with the de-select case in §1a), and the officer is given a **user
  notification**.

> **Implementation note (current reality).** The Editor's DONE handler today is
> `acceptEditsWithCheckpoint()` in `webapp/isr.js`: it stops animation, captures
> per-drone cut positions and remaining fuel, creates a checkpoint segment, and
> re-solves the un-frozen remainder. There is currently **no condition channel**
> riding along with it. Wiring Agent SEND therefore means reusing this same
> checkpoint/freeze machinery but feeding it (a) any staged geometry edit and (b)
> the typed condition. This is the design target, not yet-existing behavior.

### 1e. Agent memory & the mission constraint ledger (vision)

For the Agent to behave like a **real human helper** — making suggestions,
catching errors, and flagging contradictions — it needs **full memory of all
conversations and solutions within a given mission**, backed by the **Supabase**
database. The agent should reason against this memory, not just recall it.

**Motivating example.** If the officer earlier said *"Never land at A4,"* and later
asks *"Change the landing airport to A4,"* the Agent should **detect the
contradiction**, alert the officer, and **ask whether to override** the earlier
constraint — rather than silently obeying the latest request.

**Three capabilities bundled here (separated by cost):**

1. **Conversation recall** — remember what was said and solved this mission, so
   "why did you do X earlier?" works. (Same as weekend item #2.)
2. **Persistent constraint ledger** — every stated condition is accumulated as
   *structured, queryable state* (e.g. `landing_airport ≠ A4`), not just free text.
   This is what makes contradiction detection *possible*; free text cannot be
   reliably checked for conflict, structured constraints can.
3. **Contradiction / advisory reasoning** — on each new request, the Agent checks
   it against the **active** ledger; on conflict it surfaces the contradiction and
   offers an **override**. (#3 depends on #2.)

**Key insight.** Supabase gives *durability*, but durability alone does not catch
the A4 contradiction — the value comes from representing conditions as structured
constraints the agent reasons against each turn. So the goal is best framed as a
**per-mission constraint ledger (durable in Supabase) that the agent reasons
against**; conversation recall and contradiction-advisory both fall out of that.

> **Current reality (what already exists — large head start).** Much of this
> infrastructure is already in the codebase:
> - **Structured constraints** — `server/memory/constraints.py` defines
>   `ConstraintProgram` + dataclasses (`ForceVisitConstraint`, `MoveConstraint`,
>   `RemoveConstraint`, `SwapConstraint`, `InsertConstraint`) and a
>   `ConstraintCompiler` that emits `forbidden_targets` / `required_targets`
>   patches.
> - **NL → constraint parser** — `server/memory/constraint_parser.py`
>   (`ConstraintParser`, `parse_constraints`).
> - **Durable Supabase storage** — `server/database/mission_ledger.py` already
>   persists `agent_runs` (with `hard_constraints` / `soft_preferences` fields!),
>   `agent_traces` (full decision-trace JSONB), `agent_optimizer_steps`, and
>   `agent_policy_rules` (learned rules).
>
> **Gaps to close (so this is integration, not green-field):**
> 1. **Airport/landing constraints.** The constraint vocabulary covers *target*
>    ops well, but a first-class **"never land at Ax" / landing-airport** constraint
>    type appears to be missing and likely needs adding.
> 2. **Cross-turn accumulation + contradiction check.** Nothing yet accumulates
>    conditions across turns into a live per-mission ledger and tests a new request
>    against the active set for conflict (the A4 case).
> 3. **Ledger as working memory.** The Supabase tables exist, but the per-mission
>    constraint ledger isn't yet wired as the thing the agent reasons against on
>    every turn.

---

## 2. Core Concepts

_Definitions of the key entities and ideas (drones, targets, SAM zones, airports,
fuel, missions, segments, etc.) — added as they come up._

---

## 3. Features

_Capabilities of the planner, described from a user/behavior standpoint._

---

## 4. How It Works (mechanics)

_How the pieces fit together: solving pipeline, the agent, optimization, animation._

---

## 5. Facts & Constraints

> **CANONICAL VISUAL + STRUCTURAL DEFINITIONS (user, 2026-06-05).**
> - **Airport** = blue square. Valid start/end + refuel.
> - **Cut point** = **white diamond** (drone continues) / **red diamond** (drone
>   deactivated here). A cut point is an END of a trajectory/segment/leg.
> - **Mid-air refuel point (tanker, planned/later)** = **green diamond labeled T1,
>   T2, …**. Refuel-capable but **NOT a valid start/end airport** (endpoint-
>   ineligible); runway-agnostic (airborne). Model = "refuel point with
>   `endpoint_eligible:false`"; a placed map entity in `env`/matrix; reuses all
>   refuel machinery; build ON TOP OF multi-refuel (don't build the leg
>   generalization twice). The matrix doesn't care about node kind → "just works"
>   once §10 (one distance truth) holds.
> - **Segment** = a single waypoint→waypoint hop (between targets/airports/cut
>   points). First segment after the start airport = **segment-0**.
> - **Leg** = one tankful, bounded by refuels: **leg = one-or-more segments**.
>   Non-refuel drone = only **Leg-0**. Refuel drone = Leg-0, Leg-1 (future Leg-2/3/4).
>   A refuel airport/tanker is a **leg boundary** (tank resets to full); not every
>   segment boundary is a leg boundary.
> - **Frozen** (animation-time): everything BEHIND the drone is the past — those
>   targets are already eliminated, cannot be moved, "no longer exist." Frozen is a
>   *display-of-history* concept, NOT the trajectory the drone re-walks.
> - **At a cut** (animate → Edit → Accept): the drone is AT the cut point (inside the
>   diamond). **On resume**: it continues FROM the diamond (segment-relative; the
>   animated trajectory starts at the diamond, NOT the origin airport).
> - **Reset** = back to t=0: drone at the start airport, environment pre-edit.
>
> **FUEL IS PER-LEG, NOT PER-CUT (user, 2026-06-05 — corrects a repeated AI error).**
>   - A **cut (C1, C2, …) subdivides a leg into segments but is FUEL-NEUTRAL.** It is
>     just WHERE re-planning happens; it does NOT reset, segment, or clamp fuel.
>   - **Leg-0's fuel budget = the full tank, and it must cover ALL segments in Leg-0
>     — both BEFORE and AFTER every cut.** Fuel burned before C1 counts against
>     Leg-0's tank; what remains must cover the rest of Leg-0.
>   - A **replan's fuel budget at a cut = full tank − ALL fuel burned IN THIS LEG so
>     far** (across every segment of the leg, pre-cut included). NOT "full tank −
>     distance on segment", NOT a "committed-remaining" clamp (both were wrong AI
>     patches).
>   - **Only crossing a REFUEL airport starts a new leg with a fresh full tank.**
>   - **Frozen/history ACCUMULATES across cuts:** Airport→C1 AND C1→C2 AND C2→… all
>     stay drawn as past history; a new cut must not erase the earlier flown paths.
>   - **The final segment (last cut → end airport) must render** like any other.

> **DECISION (2026-06-05): keep BOTH coordinate systems; do NOT unify on haversine.**
> SAM-avoidance geometry (tangents, circle intersections) REQUIRES a planar xy(km)
> frame, so the solver/SAM/matrix layer is inherently xy and must stay. lat/lng is
> display-only. haversine-vs-xy differ by METERS over mission scale — not a real bug
> source (the cut bugs are data/logic, e.g. frozen-prefix prepended to the animated
> trajectory, not distance-formula). Calculation load is negligible either way.
> Correct unification = §10 "matrix is the single distance truth, lat/lng derived by
> ONE projection step," NOT "haversine everywhere" (which would force re-deriving SAM
> geometry in lat/lng or keep both systems anyway — no gain).

> **GOVERNING DESIGN RULE (user, 2026-06-04).** Every concept below is a **single
> canonical definition that ALL code must reference** — "one concept → one function →
> one constant" (§9). Nearly every bug this session came from a definition
> implemented in multiple places, inconsistently (visited-target thresholds,
> trajectory build, per-leg fuel budgets). When a definition lives in exactly one
> place: fixes propagate everywhere automatically, new code cannot drift, and the
> invariants become *checkable* (assert them, don't hope). **This is the primary
> defense against bugs — universal, uniform definitions.** Treat these as binding
> invariants; if any code path violates one, that path is the bug.

**Canonical invariants (each must be enforced in ONE shared place):**
1. **SAM-aware distance everywhere** — never Euclidean. (see below)
2. **A leg = start → finish; zero-target legs are valid;** feasible iff
   `SAM_distance ≤ fuel`. (see below)
3. **Trajectory = direct 2-vertex line absent SAMs; vertices (incl. targets)
   preserved through all transforms.** (see below; §9 #1 done)
4. **"Target visited" = drone reached the target's vertex (≤ `TARGET_HIT_KM`).**
   (§9 #1 — single source `isTargetVisited()`)
5. **Refuel = N-leg trip; EACH leg has a full tank (`fuel_capacity`) available;**
   each leg checked independently (`leg_used ≤ tank`); each an independent virtual
   leg-drone. **Stats: used = Σ leg_used; budget = tank × N; P/F = points / used.**
   (canonical fuel model below)
6. **Mission identity persists across edits; ends only by explicit action.** (§1a-0)

**INVARIANT — SAM-aware distance everywhere (agreed, absolute).** **ALL** distance
calculations, in **every** code path (solver, optimizers, validators, leg checks,
animation, stats), use the **SAM-aware distance matrix**. Euclidean / straight-line
distance is **never** used as a substitute — code must raise/refuse rather than fall
back to Euclidean (precedent: `_solve_one_leg` raises rather than computing a
straight-line distance, `solver_bridge.py` ~658). Any feasibility comparison
(`dist <= budget`) must use the SAM-aware `dist`.

**INVARIANT — a leg needs no target.** A leg is simply **start → finish**; a
**zero-target leg is valid and feasible** purely on distance vs. fuel. A bare
repositioning/return leg (e.g. A1→A2, or refuel→end with no targets) is a legitimate
leg. Corollary (user's invariant): a drone with 10 km fuel and a 10 km SAM-aware
A1→A2 distance **must fly** — feasibility is `SAM_distance <= fuel`, nothing more. A
leg must NEVER be rejected/zeroed merely for lacking a target.

**CANONICAL FUEL MODEL (agreed 2026-06-04 — the single definition for all of
solver, validator, and stats).**

Terms (per drone):
- `tank` = the drone's **full tank** = `fuel_capacity` (D1=15, D2=16 in the sample).
- A **no-refuel** trip = **1 leg** (L0). A **refuel** trip = **N legs** (L0..L(N-1)),
  L0 = a virtual leg-drone, L1 another, etc. (§refuel model below).
- `leg_used[i]` = **SAM-aware trajectory length** of leg i.

Allocation across legs (refuel): L0 carries all the drone's allocated targets; L1
carries those minus what L0 used; … cascading leftovers. Each leg is an
**independent virtual leg-drone** through the full optimizer pipeline.

Refuel semantics: refueling **tops up exactly what the previous leg burned**, so
each leg departs with a **full tank** available. (Topping up what you burned ⇒ fuel
put in = fuel used; there is no extra fuel beyond what's consumed.)

**Fuel feasibility (per-leg, independent):** each `leg_used[i] <= tank`. NOT the sum
vs. a sum — **each leg against one full tank.**

**Stats box (DECIDED — Option A, N×full-tank denominator):**
- **Numerator (fuel used)** = `Σ leg_used` = `L0 + L1 + … + L(N-1)`.
- **Denominator (budget)** = **`tank × N`** (one full tank per leg). *Chosen for
  being unambiguous and matching the feasibility envelope; the trade-off (it shows
  capacity carried, not fuel committed) is accepted. Because it lives in ONE
  function, switching to a "committed-fuel" denominator later is a one-line change.*
- **Display:** single-leg → `used / tank`; multi-leg → `⛽L0+L1+…+L(N-1) / (tank×N)`
  (e.g. D1 first solve `⛽15+15 / 30`; the 10%-of-L0 example `⛽1.5+15 / 30`).
- **P/F** = `points / (Σ leg_used)` (points per fuel **used**, not per budget).
- **Edit→Done (dynamic replan):** identical formula. Legs are re-solved from the
  diamond; each leg still measures `leg_used` (SAM-aware) against a full `tank`;
  numerator = Σ new leg_used, denominator = `tank × N` for the new leg count.

> This model must be computed in **one canonical place** and referenced by the
> solver (leg feasibility), the validator (`verify_solution_basic`), and the stats
> box (`isr.js`). The bugs §8.2/§8.3 and the `6+0 / 25` stats came from this being
> implemented inconsistently in those three places.

**INVARIANT — a dynamic replan can NEVER be worse than the existing solution, and
NEVER hard-fails (agreed 2026-06-04).** The mission already has an optimal, feasible
solution. A cut + added targets is an **opportunity, not an obligation**:

- The replan re-solves from the diamond, but the **existing remaining solution is the
  FLOOR** — the result can never be worse (fewer points / infeasible) than simply
  finishing the existing route.
- New targets are taken **only if they improve** on that floor (fit the available
  fuel). If they don't fit, they are **dropped** and the existing solution stands —
  unchanged. The drone "was going to land regardless."
- **The replan therefore can NEVER hard-fail** (no `route[x] exceeds fuel budget`
  reject), because the existing feasible remainder is always an available answer.
- The replan does **NOT** invent a different end airport or a different target set to
  "land somewhere closer": the original solve was optimal, so any closer/cheaper
  feasible alternative would already have been chosen. Proposing one contradicts the
  optimality of solution #1.

> ⇒ The bug behind §8.3/§8.5 and the repeated `... exceeds fuel budget: x > 0.00`
> hard-fails: the dynamic replan currently does an **all-or-nothing re-solve** that
> can reject everything, instead of falling back to the existing-solution floor and
> dropping only the targets that don't fit. Fix = make the replan degrade to the
> floor (drop unreachable additions, keep the known-good remainder), never reject.
> NOT a per-case patch — the solve/optimize must *always* yield the best feasible
> subset down to "fly the existing remainder," for every drone state.

> **STATS BOX — ✅ FIXED (2026-06-04).** Added the single source of truth
> `computeFuelStats(routeInfo, tank, points)` + `getLegDistances()` in
> `webapp/latlng_utils.js`: used = Σ leg_used, **budget = tank × N**, P/F =
> points/used, per-leg feasibility. `updateStatsFromRoutes()` now calls it (replaced
> the wrong `refuelBudget = budget + leg1` formula and the ad-hoc leg display).
> Verified vs. agreed examples: D1 `⛽15+15 / 30`; 10%-of-L0 `⛽2+15 / 30` (used
> 16.5); D2 now correctly `⛽14+16 / 32` (was wrongly `/30`); single-leg `12 / 15`.
> `getLegDistances` is multi-refuel-ready (prefers a `leg_distances` array; falls
> back to leg1/leg2; non-refuel = one leg). The `6+0 / 25` + frozen-count symptoms
> were the stats reading a **rejected** replan's stale `state.routes`; with the
> validator fix (§8.3) the replan now succeeds and `state.routes` refreshes — needs
> live re-confirm.

**Trajectory definition (agreed, uniform).** A trajectory between two route
waypoints is the **direct point-to-point line — exactly two vertices `[start,
end]`** — in the absence of SAMs. Only SAM avoidance inserts intermediate
vertices. This definition must be uniform throughout the code.

- **Generation side: CONFORMS.** `server/solver/trajectory_planner.py:141` returns
  `[list(start), list(end)]` for a clear path (two vertices). ✅

**Trip = single-leg (no refuel) or N-leg (refuel) — agreed model (user, 2026-06-04).**
- **Refuel NOT allowed → a single-leg trip.** One drone, one tank, start → end.
- **Refuel allowed → a multi-leg (N-leg) trip.** Each leg is an independent virtual
  drone with its **own fuel budget**, and **each leg's budget must be respected
  independently**:
  - **Leg 1**: starts at the trip start (an airport on a fresh solve; the **diamond**
    on a dynamic replan) with the fuel it has there (full tank fresh; **remaining**
    fuel on a replan); sees the **full environment**; does its best; lands to refuel.
  - **Every subsequent leg (2..N)**: starts after refueling with a **FULL TANK**
    (`fuel_capacity`); solves whatever targets remain.
  - All legs run the **full solve + optimize** pipeline (Insert / Swap-between-legs /
    Remove-crossings), exactly like a normal refuel solve.
- **Next step (planned):** support **multiple refuel stops** → the N-leg
  generalization above (today's fix covers N=2; the per-leg-budget rule already
  generalizes — only leg 1 differs).

**Refueling = independent virtual leg-drones (agreed, VERIFIED IN CODE).** Each leg of a
refueling drone is treated as an **independent virtual drone** for the optimizers:
`D1 → D1_L1 + D1_L2`, each with its own route/trajectory/budget. Implemented in
`server/solver/post_optimizer.py` via `split_refueling_drones()` (before opt) and
`merge_virtual_legs()` (after opt); leg ids `{did}_L1`/`{did}_L2`, markers
`_virtual_leg` / `_parent_drone`. Landed in commit `579b7e9` (now in this branch
via the rebase onto `main`). ✅ INSERT/SWAP/No-Cross see two separate drones + two
separate trajectories. *Open check (tie to §8.1): confirm animation visited-
detection handles the merged refuel route / leg markers correctly.*

---

## 8.4 — Replay/Reset corruption (≥2 cuts) + fuel_capacity overwrite

**Symptoms (user, 2026-06-04):** (1) Reset→Animate→Reset breaks with **≥2 cuts**
(single cut works); inflated point counts (D1:129pts) after replay. (2) Stats show
**D2 `⛽15+16 / 31`** — budget should be `16×2 = 32`.

**Root cause #1 — replay aliasing (deep-copy missing).** `buildCombinedRoutesFromSegments`
spreads `{...routeData}` from each stored `missionReplay._segments[i].solution`, so
nested fields (esp. `route`) stay **aliased** to the stored segment. Callers assigned
that straight into `state.routes` (`isr.js` ~2882/2916, missing the deep copy the
accept path at ~3728 has). Animation then mutates `state.routes[did].trajectory`
in place (densify ~10740, loiter splice ~10803), writing **back into the stored
segments** → a later Reset replays corrupted/inflated data (compounds with ≥2 cuts).
**Fix:** `buildCombinedRoutesFromSegments` now `return JSON.parse(JSON.stringify(...))`
— one chokepoint makes stored segments immutable for ALL callers.

**Root cause #2 — fuel_capacity overwritten by the mutable budget (§5 disease again).**
`readDroneConfigsFromUI` set BOTH `fuel_budget` AND `fuel_capacity` from the fuel
input (`isr.js` ~6710-6711); a dynamic replan reduces that input, so `fuel_capacity`
(the immutable full tank) got clobbered to the reduced value → `tank×N` wrong (D2
`31` not `32`). A second path (~6830) dropped `fuel_capacity` entirely. **Fix:**
`fuel_capacity` is now **preserved from the existing config** (seeded from the input
only when unknown), never overwritten by the mutable budget — in both paths.

> Status: both fixed; `node --check` clean. Awaiting live re-test (≥2-cut
> Reset→Animate cycle clean; D2 budget = 32). Note: a *separate* `_fullTrajectory`
> re-densify inflation vector remains (densify re-runs on already-densified traj on
> the plain replay path) — deferred; the deep-copy fix addresses the segment
> corruption.

## 8.5 — Cut at/just-after refuel airport → replan fails on ~0 fuel

**Symptom (user, 2026-06-04):** cut while a drone was AT / just-LEFT the refuel
airport → replan `route[1] exceeds fuel budget: 11.30 > 0.00` (and D2 budget 0.41).
The drones had refuel available and were mid-mission (not at the END), so they
should refuel and continue.

**Root cause:** the cut-time remaining-fuel calc (`acceptEditsWithCheckpoint`,
`isr.js` ~2353) used **`fuel_budget` as the tank** and, in the post-refuel branch,
computed `fuel_budget − distSinceRefuel`. `distanceTraveled` is whole-mission, so a
drone past the refuel point came out at ~0 fuel even though it had just **refilled to
a full tank**. The replan then posed leg-2 (`refuel→A2 = 11.3`) against ~0 → fail.

**Fix (single-refuel / 2-leg semantics):** remaining fuel = **full tank
(`fuel_capacity`) − distance flown ON THE CURRENT TANK**, with the tank refilling at
the refuel point:
- before refuel (leg 1): `fuel_capacity − currentDist`
- at/after refuel (leg 2): `fuel_capacity − (currentDist − refuelDist)`
Uses the immutable `fuel_capacity`, never the mutable `fuel_budget` (§5). The
existing `dronesAlreadyRefueled` path already clears `refuel_airport` for a
post-refuel drone, so its replan is correctly a **single-leg full-tank trip** to the
end (no second refuel). ⇒ "just left refuel" drone now has ~full tank (16),
`C2-1→A2 = 11.3 ≤ 16` → **flies** (honors the "drone with fuel + reachable end must
fly" invariant). End-of-mission (truly out of fuel, no refuel) still correctly = no
more flying.

> Single-refuel only for now (N=2 legs); multi-refuel later — the tank-resets-at-
> refuel-point rule generalizes (reset distance-on-tank at each refuel). `node
> --check` clean; awaiting live re-test.

## 8.6 — Refuel solve degrades to floor (never hard-fails) ✅ FIXED (2026-06-05)

**The recurring `route[x] leg exceeds fuel budget: d > 0.00` hard-fail.** Per §5
"a replan can NEVER be worse than the existing solution and NEVER hard-fails." The
refuel solve was **all-or-nothing**: with no feasible two-leg split (near-empty
diamond / unreachable added targets) it (a) `_solve_two_leg_refuel` returned a
**fabricated `distance:0` route still carrying `refuel_airport`**, and (b) the "ANY
refuel" greedy stored candidates **without per-leg feasibility**; the validator then
rejected → `success=false` → frontend discarded everything → "Replan failed".

**Fix (solver-only, one concept = "refuel solve never returns infeasible"):**
- `_solve_two_leg_refuel` no-feasible-split fallback → now **degrades to a single-leg
  `_solve_one_leg` fly-to-end** (whatever targets fit, down to zero), returns it with
  **NO `refuel_airport`/leg fields** → validates as a plain single leg.
- Both "ANY refuel" greedy blocks (`solve_mission` + `solve_mission_with_allocation`)
  now only keep a candidate when **both legs fit their own budgets** (`d1 ≤
  leg1_budget`, `d2 ≤ leg2_budget`); if none feasible at any airport → same
  single-leg floor.
**Chosen over frontend/validator guards** because the invariant is a property of the
*solver output* — guaranteeing it in one place removes the need for downstream guards
(which would duplicate the "floor" concept and invite drift). Single-leg solver
already degrades gracefully (drops targets that don't fit), so the floor reuses it.

**Invariant test (locks it):** `tests/test_refuel_floor_invariant.py` — zero-fuel
refuel solve returns a feasible single-leg route (no `refuel_airport`, no fabricated
legs); ample-fuel still refuels (no regression). `pytest` 15 passed.

> Awaiting live re-test of the original scenario (3 cuts, last just before end of
> leg 2 on `isr_env_20260602-0444_N1_1.json`).

## 8.7 — First cut mislabeled C2 (double-increment) ✅ FIXED (2026-06-05)

**Symptom (user, fresh load→import→solve→first cut):** the first diamond was `C2`,
should be `C1`. (Server still received `C1-1`/`C1-2` — so this is a frontend
counter/label issue, separate from §8.8.)

**Root cause:** `state.checkpointSegmentCounter` was incremented in **two** places
that both run during a cut→Done flow — `freezeAtCheckpoint()` (`isr.js` ~10488) and
`acceptEditsWithCheckpoint()` (~2321). 0→1→2 ⇒ first cut labeled C2. Same "one
concept implemented twice" disease.

**Fix:** one increment per logical cut via a claim flag `state._cutCounterClaimed`:
whichever cut function runs first claims & increments; the other reuses. Flag cleared
on **accept-complete** (next cut gets a fresh number) and on **reset/import**;
**not** cleared on discard (a discarded checkpoint re-solves the *same* cut number).
`node --check` clean. (Did NOT alter the restart-from-start bug — that's §8.8.)

## 8.8 — Cut+accept re-flies from the start airport (restart) — RE-PLAN (2026-06-05)

> **Re-plan note:** §8.8 has resisted multiple fixes because the hypothesis kept
> changing (distance frame vs trajectory-prefix vs position-math) and all inference
> was from the SERVER log, which can't distinguish them. STOP guessing. Resolve with
> ONE decisive frontend measurement first.
>
> **Established facts (this session):** (1) backend correct — trajectory starts at the
> diamond C1-1 (-5.7,0.6) ≠ A1 (-5.4,-3.2); route correct; success:true. (2)
> `cutDistance=3.9` used as resume `missionDistance` — and 3.9 ≈ the A1→C1-1 distance
> (~3.8). (3) animation COMPLETES (visits all targets, reaches end) — so it's
> mis-POSITIONED at start + stops partway needing re-animate, not "broken". (4)
> `debugStopAtSplice` is dead code (set ~isr.js:10707, never read). (5) §10 distance
> work targets fuel/refuel-pause precision, NOT this (distance 3.9 is already right).
>
> **The ONE decisive test:** does the ANIMATED `state.routes[did].trajectory[0]`
> equal the DIAMOND or the START AIRPORT?
> - = start airport → H2: the frozen prefix (A1→C1-1) is still in the animated
>   trajectory → fix = store/animate SEGMENT-ONLY trajectory (diamond→end); the
>   prefix is static history only.
> - = diamond → H3: trajectory is fine; the position renderer / advance maps
>   distanceTraveled in a frame where 0 = start-of-mission not start-of-segment →
>   fix the renderer/resume-distance, not the trajectory.
>
> Measure via a temporary frontend debug log (trajectory[0], distanceTraveled,
> diamond coords) or browser console. Then apply the single matching fix. Do NOT
> apply a fix before this measurement.

> **RESOLVED DIAGNOSIS (2026-06-05, via browser-console measurement) — H3 confirmed:**
> `state.routes['1'].route[0] = 'C1-1'` and `trajectory[0] = [32.014,-90.16]` (the
> DIAMOND) — so the trajectory is **segment-only and correct** (NOT H2/prefix).
> But `distanceTraveled = 11.6` — a **whole-mission CUMULATIVE** value applied to a
> **segment-RELATIVE (0-based) trajectory** whose `cumulativeDistances` starts at 0
> at the diamond. → the drone is indexed far past/wrong on the segment trajectory.
>
> **ROOT (the real conflation):** `missionDistance`/`distanceTraveled` is used for
> TWO incompatible purposes: (1) **segment-switch / next-cut / replan-detect** need
> it CUMULATIVE (`checkSegmentBoundary` cmp vs cumulative cutDistance ~isr.js:1133;
> `pendingCutDistance = missionDistance` ~2437/10495; `isCheckpointReplan =
> missionDistance>0` ~10706); (2) **drone POSITION** indexes the segment-only,
> 0-based `cumulativeDistances` and so needs it SEGMENT-RELATIVE. One variable, two
> frames → every prior fix that flipped one broke the other.
>
> **FIX (targeted, principled):** keep `missionDistance` CUMULATIVE (segment logic
> unchanged), but compute drone POSITION from a **segment-relative distance =
> `distanceTraveled − getStartingDistance()`** (the current segment's cumulative
> start offset; `getStartingDistance()` ~1151 = cutDistance of current segment).
> At resume that's `cutDistance − cutDistance = 0` → drone sits IN the diamond;
> grows as it flies. Apply the SAME offset wherever a distance indexes the
> segment-only `cumulativeDistances`/trajectory: the position renderer (~5490),
> the per-frame advance comparison, and the visited-check distance args. Do NOT
> change `missionDistance` itself.

> **⏸️ STOPPED 2026-06-05 (user tired; stop guessing).** Multiple targeted fixes on
> scratch branch `feat/distance-frame-atomic` (commits `3b73d97`, `5af6d4e`,
> `718aec9`, `55e94b3`) did NOT change the symptom: when the new trajectory displays
> the drone snaps to the START airport; on animate it crawls/hesitates/stops, then
> all targets green-check. The §10 distance helpers + the two `distanceTraveled=0`
> position fixes are all isolated on the scratch branch; the WORKING baseline is
> `feat/optimizer-pipeline @ 27b33b6` (untouched).
>
> **Why the fixes aren't landing — the real lesson:** my model of WHICH code path
> sets the drawn drone position is still wrong. The console measurement proved
> trajectory[0]=diamond + distanceTraveled was cumulative; I zeroed the two init
> sites I could find, yet the drone STILL snaps to the start on display. ⇒ there is
> a THIRD positioning influence I haven't found — candidates NOT yet ruled out:
> (1) the route DRAW loop / drone-draw (~isr.js 5420+) computing position from
> `progress` or a different array, not `distanceTraveled`+`cumulativeDistances`;
> (2) the densify/`_fullTrajectory` restore re-merging the prefix back into
> `state.routes[did].trajectory` AFTER my zeroing; (3) `pendingCutPositions` /
> checkpoint-marker drawing overriding the drone position; (4) the position-render
> `length === trajectory.length` guard failing → fallback to `progress` frame.
>
> **FRESH-EYES PLAN (do FIRST, before any more edits):** instrument, don't guess.
> Add ONE temporary debug line in the DRONE-DRAW code (the place that computes the
> circle's on-screen x/y) printing: did, distanceTraveled, segmentStartOffset,
> cumulativeDistances[0], cumulativeDistances.length vs trajectory.length, and the
> computed droneX/droneY vs trajectory[0]. Reproduce once. That print — from the
> ACTUAL draw path — will show which frame/branch positions the drone and end the
> guessing. Only then fix. Consider whether to abandon the scratch branch and redo
> from `27b33b6` once the true cause is known.

**Symptom:** after a single cut+accept, pressing Animate sends the drone BACK to the
start airport and replays the whole mission instead of resuming from the diamond.
**Agreed intended behavior:** *resume from the diamond* — Animate flies only the new
segment (C1-1 → end); the flown part (A → C1-1) is static history, not re-flown;
animation starts at the cut distance, not 0.

**Root cause (traced, read-only):** backend is correct (trajectory starts at C1-1).
Frontend, two compounding parts:
1. `applyDraftSolutionToUI` display-merges the frozen prefix (A→C1-1) + new segment
   into `state.routes` (~`isr.js:10225-10239`), then the auto-optimize / insert-missed
   step **copies that merged trajectory BACK into `draftSolution.routes`**
   (~`isr.js:10130`/`10137`). Accept then stores it un-truncated (the checkpoint guard
   `isr.js:2697` trusts it "starts at the synthetic start," which it no longer does),
   and the live-cut accept path returns (~`isr.js:2985`) **without** rebuilding via
   `buildCombinedRoutesFromSegments`. ⇒ stored/animated trajectory includes A→C1-1.
2. Start distance: a single cut lands the replan at segment index 0, so
   `getStartingDistance()` returns 0 (`isr.js:1153`) ⇒ animation starts at distance 0
   of that merged trajectory = the start airport.

**Refined diagnosis (user, 2026-06-05):** the drone circle ends up **behind the
diamond by ~the start→diamond distance** — as if it flew diamond→start→back. This is
a **distance-0 origin inconsistency**: the animated trajectory was extended *backward*
to include the frozen prefix (start→diamond), but the resume distance is **0** (a
single cut lands the replan at segment **index 0**, and `getStartingDistance()`
returns 0 for index 0 — `isr.js:1153`). So distance-0 = the start airport, and the
drone walks forward from there. **Correct model: the diamond IS the segment origin
(distance 0).**

**Agreed fix (resume-from-diamond + keep prefix as static history):**
1. **Animated trajectory = segment-only** (diamond→end). For a checkpoint replan,
   do NOT fold the frozen prefix into `state.routes[did].trajectory` / the stored
   draft. The copy-backs at `isr.js:10138`/`10145` currently overwrite the draft with
   the merged display trajectory — must keep segment-only there.
   `applyDraftSolutionToUI`'s merge (`~10199-10247`) is for DISPLAY only and must not
   become the animated/stored route.
2. **Frozen prefix → static history layer.** Capture start→diamond into the EXISTING
   (but dormant) `_frozenTrajectories` store (`setFrozenTrajectory`, `isr.js:635`) at
   cut time, and **draw it** in the trajectory render loop (`isr.js:5380-5413`) as a
   static (e.g. solid/dimmed) line — currently `setFrozenTrajectory` is never called
   and `_frozenTrajectories` is never drawn.
3. **Resume distance = diamond.** With segment-only trajectory, distance 0 = diamond
   naturally; ensure the resume path uses the segment-only traj so the index-0 →
   distance-0 case lands the drone IN the diamond, not at the start airport.

> Status: diagnosed + planned (4 change points across accept/apply/animate/draw).
> Higher-risk multi-point change in the 328KB isr.js.

**Engine reality (traced 2026-06-05):** the animation is a **single global cumulative
distance over a MERGED trajectory** — the drone is positioned by `missionDistance`
(set to `cutDistance` on resume) walked along the merged route. Segment switches /
lost-drone stops / replay all depend on this. So the full "segment-only + frozen
history layer" rework is **high-risk** (would break the cumulative segment-switch
machinery). Chosen instead: the **minimal fix** targeting the actual symptom.

**Minimal fix — ✅ APPLIED (2026-06-05).** Found a concrete code bug: the
`_fullTrajectory` restore in `startAnimation` (`isr.js` ~10664) had the comment "BUT
NOT if this is a checkpoint replan" but **NO guard** — so on a checkpoint-replan
resume it overwrote the spliced segment trajectory (diamond→end) with the **full
ORIGINAL start→end trajectory**, sending the drone back toward the start airport
(matching the user's "ends up behind the diamond by ~the start→diamond distance").
Added the missing guard: skip the `_fullTrajectory` restore when resuming a
checkpoint replan (current segment index > 0, or pending cut distance set). The
merged-trajectory engine and `missionDistance = cutDistance` resume are unchanged.
`node --check` clean. Awaiting live re-test (drone must resume IN the diamond).
If the symptom persists, the secondary angle is the resume-distance value
(`getStartingDistance` returning 0 at segment index 0) — verify the live
`🎬 Mission starting at distance: X` log.

**Follow-on (2026-06-05): cut near end → `fuel_budget: 0` → hard-fail.** After the
resume fix, cutting just before the end airport gave `route[1] exceeds fuel budget:
4.16 > 0.00` (D1) and `route[2] contains unknown waypoint id: "-"` (D2). Trace of the
3 cuts showed remaining fuel **monotonically decreasing 11.5 → 4.8 → 0** because the
cut computed remaining = `fuel_capacity − cumulative_distance_flown`, which rounds
toward 0 near the end (the drone legitimately used most of its tank). **But the cut
sits on an already-feasible trajectory — the drone provably has ≥ enough to finish
its committed remaining route; a diamond never creates a deficit (user invariant).**
**Fix:** clamp the cut's remaining fuel UP to `committedRemaining = totalDist −
currentDist` in both branches (`isr.js` ~2357). So the replan never sees a deficit
and the `> 0.00` hard-fails become impossible by construction. The D2 `"-"` waypoint
leak was a downstream consequence of the `fuel_budget: 0` (flexible-end floor going
degenerate); expected to resolve with the clamp — confirm live. `node --check` clean.

## 8.9 — ⏸️ PAUSED: cut/resume distance-frame mismatch (too many interacting fixes)

**Paused 2026-06-05** at the user's call — the §8.5–§8.8 cut/resume fixes keep
spawning the next failure because they patch a **distance-bookkeeping model that has
a frame mismatch at its core**, instead of fixing that model once. STOP adding fixes
here until the frame model is pinned with a clear head.

**Symptom (consistent):** cutting in/near the **LAST segment** fails — alternately
`route[x] exceeds fuel budget: d > 0.00`, `unknown waypoint id: "-"`, and/or the
drone re-flies from the start airport. First cut works; later cuts (esp. final leg)
break.

**Core suspicion (the thing to verify before ANY further edit):** the animation
mixes **two distance frames**:
- `state.animation.missionDistance` / per-drone `distanceTraveled` = **cumulative**
  (set to `getStartingDistance()` = cutDistance on resume).
- `totalDistance` = length of `state.routes[did].trajectory`.
Whether `distanceTraveled = missionDistance` is correct depends on whether
`state.routes[did].trajectory` is the **MERGED** (start→end, cumulative) or
**SEGMENT-ONLY** (diamond→end) trajectory. The engine was built MERGED/cumulative;
the §8.8 fix (commit `e915193`, stop restoring `_fullTrajectory` on checkpoint
resume) likely left a **2nd cut** with a segment-only trajectory while
`missionDistance` stayed cumulative → mismatch → wrong position (back-to-start) AND
wrong fuel (committedRemaining `totalDist−currentDist` collapses).

**Recent commits in this area (candidates to review/possibly revert when resuming):**
`9590b2e` (committed-route fuel clamp), `e915193` (skip _fullTrajectory restore on
resume), `0c69a3d` (C1 counter), `dfcf7c4` (refuel floor), `b088a08` (refuel resets
tank). §8.6/§8.7 fixes (refuel floor, C1 label) seemed good in isolation; the
distance-frame ones (§8.8 + clamp) are the suspects.

**Resume plan (no edits yet):** (1) write down, per scenario (fresh / 1st cut / 2nd
cut / last-leg cut), which frame each of `missionDistance`, `distanceTraveled`,
`totalDistance`, and `state.routes[did].trajectory` is in; (2) find the single
inconsistency; (3) decide ONE coherent model (likely: keep the engine fully
MERGED/cumulative, or fully segment-relative — not mixed) and fix once; (4) consider
reverting the interacting patches first. NO live changes were made during the
investigation — tree clean at `9590b2e`.

## 8.10 — 3rd-cut regression: A1-rooted 13-pt route + cumLen≠trajLen (investigation)

**Symptom (2026-06-05):** 1- and 2-cut now work (fuel per-leg, resume-from-diamond,
frozen history, stats all good). On the **3rd cut** (4 segments, current=3) it breaks:
on Animate the drone draws at A1 and the route is the FULL A1-rooted combined route.

**Evidence (the two 🔎 lines + segment table):**
- Draft display (correct): `🔎 @(32.059,-90.067) cut=(32.059,-90.067) cpActive=true`,
  draft route `8pts` diamond-rooted (`📎 D1: solver trajectory 8pts`).
- Animate (broken): `🎬 D1: 13 pts, route: A1-T17-T16-T28-A2` (A1-ROOTED, 13 pts),
  `🔎 @(32.014,-90.162)=A1, distTrav=0, segOff=14.47, cumLen=8 trajLen=13`.
- Segment table: seg3 cutDist=31.3, but `segOff=14.47` = seg2's cutDist.

**Two concrete defects identified (read-only trace):**
1. **`segmentStartOffset` is the WRONG segment's cutDistance** (14.47 = seg2, should
   be 31.3 = seg3). `getStartingDistance()` (`isr.js:1191`) reads
   `getCurrentSegment().getCutDistance()`, so `_currentSegmentIndex` is one behind at
   animate time for the 3rd cut.
2. **The animated route became the A1-rooted 13-pt COMBINED route** (not the
   segment-only 8-pt draft), AND **`cumLen(8) ≠ trajLen(13)`** → the position
   renderer's `cumulativeDistances.length === trajectory.length` guard (`isr.js:~5493`)
   FAILS → falls back to the `progress`-frame branch → wrong position. The live-cut
   accept branch (`isr.js:~3000-3028`) does NOT call buildCombinedRoutesFromSegments
   and keeps state.routes segment-only — so SOMETHING between accept and animate
   replaced it with a combined A1-rooted trajectory whose cum array wasn't rebuilt to
   match (length 8 vs 13).

**Root tension (confirmed):** the engine has a **merged/cumulative replay** model
(`buildCombinedRoutesFromSegments`, A1-rooted, `_currentSegmentIndex` walking) AND the
new **segment-relative resume** model (segment-only, distance-0, segmentStartOffset).
They coexist OK for 1-2 cuts but COLLIDE at 3+ (wrong segOff + a combined route paired
with a segment-length cum array → guard fail → progress fallback).

**Next (decide before fixing):** (a) find what replaces state.routes with the
combined A1-rooted route between the live-cut accept and animate (and rebuilds/doesn't
rebuild its cum array), and (b) why `_currentSegmentIndex` lags by one at animate.
Likely fix = make the live-cut REPLAY also use the per-segment-relative model
consistently (each segment drawn from its own diamond, segOff = that segment's
cutDistance), OR rebuild the cum array whenever the combined route is set so the guard
holds. Do NOT patch blind — confirm the state.routes-replacement site first.

## 9. De-duplication Effort — "one concept → one function → one constant"

**The real problem (user, 2026-06-04):** the same logic lives in multiple places, so
a change in one spot doesn't show in another — "I think I am working on multiple
locations for this code… It is a mess." This corrupts dynamic re-solves (e.g.
targets falsely crossed → frozen out → bad solution).

**Operating principle.** Every duplicated decision is collapsed to a **single named
function** + a **single shared constant**; all call sites *call it*. A change is then
made in exactly one place and is always respected. (Proven pattern: `get_or_create_mission`,
`upsert_mission`.)

**Process per concept (one at a time, each ends in a commit):**
1. **Map** every duplicate. 2. **Decide** the single definition. 3. **Extract** one
function + constant. 4. **Replace** all sites with calls. 5. **Verify** (tests + live)
and **commit**. 6. Next concept.

**Decided definitions:**
- **"Target visited" = reached the target VERTEX** (the route point that *is* the
  target) within ONE tight shared tolerance (vertex-based, not proximity-to-line).

**Concept backlog (de-dup targets):**
1. **`isTargetVisited(...)` — ✅ DONE (2026-06-04).** Single source of truth in
   `webapp/latlng_utils.js` (`isTargetVisited()` + `TARGET_HIT_KM = 0.05`); all **5**
   visited-decision sites in `isr.js` now call it (was 50/100/150/500 m + `EPS_KM`).
   **Root cause found & fixed (NOT patched):** the animation **resampled** the
   trajectory onto a fixed grid (`startAnimation`, ~line 10760), discarding the
   original route vertices — including the **target** vertices — so a tight tolerance
   missed targets, and a **500 m radius had been bolted on to compensate** (the patch
   the user wanted gone). Rewrote the resampler to **densify** (insert intermediate
   points) while **preserving every original vertex exactly**, so each target stays a
   vertex. Now visited = drone reached the target's vertex within 50 m, uniformly.
   Verified: unit tests show isTargetVisited fires AT the vertex (not before, not for
   off-path targets) and the densifier preserves the target vertex while still
   smoothing (3→201 pts). `node --check` clean.
2. **Trajectory build/standalone** — audit for duplicate trajectory construction so the
   two-vertex definition is enforced in exactly one place (generation already conforms;
   check frontend/`applyResult` copies).
3. **Per-leg fuel budget** — leg1=start fuel / leg2..N=full tank, in one place
   (partially done §8.2; ensure every refuel path + the N-leg future use it).
4. **SAM-aware distance** — audit that EVERY distance/feasibility comparison routes
   through the SAM-aware matrix (invariant #1); kill any Euclidean shortcut.
5. *(add others as found)*

### 9.1 — END STATE: a definitions-unification refactor (user, 2026-06-04)

The de-dup campaign's destination is an explicit **refactor whose goal is that all
parts of the code use the exact same definition for every item** — the canonical
invariants (§5) each implemented once and referenced everywhere. This is the
*systematic* completion of the per-concept work above: instead of fixing one concept
at a time as bugs surface, sweep the whole codebase so no concept has a second
implementation that can drift. Aligns with the planned modular refactor
(`docs/architecture/MODULAR_ARCHITECTURE.md`) and weekend TODO item #4. Approach when
undertaken: for each §5 invariant → locate ALL implementations → collapse to one
shared function/constant → replace all call sites → assert the invariant in tests.

---

## 8. Known Issues / Bugs To Fix

### 8.3 — Replan hard-fails when leg 1 can't even reach a refuel airport

**Symptom (user, 2026-06-04, after the §8.2 leg-2 fix):** error moved to **leg 1**:
`route[1] leg 1 exceeds fuel budget: 8.95 > 1.77`. The drone was cut late with only
**1.77 km** of fuel; the nearest refuel airport is **8.95 km** away — so **no leg-1
route exists** (even the empty diamond→refuel hop exceeds the budget).

**User's hypothesis (likely correct):** "when there is only enough fuel to go back to
the airport without visiting a target, it fails." I.e. when refueling is impossible,
the system should fall back to a **single-leg, non-refuel trip** (the agreed model:
no-refuel = 1-leg trip) — diamond → end, no targets — not hard-fail.

**Root cause (traced):** when `_solve_two_leg_refuel` finds no feasible split
(`solver_bridge.py` ~1007–1019) it returns a fabricated route with
`distance: 0.0`/`leg1_distance: 0.0` (wrong — masks the real 8.95), which downstream
recomputes and the validator rejects → `success=False`, hard fail. The refuel path
is **forced** even when refueling can't physically work; there is **no automatic
fall-through to the single-leg (no-refuel) solve.**

**Fix direction (proposed, pending confirmation + the exact live numbers):** if the
refuel solve yields no feasible leg-1 (can't reach any refuel airport on the fuel at
hand), **fall back to a single-leg solve** start→end on the available fuel (visiting
whatever targets fit; worst case the bare diamond→end "limp home"). Only if even
that bare route exceeds fuel is it a genuine "stranded" report — and even then,
prefer keeping the prior solution over a hard fail (§8.2 model: a replan can't be
worse than finishing the remaining route).
> Status: **ROOT CAUSE FOUND & FIXED (2026-06-04)** — it was NOT a limp-home/stranded
> case. The per-leg-budget concept was implemented in **TWO** places and only the
> solver was fixed (§8.2); the **validator** `verify_solution_basic` (`main.py` ~438)
> still checked **both legs against one `fuel_budget`** (the reduced remaining), so it
> rejected a leg-2 that is feasible on a full tank (`leg 2: 13.49 > 10.40`). Fixed the
> validator to apply the same per-leg rule: **leg 1 vs `fuel_budget`, leg 2 vs
> `fuel_capacity` (full tank)** from `drone_configs`. Both call sites already pass
> `drone_configs`. Verified: leg2=13.49 vs full-tank 15 → no error; leg2=16 > 15 →
> errors; `pytest` 11 passed. **Textbook §5 disease:** one concept, two
> implementations, fix one → the other still bites. Awaiting live re-test.

### 8.2 — Dynamic replan fails entirely when refuel greedy picks an infeasible leg

**Symptom (user, 2026-06-04, env `isr_env_20260602-0444_N1_1.json`):** existing
solution uses most of the fuel; user adds targets mid-mission + Done → replan hangs
with `route[1] leg 2 exceeds fuel budget: 10.52 > 7.12` and **no solution at all**.
Expected: keep the old solution, or use whatever new targets are reachable for more
priority — not hard-fail.

**Root cause (traced, NOT a patch target):** in `solver_bridge.py` the "ANY refuel"
greedy scan (~1591–1664) picks the refuel airport by **max targets, then min total
distance** — but **never checks that each leg individually fits `fuel_budget`.** In
the failing case the post-cut budget is `7.12`; refuel-at-A1's **leg 2 is the bare
A1→A2 repositioning around a SAM = 10.52 km**, which alone exceeds the budget. No
refuel airport yields a feasible leg pair, yet the greedy loop still records the
least-bad **infeasible** route as `best_greedy_sol` (the comparison at ~1618 looks
only at points/distance, never feasibility). The downstream validator
(`verify_solution_basic`, `main.py` ~446) correctly rejects it → `success=False`
with empty routes → the whole replan fails. **Two defects:** (1) greedy accepts
infeasible legs; (2) no graceful fallback when refuel is infeasible.

**REFRAME (user, 2026-06-04) — the real bug is upstream; §8.2 is a symptom.**
The user's model of a **dynamic replan** (authoritative definition):

1. **The diamond (cut point `Cx`) IS the start location** of the replan — the drone
   begins the new solve *from the diamond*.
2. **The frozen (already-flown) trajectory DOES NOT EXIST** for the replan — it is
   off the table. Only the **airports** remain available (for landing/refuel).
3. **Refuel uses the agreed leg/virtual-drone definition** (`D1_L1` / `D1_L2` as
   separate drones/trajectories — §5).
4. **"Solve" means FULLY solve** = find the solution **and optimize it**: run the
   full post-optimizer pipeline — **Insert** (missed targets), **Swap** between legs,
   **Remove crossings** within a single leg.
   ⇒ A dynamic replan is therefore **identical to a fresh full solve**, just with the
   **diamond as start** and the **frozen path erased** (airports kept). No special case.

**Key logical point:** it is **NOT possible for a replan to "fail to beat the
budget" — it already has.** The drone is at the diamond on a feasible remaining
path. The worst case is simply **completing the original remaining route with no new
targets** — always feasible. So a correctly-set-up replan must NEVER hard-fail on
fuel.

**⇒ Real root cause to fix:** the replan is being **set up wrong**. In the failing
log the orienteering solver ran with `start_id=A1` and `fuel_cap=7.12`, but the
returned route began at the diamond `C1-1` (`['C1-1','T16','T17','A1','A2']`) and
leg 2 was the bare `A1→A2 = 10.52` repositioning hop — impossible from the start.
That means the replan's **start point / budget / leg structure don't correspond to
the diamond as origin.** Fix the SETUP (diamond = start, frozen erased, full
pipeline) so an infeasible problem is never posed — rather than patching the greedy
selector or adding fallbacks. (The greedy per-leg-feasibility gap at ~1618 is still
a latent defect worth closing, but it's not the primary fix.)

> Status: **FIXED (2026-06-04).** In `solver_bridge.py`, refuel solves now use
> **per-leg budgets**: leg 1 = `fuel_budget` (fuel at the leg-1 start — remaining on
> a dynamic replan), leg 2 = **full tank** (`fuel_capacity`). Applied in both
> `solve_mission` and `solve_mission_with_allocation` (greedy "ANY refuel" paths)
> and in `_solve_two_leg_refuel` (explicit-split, greedy>MAX, and exhaustive paths,
> incl. the `d2 > budget` check now `d2 > leg2_budget`). `leg2_budget` derives from
> `drone_cfg.fuel_capacity` (callers already pass `drone_cfg=cfg`). Import clean;
> `pytest` 11 passed. **Awaiting live confirmation** on the failing env. Generalizes
> to N legs (see §5 refuel model: only leg 1 uses the start fuel; every post-refuel
> leg = full tank).

**TRUE ROOT CAUSE (user-corrected, 2026-06-04) — per-leg budget bug, NOT a
feasibility/fallback issue.** Per the agreed refuel model: **refuel = a fresh full
tank + a new virtual leg-drone**. Each leg is its own drone with its **own budget**:
- **Leg 1** (diamond → refuel airport): budget = the drone's **remaining** fuel
  (partial tank it has *now*).
- **Leg 2** (refuel → end): budget = a **FULL TANK** (`fuel_capacity`) — refueling
  resets the tank.
Both legs are then a **full solve** (Insert / Swap-between-legs / Remove-crossings),
exactly like a normal (non-dynamic) refuel solve.

**The bug:** `solver_bridge.py` ~1594 & ~1605 passes the **same `fuel_budget` to
BOTH `_solve_one_leg` calls.** On a dynamic replan that value is the *reduced
remaining* fuel (log: `fuel_budget=12.23`/`7.12`), while the config also carries
`fuel_capacity=15`/`16` (the full tank). So **leg 2 is wrongly checked against the
remaining budget instead of a full tank** → `A1→A2 = 10.52 > 7.12` → a genuinely
feasible refuel route is rejected → replan hard-fails.

**Fix:** give leg 2 the **full-tank budget** (`fuel_capacity`), not the reduced
`fuel_budget`. Leg 1 keeps the remaining budget. (Applies to both "ANY refuel"
greedy and the specific-airport `_solve_two_leg_refuel` path — audit both.) NOT a
fallback/feasibility-skip change (my earlier proposal was wrong — it would reject a
leg that is actually feasible on a full tank). Frontend setup (diamond=start,
remaining `fuel_budget`) is correct and unchanged.

### 8.1 — Target marked "crossed" while the drone is still far away (animation)

**Symptom (user, 2026-06-04):** during animation a target gets marked visited
(green X) when the drone is some distance from it — the pre-unification behavior,
even though the trajectory definition was unified.

**Root cause (traced):** the trajectory *definition* was unified at **generation**
(two-vertex direct line), but the **frontend "visited" detection was never unified
to match.** `webapp/isr.js` marks a target visited by a **proximity-to-trajectory**
test, with **5 inconsistent thresholds** across 6 call sites instead of "drone
reached the target vertex":

| Site (isr.js) | Threshold | Context |
|---|---|---|
| ~2411 | `minDist < 0.1` km (100 m) | cut-time |
| ~10588 | `minDist < 0.1` km (100 m) | animation |
| ~11220 | `EPS_KM = 0.5` km (**500 m!**) | live drone position |
| ~11691 | `minDist < 0.05` km (50 m) | segment switch |
| ~11948 | `minDist < 0.15` km (150 m) | animation |

The 500 m live-position check (`EPS_KM` at ~11190) is the most likely culprit for
"crossed while far away." All sites use closest-trajectory-point proximity rather
than vertex arrival.

**Fix direction (proposed, NOT yet applied):** make "visited" uniform and
vertex-based — a target is visited when the drone reaches the **route vertex** for
that target (the trajectory point that *is* the target), within one tight shared
tolerance (e.g. a single `TARGET_HIT_KM` constant). Collapse the 5 thresholds into
that one constant; prefer arrival-at-vertex over near-the-line proximity.

> Status: **FIXED (2026-06-04)** via §9 concept #1 — see there. Root cause was the
> animation trajectory **resampler discarding target vertices** (the 500 m radius was
> the old patch); fixed by a vertex-preserving densifier + the single
> `isTargetVisited()` (50 m, vertex-based). No patch. Awaiting live confirmation.

---

## 7. Implementation Plan (how to build it, and where to begin)

### Key seams found in the current code

- **The Agent is explicitly blocked during animation today.** `sendAgentMessage()`
  in `webapp/isr.js` returns early when `perms.isAnimating` ("Agent Solve disabled
  during animation. Pause or stop first."). Enabling the Agent in the dynamic
  mission *begins by changing this guard.*
- **The DONE/cut machinery already exists.** `acceptEditsWithCheckpoint()` in
  `webapp/isr.js` stops animation, captures per-drone cut positions + remaining
  fuel, creates a checkpoint segment, and re-solves the un-frozen remainder.
- **Constraints are parsed per-turn and discarded.** `run_multi_agent_v4()` calls
  `parse_constraints(user_message, …)` (`server/agents/isr_agent_multi_v4.py`),
  uses the result for that one solve, and never persists or accumulates it. This is
  the §1e ledger gap.
- **`mission_id` is an ephemeral auto-uuid in RAM** (`MISSION_STORE`), with no name
  and an implicit (hash-mismatch) end. This is the §1a-0 gap.

### Dependency order (build spine)

```
§1a-0 Mission (identity + explicit lifecycle + owns env)
        │  enables
        ▼
§1e Per-mission constraint ledger (durable, structured, reasoned-against)
        │  powers
        ▼
§1c/§1d Agent SEND in the dynamic mission (== Editor DONE + condition)
```

### Phased plan

**Phase 0 — Mission as a first-class object (§1a-0). _Start here._**
- Add an explicit **Create / Name** and **End Mission** action (officer-declared
  name/number). Stop minting a new `mission_id` on env/cfg-hash mismatch — edits
  stay inside the same mission until End.
- Make the Mission **own the environment**; persist the mission record to Supabase
  (today `MISSION_STORE` is RAM-only). Reuse `mission_ledger` tables; revive the
  `mission_name` field (currently a hardcoded no-op).
- *Why first:* both the ledger and the SEND flow need a stable, durable mission to
  attach to.

**Phase 1 — Persistent constraint ledger (§1e, capabilities #1–#2).**
- On each Agent turn, take the already-parsed `ConstraintProgram` and **append** it
  to a per-mission ledger (durable via `agent_runs.hard_constraints` / a new
  ledger table) instead of discarding it.
- Add the missing **landing/airport constraint type** ("never land at Ax") to
  `server/memory/constraints.py` + the parser.
- Feed the **accumulated active ledger** (not just this turn's message) into the
  solve, and into the agent's context for recall.

**Phase 2 — Contradiction / advisory reasoning (§1e, capability #3).**
- Before solving, check the new request against the active ledger; on conflict
  (e.g. "land at A4" vs. active "never land at A4"), **surface the contradiction
  and offer an override** rather than silently obeying.

**Phase 3 — Agent SEND in the dynamic mission (§1c/§1d).**
- **Relax the animation guard** in `sendAgentMessage()` so SEND is allowed during
  animation.
- Wire SEND to invoke the **same checkpoint/freeze path as DONE**
  (`acceptEditsWithCheckpoint()`-equivalent), but feeding it (a) any staged
  geometry edit and (b) the typed condition → re-solve un-frozen remainder under
  the condition → Accept → continue animating.
- Apply §1d behavior rules: condition mandatory; infeasible → best-effort +
  explanation (accept/reject); 0-fuel drone → red diamond + notification.

> **Recommended starting point:** **Phase 0** — give the Mission an explicit
> identity, lifecycle, and ownership of the environment. It's the smallest change
> that unblocks everything else, and it's mostly *wiring + persistence* on top of
> infrastructure (`mission_ledger`, `MissionRecord`) that already exists.

### Phase 0 — detailed task breakdown

**Current code facts this builds on:**
- `mission_id` is minted in **6 places** in `server/main.py` via
  `f"m_{uuid4().hex}"` (lines ~1154, 1157, 1367, 1370, 2161, 2312), including the
  hash-mismatch fork at ~1148–1158.
- `MISSION_STORE` (RAM-only dict) is **written in 3 places** (~1269, 1549, 2317);
  `MissionRecord` (`server/agents/mission_store.py`) has **no** `name` or
  `status`/`ended` field.
- `mission_ledger.py` has rich Supabase helpers but **no mission-level
  persistence**; `create_mission_run(...)` is a legacy **no-op**. An adjacent
  layer (`server/database/session_storage.py:save_mission_plan`,
  `server/memory/session_store.py`) also exists and may be the cleaner home.
- Frontend holds `state.missionId` (`webapp/isr.js`), set from the solve response
  at ~12848, reset to `null` in several places (~4272, 7779, 7899, 10289).

**Tasks (suggested order):**

**Persistence layer — DECIDED.** Build the Mission's durable home on the
**`session_storage.py` model** (`sessions` → `conversations` → `mission_plans`),
**not** `mission_ledger.py`. Rationale:

| | `mission_ledger.py` | `session_storage.py` (+ `session_store.py`) |
|---|---|---|
| Purpose | analytics/telemetry per solve | session **state** + history |
| Grain | **one row per agent run** | **one row per session**, w/ child conversations + plans |
| Live today? | ✅ called from `main.py:2197` | ❌ exported but **zero live callers**; `session_store.py` abstraction never instantiated (dormant) |

The §1e container (identity + recall + solutions + future ledger, accumulating
across turns) maps almost 1:1 onto `sessions`≈**Mission**, `conversations`≈recall,
`mission_plans`≈solutions. `mission_ledger` is **run-grained** — the opposite of
"one mission across many solves" — so forcing the Mission into `agent_runs` fights
its grain. **Keep `mission_ledger` as-is for per-run decision traces;** the Mission
record just holds the `mission_id` those runs reference.

> **Caveat (honest):** `session_storage`'s *model* is right, but its *code is
> unproven* — no live callers, and `session_store.py` (with `InMemorySessionStore`
> fallback + `SupabaseSessionStore` + optimistic concurrency) is fully written but
> never wired in. So Phase 0 is **"activate dormant-but-well-designed code,"** not
> "reuse a working path." Prefer activating `session_store.py` over hand-rolling —
> its `InMemorySessionStore` is the drop-in replacement for today's RAM-only
> `MISSION_STORE`.

> **Schema check — DONE.** The tables **are defined** in
> `docs/architecture/supabase_schema.sql`:
> - `sessions` (id, created_at, updated_at, **metadata JSONB**) → the **Mission**
> - `conversations` (session_id, role, content, …) → **recall** (ready as-is)
> - `mission_plans` (session_id, plan_data, total_fuel/points, drone_count, …) →
>   **solutions** (ready as-is)
>
> **But the `sessions` table is conversation-oriented, not mission-oriented** — so
> Phase 0 needs a **small additive migration** (not a from-scratch one). Missing for
> §1a-0:
> - `name` (mission name/number) — could hide in `metadata` JSONB but weak for
>   querying; prefer a real column.
> - `status` (`active` | `ended`) and `ended_at` — for the explicit lifecycle.
> - `env` / `drone_configs` (Mission **owns the environment**) — new column(s) or
>   `metadata`.
>
> **Residual unknown (cannot verify from the repo):** whether this schema has
> actually been **applied to the live/deployed Supabase** instance. The `.sql` file
> proves design intent, not that it was run against prod. Confirm via DB access
> (`\dt`) before relying on durability; until then `InMemorySessionStore` keeps
> Phase 0 working without a DB.

**Migration — WRITTEN.** See `docs/architecture/migrations/001_sessions_to_missions.sql`
(idempotent `ADD COLUMN IF NOT EXISTS`; touches only `sessions`).

**Mission ↔ storage column mapping (the 0.1 target):**

| Mission concept (§1a-0) | `sessions` column | `MissionRecord` field | Status |
|---|---|---|---|
| identity (name/number) | `name TEXT` | add `name` | **new (migration)** |
| lifecycle state | `status` (`active`/`ended`) | add `status` | **new (migration)** |
| end timestamp | `ended_at TIMESTAMPTZ` | add `ended_at` | **new (migration)** |
| owns environment | `env JSONB` | `env` (exists) | **new col / maps to existing field** |
| owns drone configs | `drone_configs JSONB` | `drone_configs` (exists) | **new col / maps to existing field** |
| continuity (not identity) | `env_hash`, `cfg_hash` | `env_hash`, `cfg_hash` (exist) | **new cols / map to existing fields** |
| created | `created_at` | `updated_at` (exists) | as-is |
| conversation recall (§1e #1) | `conversations` table | — | as-is |
| solutions | `mission_plans` table | `routes`/`allocation`/`attempts` | as-is (append per accept) |
| per-run traces | `agent_runs` (ledger) | — | as-is; `mission_id` → `sessions.id` |

**0.1 — Extend the data model. ✅ DONE (2026-06-03).**
- Added `name`, `status` (`STATUS_ACTIVE`/`STATUS_ENDED`), `created_at`, `ended_at`
  to `MissionRecord` (`server/agents/mission_store.py`) as defaulted fields (kept
  original positional fields first → backward-compatible), plus `is_active` and
  `end()` helpers. Verified: importable, defaults correct, lifecycle transition
  works. Note: `MISSION_STORE` is currently written as **plain dicts** in
  `main.py`, so `MissionRecord` was documentation-only until now; 0.2/0.4 will make
  callers actually construct it.
- **Still to do in 0.1-adjacent work:** activate `server/memory/session_store.py`
  (`InMemorySessionStore` as the `MISSION_STORE` replacement / `SupabaseSessionStore`
  for durability, backed by the `session_storage.py` tables).

**0.2 — Centralize mission_id creation. ✅ DONE (2026-06-03).**
- Added `new_mission_id()` (single mint source) and `get_or_create_mission(...)`
  (wraps `enforce_mission_continuity`, always returns a usable id) in `main.py`.
  Replaced all scattered `f"m_{uuid4().hex}"` mints — the two solve endpoints now
  call `get_or_create_mission(...)`; the two fallback sites call `new_mission_id()`.
- **Behavior unchanged on purpose:** mismatch still forks a new mission. The single
  decision point is now documented in `get_or_create_mission` with an explicit
  **0.3 note** so flipping the fork is a one-place change. Verified: clean import,
  helper returns fresh id for absent mission_id; `pytest -k "solve or mission"` →
  3 passed, 0 failed.

**0.3 — Stop implicit forking on edit (the core behavior change).**
- At the hash-mismatch site: when an **active** mission_id is present, do **not**
  mint a new mission. Instead update the mission's env/solution **in place** (record
  a new attempt/version), keeping the same mission_id until an explicit End. Keep
  hashes for *continuity bookkeeping*, not for identity.

> **Pre-0.3 dependency investigation (2026-06-03) — fork-on-edit is THREE
> mechanisms, not one. Flipping 0.3 means changing #1 while preserving #2/#3.**
>
> 1. **Identity fork — the 0.3 target (safe to change).** `get_or_create_mission`
>    mints a new id on mismatch. Nothing downstream needs the id to *change*: the
>    `continuity` flag it returns is only echoed to the frontend, which **never
>    reads it** (0 reads in `isr.js`). Flipping to "keep the id" is safe on the
>    identity axis.
> 2. **Solution-staleness guard — MUST PRESERVE.** `agent_chat_v4`
>    (`main.py` ~2110–2121) sets `mission_id = None` on mismatch **not to fork
>    identity** but to avoid loading a *stale stored solution* as
>    `existing_solution` → "solve fresh, don't continue from old routes." 0.3 must
>    keep this *effect* while keeping the id stable. The two concerns (identity vs.
>    is-the-old-solution-still-valid) currently overload the same `None` sentinel
>    and must be **separated**.
> 3. **Frontend `invalidateMission()` — the real coupling (must change too).**
>    `isr.js` drops `state.missionId = null` on **9 drone-config changes**
>    (`~6807–6883`: fuel, start/end airport, refuel, runway, speed, etc.) with the
>    comment *"so backend will treat next v4 call as a new mission."* The frontend
>    is an **active participant** in fork-on-edit — it pre-emptively forgets the
>    mission rather than waiting for backend detection. This **directly conflicts**
>    with §1a-0 and is the most important change. **Carve-out already present:**
>    during checkpoint workflow with committed segments it **preserves** frozen
>    trajectories (`~4276–4282`) — exactly the dynamic-mission case we care about,
>    so the precedent for "edit without forgetting the mission" already exists.
>
> **Revised 0.3 sub-plan:** (a) backend — in `get_or_create_mission`, if the stored
> mission is `STATUS_ACTIVE`, keep its id and record a new attempt instead of
> minting; (b) backend — split the staleness concern from identity so #2 still
> solves fresh without nulling the id; (c) frontend — change `invalidateMission()`
> to invalidate the *solution* (routes/mode) **without** nulling `state.missionId`
> while a mission is active, generalizing the existing committed-segments carve-out.

> **0.3 part (a) — ✅ DONE (2026-06-04).** `get_or_create_mission` now keeps the id
> of an **ACTIVE** mission across an env/cfg edit (returns the same id,
> `continuity=False`, `mismatch_reason` preserved) instead of minting. Mints fresh
> only when the prior mission is absent / not-found / **ENDED** (no resurrection of
> ended missions). Added `_mission_is_active()` + local `STATUS_ACTIVE/ENDED` in
> `main.py` (its `MISSION_STORE` is a separate dict-based store from
> `mission_store.py`; missing `status` ⇒ treated active). Verified with 5 unit
> cases (absent/clean/active-edit/ended-edit/unknown) + `pytest -k "solve or
> mission"` (3 passed). **Note:** the `/solve` endpoint already overwrites
> `MISSION_STORE[mission_id]` with the new env after solving, so keeping the id
> means the env is **updated in place under the same mission** — no extra wiring
> needed on the solve path. Parts (b) staleness-split and (c) frontend
> `invalidateMission` are **still pending**.

> **0.3 part (b) — ✅ DONE (2026-06-04).** In `agent_chat_v4`, the two env/cfg
> hash-mismatch branches no longer set `mission_id = None`. They now **keep the
> mission identity** (active mission survives the edit) while still **dropping the
> stale stored solution** (`existing_solution` stays `None` → agent solves fresh).
> Identity and solution-validity are now separate concerns instead of sharing one
> `None` sentinel. The genuine *not-found* case still nulls `mission_id` (correct —
> mints fresh, matching 0.3a). The edited `env` comes from `req.env`, so the new
> env is used. Verified: import OK, focused split test (match→load+keep,
> edit→keep+drop, not-found→null), `pytest -k "solve or mission or agent or chat"`
> (6 passed). Part (c) frontend `invalidateMission` is **the last 0.3 step** — until
> it lands, the frontend still nulls `state.missionId` on config edits, so
> end-to-end the mission still forks from the client side.

> **0.3 part (c) — ✅ DONE (2026-06-04). TASK 0.3 COMPLETE.** `invalidateMission()`
> in `webapp/isr.js` no longer nulls `state.missionId`. A mid-mission edit now
> invalidates the **solution** (clears routes unless committed segments exist;
> resets mode to IDLE so the user re-solves) but **keeps the mission identity** —
> the backend (0.3a/0.3b) then keeps that id stable and re-solves the new env in
> place. The three *legitimate* `state.missionId = null` sites remain untouched —
> they are the real new-mission boundaries: segment-0 reset (`~7788`),
> `loadFromJson` import (`~7908`), and load-new-environment-from-backend (`~10298`).
> `node --check` passes. **Net effect (a+b+c):** editing the environment/config
> mid-mission no longer forks a new mission anywhere — client or server. A mission
> now persists across edits and ends only by explicit action (the End-Mission UX
> itself is 0.5/0.6).

**0.4 — Mission owns the environment; persist it.** *(Split into 0.4a / 0.4b.)*

> **Finding (2026-06-04):** the 3 `MISSION_STORE[mission_id] = {...}` write sites
> (`main.py` ~1333, ~1610, ~2384) already store `env`/`drone_configs` — so the
> Mission already owns its env *in RAM*. **But each write replaces the whole
> record**, which means a re-solve would **clobber** a mission's `name` / `status`
> / `created_at` (the §1a-0 lifecycle fields). That's a real bug for mission
> persistence and is what 0.4a fixes.

**0.4a — single `upsert_mission()` that preserves lifecycle (RAM, low-risk). _Do
first._**
- Add one `upsert_mission(mission_id, *, env, drone_configs, env_hash, cfg_hash,
  routes, allocation, metrics)` helper; route all 3 write sites through it.
- On write, **merge** rather than replace: carry forward existing `name`,
  `status`, `created_at` if the mission already exists; only update env/solution/
  hashes/`updated_at`. New missions get `status=active`, `created_at=now`.
- Pure RAM, no new deps. Makes the Mission genuinely own its env under a stable
  identity + lifecycle.

> **0.4a — ✅ DONE (2026-06-04).** Added `upsert_mission(...)` as the single
> MISSION_STORE write path; routed all 3 bare-dict writes (`/solve`,
> `/solve_with_allocation`, `agent_chat_v4`) through it. It **merges**: existing
> `name`/`status`/`created_at` are carried forward across re-solves; only
> env/solution/hashes/`updated_at` refresh. New missions start `active` with
> `created_at`. Fixes the clobber bug (a re-solve previously wiped identity/
> lifecycle). Verified: 0 bare writes remain; unit test shows a re-solve after an
> edit preserves `name='Recon Alpha'` + `status` + stable `created_at` while
> refreshing env/points; `pytest` 14 passed.

**0.4b — durable persistence (separate, bigger).**
- Swap the RAM dict for `server/memory/session_store.py`
  (`InMemorySessionStore` dev / `SupabaseSessionStore` prod). Its snapshot model
  already captures `environment`/`drone_configs`/`solution`/`constraint_program`.
  Requires the `sessions` migration (001) applied + the live-table verification
  from §6. Deferred until after 0.4a/0.5/0.6 prove the shape in RAM.

> **⏸️ RESUME POINT — paused 2026-06-04 to handle an unrelated issue.** Pick up
> 0.4b HERE. Pre-work already done (don't redo): traced `SessionStore` — both
> `InMemorySessionStore` and `SupabaseSessionStore` exist + `get_session_store()`
> auto-selects via `is_supabase_configured()`; **no `SUPABASE_*` in local `.env`
> (no live DB locally → real persistence can't be tested here, only the no-op
> fallback path)**; and `SessionStore`'s grain (sessions/snapshots) **differs**
> from `MISSION_STORE` (flat dict keyed by mission_id). **Decision still open**
> (asked, not yet answered): (a) **thin persistence sidecar** — keep RAM as source
> of truth, add a no-op-unless-Supabase mirror to the `sessions` table on
> `upsert_mission`/`end_mission` + startup rehydrate (LOW risk, recommended); (b)
> full SessionStore swap (rewrites the stabilized 0.1–0.6 core; HIGH risk,
> untestable locally); (c) defer until `SUPABASE_*` keys + migration 001 applied so
> it can be built AND tested against a real DB. **Phase 0 is functionally complete
> without 0.4b** — missions just reset on server restart until this lands.

**0.5 — Explicit lifecycle endpoints. ✅ DONE (2026-06-04).**
- Added `POST /api/missions` (create + name → fresh id, ACTIVE),
  `GET /api/missions/{id}` (identity + lifecycle + solution summary; 404 if
  unknown), `POST /api/missions/{id}/end` (set `status=ended` + `ended_at`,
  idempotent). New `MissionCreateRequest` / `MissionResponse` models +
  `_mission_to_response()` projector. Imported `HTTPException`.
- **Bug found & fixed by the endpoint tests:** `get_or_create_mission` would
  **resurrect an ENDED mission** when the incoming env matched (or was empty →
  no hash guard). Added an explicit ENDED short-circuit **before** the
  continuation path: an ended id always mints a fresh mission (`reason="mission
  ended"`). Verified: create/get/end/404/idempotent + both resurrection cases
  (env-changed and env-same) + active-mission regression; `pytest` 14 passed.
- Did not wire the legacy `create_mission_run` name (left as-is; retire later).

**0.6 — Frontend: declare + end a mission. ✅ DONE (2026-06-04).**
- Added a **mission row** to Mission Control (`index.html`): a name/number input
  (`mc-mission-name`), a status badge (`mc-mission-status`, ● active / ■ ended),
  and an **End Mission** button (`mc-end-mission`). Styled in `isr.css`.
- `isr.js`: added `state.missionName` / `state.missionStatus`; `syncMissionUi()`
  (badge + End-button enablement), `endCurrentMission()` (POST
  `/api/missions/{id}/end`), and listeners (name `change`, End `click`).
- The `solve_with_allocation` payload now sends `mission_id` **and**
  `mission_name`; the response's `mission_id` is **adopted into
  `state.missionId`** (previously this endpoint never captured it), so the mission
  persists across edits. Backend `SolveWithAllocationRequest` gained
  `mission_name`, applied via the upsert (preserving an existing name).
- Verified: `node --check` + `import` OK; **end-to-end TestClient flow** — named
  solve persists the name, GET shows it, re-solve after an edit keeps id+name, End
  sets ended, next solve mints fresh (no resurrection); `pytest` 14 passed.
- *Deferred:* a dedicated "New/Declare Mission" button (today a mission is born on
  first solve and named via the field); auditing the 3 legitimate
  `state.missionId = null` reset sites is already correct from 0.3c.

**Phase 0 acceptance check:** create a named mission, solve, animate, make an
Editor geometry edit mid-animation, and confirm the **same mission_id persists
across the edit** (no silent fork) and survives a server restart, ending only on
the explicit End action.

---

## 6. Open Questions / To Be Clarified

- **✅ BRANCH DIVERGENCE — RESOLVED (2026-06-04).** `feat/optimizer-pipeline` was
  12 commits behind `origin/main`, missing the refueling-optimizer fix series
  (virtual-drone splitting `579b7e9`, INSERT per-leg fuel `4f43e93`, refuel_idx
  preservation `beab349`, Auto-Optimize checkbox `bdedf49`, SWAP virtual-leg
  `d9314b0`, iterative optimizer pipeline `ff718e1`). That gap caused the local D2
  refueling drone to auto-optimize worse (`Excluding D2 from optimization`) and
  require a manual Insert — Railway (on `main`) did not have this problem.
  **Fix:** rebased `feat/optimizer-pipeline` onto `origin/main` (5 Phase 0 commits
  replayed cleanly, no conflicts; backup ref
  `backup/optimizer-pipeline-pre-rebase-20260604` at old tip `5eab6ab`). **Verified
  live (user-confirmed):** local is now fully functional — D2 auto-optimizes
  completely, **no manual Insert needed**, matching Railway. NOT a Phase 0
  regression. **Pending:** branch not yet pushed — `origin/feat/optimizer-pipeline`
  still has pre-rebase history, so a force-push (`--force-with-lease`) is needed
  when ready.
- **Mission reload / replay (deferred).** How a saved mission is re-opened and/or
  replayed — restoring its environment, solutions, segments, conversation, and
  constraint ledger. Explicitly deferred per §1a-0; to be designed later.
- **Durable persistence layer — RESOLVED (§7):** use the `session_storage.py`
  model (`sessions`/`conversations`/`mission_plans`) + activate `session_store.py`;
  keep `mission_ledger.py` for per-run traces. Tables **are defined** in
  `docs/architecture/supabase_schema.sql`; Phase 0 needs a **small additive
  migration** to `sessions` (add `name`, `status`, `ended_at`, `env`).
  **Residual unknown:** whether the schema is actually applied to the live Supabase
  (verify via DB `\dt`; the `.sql` proves intent, not deployment).
- **Freeze-line rule (§1a).** Where exactly the freeze boundary falls when a cut is
  taken mid-animation — the drone's exact position at the current frame, or snapped
  to the nearest/next waypoint.
- **Condition persistence across SENDs (§1d).** Resolved in spirit by §1e (the
  per-mission ledger accumulates conditions), but the precise interaction —
  does each SEND re-assert all active constraints automatically — to be confirmed
  when wiring.
- **Mission end semantics (§1a-0).** Explicit "End Mission" action is agreed; the
  exact UX (button/command) and what happens to an in-flight animation on End — TBD.

---

## 10. DESIGN SPEC — Single Distance Ground Truth (the real fix for §8.5–§8.9)

> **Status: SPEC ONLY (2026-06-05). No code written. Approve before building.**
> User's insight: "There has to be a single ground truth (SAM-aware matrix) that all
> parts use, so that when a target is added the matrix is edited and all use the same
> matrix." Every cut/resume bug this session (back-to-start, fuel=0, refuel-pause-
> late, stale segment length) is **one disease**: distances are computed *many times,
> many ways*, and drift.

### 10.1 The disease — audit of current distance derivations (read-only, 2026-06-05)

**`cumulativeDistances` is independently recomputed in ~7 places** in `webapp/isr.js`
(≈ lines 10281, 10751, 10872, 11420, 11559) and read in ~5 more (5490, 11171, 11609,
11902, …). **`totalDistance` (segment length) likewise recomputed ~6 times** (10282,
10752, 10873, 11421, …). **`refuelDistance`** is found ad hoc from `_waypointDistances`
(10947–10952). **Remaining fuel** computed in `acceptEditsWithCheckpoint` (2363–2384).

**Two violations compound it:**
1. **Frame mismatch:** position/fuel mix CUMULATIVE `missionDistance`/`distanceTraveled`
   with per-trajectory `totalDistance`; whether they agree depends on whether
   `state.routes[did].trajectory` is MERGED (start→end) or SEGMENT-ONLY (diamond→end)
   — inconsistent across cuts.
2. **NOT SAM-aware (violates §5 #1):** several frontend cumulative sums use
   **`haversineKm` straight-line** between trajectory points (10287, 10882, 11425),
   NOT the SAM-aware matrix. So even the "ground truth" is bypassed.

**Consequence:** a cut shortens a leg, but each consumer that didn't recompute keeps a
stale distance — e.g. the refuel pause fires at the OLD refuel distance (drone flies
*through* the airport and pauses after), fuel uses a stale `tank − distance`, position
uses a stale cumulative origin.

### 10.2 The model — one ground truth, one derivation

- **GROUND TRUTH = the SAM-aware distance matrix** (pairwise waypoint distances).
  Already the §5 invariant. When geometry changes (add/remove target/SAM), the matrix
  is edited **in one place**; everyone reads it. No straight-line/haversine substitutes
  anywhere.
- **ONE DERIVED ARTIFACT = a "route distance profile"** for a given (route, matrix):
  the cumulative SAM-aware distance at each waypoint of the route. Computed **once** by
  a single function; it yields, for free and consistently:
  - segment length (= profile total),
  - refuel-point distance (= profile value at the refuel waypoint),
  - fuel needed / remaining (= tank − profile-distance-on-current-leg),
  - the drone's position (= interpolate the trajectory at `profile`-distance),
  - segment-switch boundaries.
- **ONE FRAME:** pick a single convention and use it everywhere. Recommended:
  **segment-relative** — each segment's profile starts at 0 at the diamond; the
  drone's `distanceTraveled` is distance-along-the-current-segment; the global
  "mission distance" (for display only) = sum of completed segment lengths + current.
  (Alternative: fully cumulative/merged. The point is ONE, not mixed.)

### 10.3 The single recompute rule

On **any cut or geometry edit**: (1) edit the matrix if geometry changed; (2) recompute
the affected route(s)'s **route distance profile** from the matrix; (3) every consumer
reads the profile — none recomputes its own. A cut that shortens leg 1 → new profile →
refuel point, fuel, position, segment length all update together. This is what makes the
refuel pause land AT the airport, not past it.

### 10.4 Build approach (when approved)

1. Add the single `buildRouteDistanceProfile(route, matrix)` (SAM-aware) — backend or a
   shared util; the matrix already exists.
2. Replace the ~13 ad-hoc `cumulativeDistances`/`totalDistance`/`refuelDistance`/fuel
   computations in `isr.js` with reads of the one profile (§9 "one concept → one
   function" applied to distance).
3. Pick + enforce the single frame (segment-relative recommended); remove the
   merged-vs-segment ambiguity that caused back-to-start.
4. Likely **revert the interacting §8.8 + fuel-clamp patches** (`e915193`, `9590b2e`)
   since they patch the broken model; re-derive their intent from the profile.
5. Re-test the full cut matrix: cut in leg 1 (refuel pause lands AT airport), cut in
   last leg (no fuel>0.00, no back-to-start), 2nd/3rd cut, add-target-then-cut.

> This is §9.1 (the unification refactor) applied to the distance/fuel concept. There
> is **no clean commit to revert to** (user confirmed) — the model itself must be made
> coherent. Do this deliberately, once.

### 10.5 Progress + the frame-conversion lesson (2026-06-05)

- **DONE (kept):** the two profile helpers in `webapp/latlng_utils.js` —
  `buildRouteDistanceProfile` (`8c8d9b3`) and `buildTrajectoryCumDistFromProfile`
  (`6021611`), both pure + unit-tested. These are the foundation; they stay.
- **TRIED + REVERTED:** Step 3 wired ONLY the animation's `cumulativeDistances`/
  `totalDistance` to the matrix-frame profile (`3b4215e`), but left
  `missionDistance`, `getStartingDistance()`/`cutDistance`, and the per-frame
  `distanceTraveled += speed*dt` advance in the OLD (haversine) frame. Result: the
  **position renderer** (`isr.js` ~5490, maps `distanceTraveled` against
  `cumulativeDistances`) and the **visited-check** (also reads `cumulativeDistances`)
  ended up in DIFFERENT frames — visible drone stuck at the start airport while an
  "invisible drone" (the visited logic walking the correct profile distances)
  green-checked targets. Reverted in `70c2bef`.
- **LESSON / RULE for the redo:** the frame conversion must be **ATOMIC** — convert
  ALL of these together or none: (a) per-trajectory `cumulativeDistances` +
  `totalDistance`; (b) the position renderer (~5490); (c) `missionDistance` init +
  `getStartingDistance()`/`cutDistance`; (d) the per-frame advance `distanceTraveled
  += effectiveSpeed*dt` (~11078); (e) the visited-check (~11171/5490 readers); (f)
  the cut's `currentDist`/`totalDist` (~2335). Every variable COMPARED AGAINST the
  cumulative array must be in the same frame. Partial conversion = guaranteed
  mismatch. Recommend doing it on a scratch branch and verifying position + visited
  + refuel-pause together before merge.

> **⏸️ RESUME POINT (paused 2026-06-05).** State is WORKING: profile helpers landed
> (`8c8d9b3`, `6021611`, both unit-tested), Step 3 reverted (`70c2bef`) so position
> renders correctly. Last commit `704c61d`. Next task = the **single atomic frame
> conversion**: convert consumers (a)–(f) above TOGETHER to the matrix frame using
> the two helpers, then live-verify position + target-visited + refuel-pause at once
> on `isr_env_20260602-0444_N1_1.json` (cut in leg 1 → refuel pause AT the airport;
> cut in last leg → no fuel>0.00 / no back-to-start). Do NOT convert any single
> consumer alone. NOT pushed to Railway (still at `b088a08`).

---

## 11. Fuel model — canonical (§5 / §8.8, as implemented on `main`)

The single source of truth for "how much gas." Verified against the code on
`main` as of 2026-06-08 (`61edae3`).

### 11.1 The three quantities (keep them distinct)

- **Tank** = `fuel_capacity` — the FULL tank, immutable for a drone. The stats
  box denominator. A manual edit of the Assets fuel field sets BOTH `fuel_budget`
  and `fuel_capacity` (a user edit = a new tank).
- **Remaining-at-last-cut** = `fuel_budget` — what the SOLVER replans on. A cut
  OVERWRITES it to `tank − fuel-burned-this-leg`; it is sampled only at cuts and
  the initial solve, NOT continuous with time. (This dual meaning is the
  variable-soup the north-star refactor targets — `fuel_budget` is really
  "remaining", not a budget. The orphaned commit `4c66ad3` renamed it but was
  lost; not on `main`.)
- **Consumed-this-leg** = `state.legFuel[did]` — distance flown since the leg
  start. Cut-NEUTRAL: accumulates straight across cuts (detours still count
  toward the same leg); resets to 0 only at a REFUEL crossing.

### 11.2 The leg model

A trip is **1 leg** (no refuel) or **N legs** (refuel). Each leg has a full tank.
A **cut is fuel-neutral** — it does NOT start a new leg. Only a **refuel** starts
a new leg. So legs = refuels + 1, NOT cuts + 1.

### 11.3 The stats box

`Fuel / Budget` = **consumed / (tank × num_legs)**.

- Single-leg: `consumed / tank`, consumed rising 0→leg-end, always ≤ tank.
- Refuel: `⛽ leg1+leg2+…+legN / (tank × N)` — one fuel number per leg,
  `+`-joined. Each leg starts at 0, climbs through its cuts, freezes at the
  refuel that ends it; the next leg then climbs. Only the active leg climbs.
- Lost drone: `💀 tank / tank` (forfeits the full tank).
- Mission row: `Σ consumed (all drones) / Σ budget (all drones)`.

### 11.4 Durable per-leg store (the fix that made it survive cuts)

`state.legFuel[did]` (active leg) + `state.legFuelHistory[did]` (finalized past
legs, an array) live in DURABLE state — NOT in `state.animation.drones`, which
`startAnimation` wipes. `legBurnedDistance` is a live mirror so the solver's
replan reads are unchanged. At a refuel crossing the active leg is pushed into the
history (only if it burned > 0.05 km — guards the phantom 0-leg when the refuel
airport is the START airport), then reset to 0. `legFuelHistory.length` proves a
refuel happened even after a cut clears `refuel_airport`.

### 11.5 Known open fuel-box gaps (display-only)

- Post-solve static box (`updateStatsFromRoutes`) never shows the ⛽ split — only
  the animation renderer does.
- A non-refuel drone whose route got a SPECIFIC `refuel_airport` stamped wrongly
  shows a 2-leg split.
- Continuous full-mission replay shows one field, not ⛽leg1+leg2 (the stitched
  replay route carries only last-segment metadata). Deferred to the Phase-3
  whole-mission-route reconstruction.

---

## 12. Cut / Reset / Replay — behavior (as implemented on `main`)

### 12.1 Cut (checkpoint)

Press **C** during animation → `freezeAtCheckpoint` records a cut at the drone's
current cumulative distance, freezes the flown prefix as static history, and
the post-cut remainder is replanned (`buildCheckpointEnv` → solver). Each cut adds
a segment; segments concatenate for replay. The flown trajectory is SEGMENT-ONLY
after a cut; the frozen prefix is drawn separately as dimmed dashed history.

### 12.2 Distance frame (§10, unified 2026-06-08)

`computeAnimationCumDist` is the single per-trajectory cumulative-distance builder.
It SELF-SELECTS the frame from the inputs: the SAM-aware matrix profile when the
route↔trajectory↔matrix line up (profile total ≥ 0.9× the trajectory's raw
haversine length), else plain haversine over the actual points. The haversine path
is what a stitched whole-mission-replay trajectory needs (its route describes only
the last segment). Callers no longer pass a `forceHaversine` flag.
`distanceTraveled` is segment-relative (resets to 0 each segment);
`segmentStartOffset` holds the global offset; `missionDistance` stays cumulative
for segment-boundary logic.

### 12.3 Visited targets (unified 2026-06-08)

A target is visited iff the drone passed its vertex on the canonical distance
frame (`isTargetVisited`). The green X reads ONLY from `state.visitedTargets`.
There are exactly FOUR writers, each keeping the owner map (`visitedTargetByDrone`)
in sync inline: `clearVisited()`, `markTargetVisitedByDrone(t,d)` (live),
`seedVisitedFromSegment(ids)` (merge stored segment data), `markAllVisited(targets)`
(final accept). No raw writes or scattered owner-rebuilds elsewhere.

### 12.4 RESET

RESET is destructive — it erases ALL cuts/segments and returns to the original
mission as ONE continuous flight. A native `confirm()` warns first, but ONLY when
there is cut work to lose (> 1 segment); reminds the user to export. On confirm:
truncate `missionReplay` to segment 0, restore the seg-0 full solve, clear
visited/fuel/frozen. The drone then flies the whole original mission start→end
without stopping at old cuts. (To keep cut work, export before RESET.)

### 12.5 Replay (two modes, both after a mission completes)

When a cut mission finishes (drone at End), pressing **Animate** replays it.
`state.animation.missionComplete` (set at natural end) triggers the replay; the
**"Replay segments"** checkbox by Animate selects the mode:

- **Continuous (default, unchecked):** flies the WHOLE flown mission start→end as
  one continuous flight — the actual stitched trajectory that was showing at
  completion (frozen history + last segment), NOT the original uncut solve.
  `continuousReplay` suppresses segment-boundary switching; segments are kept so
  the user can still re-cut. Repeatable.
- **Segment-by-segment (checked):** re-flies the cut HISTORY — flies the original
  pre-cut solve from the start, stops at each cut diamond, loads that segment's
  environment + replan, continues. Reuses the imported-mission segment-switch
  machinery (`checkSegmentBoundary`).

A mid-segment **pause** still resumes the current segment (the resume path),
distinct from a completed-mission replay.

---

## Changelog

- **2026-06-03** — Document created with skeleton; awaiting first contribution.
- **2026-06-03** — Added §1 (core create→solve→accept→animate workflow) and §1a
  (Dynamic Mission: mid-animation editing, frozen vs. un-frozen re-solve, today's
  goal of giving the Agent the Editor's dynamic re-planning ability).
- **2026-06-03** — Added cut-point/diamond semantics to §1a (white = normal cut,
  red = drone de-selected at checkpoint), verified against `freezeAtCheckpoint` /
  `lostDrones` in `webapp/isr.js`.
- **2026-06-03** — Added §1b (Run Planner = unconditional solve vs. Agent =
  conditional solve) and §1c (today's agreed scope: Agent in the Dynamic Mission;
  **Agent SEND == Editor DONE** — halt, apply condition, re-solve un-frozen
  remainder, accept, continue).
- **2026-06-03** — §1c: clarified division of labor — geometry edits are
  **Editor-only**; Agent owns **conditions**. DONE and SEND both re-solve at a cut
  point and **compose** (Editor DONE first for geometry, then Agent SEND to layer a
  condition; or SEND alone on the unchanged environment).
- **2026-06-03** — §1c: a single SEND can resolve a **staged edit + condition**
  together. Added §1d behavior rules (condition mandatory; infeasible →
  best-effort + explanation, accept/reject; 0-fuel → red diamond + notification).
  Noted current DONE handler `acceptEditsWithCheckpoint()` has no condition channel
  yet — that's the wiring target.
- **2026-06-03** — Added §1a-0 (**The Mission** as a first-class, identifiable,
  durable concept — name/number + declared start/end). Documented the gap: today
  `mission_id` is an auto-`uuid4` in RAM-only `MISSION_STORE`, with no name and an
  implicit end (env/cfg-hash mismatch silently forks a new mission). Flagged as a
  **prerequisite** for the §1e memory/ledger work.
- **2026-06-04** — **⏸️ Paused at 0.4b** (resume point marked in §7) to handle an
  unrelated issue. 0.4b pre-work traced; scope decision (sidecar vs swap vs
  defer-for-keys) still open. Phase 0 functionally complete without it.
- **2026-06-04** — **Task 0.6 done:** Mission Control gains a name field + status
  badge + End Mission button, wired to the lifecycle endpoints. `solve_with_allocation`
  now sends + adopts `mission_id`/`mission_name`, so the mission persists across
  edits from the UI. End-to-end TestClient flow verified; 14 suite tests pass.
  **Phase 0 functional scope complete** (only 0.4b durable persistence remains).
- **2026-06-04** — **Task 0.5 done:** added mission lifecycle endpoints (`POST
  /api/missions`, `GET /api/missions/{id}`, `POST /api/missions/{id}/end`) +
  models. Endpoint tests exposed and fixed a resurrection bug — an ENDED mission
  could be reopened when env matched/empty; now short-circuited to mint fresh.
  14 suite tests pass.
- **2026-06-04** — **Task 0.4a done:** added `upsert_mission()` single write path;
  all 3 MISSION_STORE writes routed through it. Merges (preserves
  name/status/created_at across re-solves) instead of clobbering. Verified (unit +
  14 suite tests). 0.4b (durable Supabase persistence) deferred.
- **2026-06-04** — **Rebased onto `origin/main` + verified.** Resolved the branch
  divergence: 5 Phase 0 commits replayed cleanly onto `main` (no conflicts; backup
  at `backup/optimizer-pipeline-pre-rebase-20260604`). 14 tests pass. **User
  confirmed live:** local now fully functional, D2 auto-optimizes with no manual
  Insert — matches Railway. Branch not yet pushed (force-push pending).
- **2026-06-04** — **Branch-divergence finding:** local `feat/optimizer-pipeline`
  is 12 commits behind `origin/main`, missing the refueling-optimizer fixes; Railway
  runs `main` (better refuel results). Explains the local D2 sub-optimal/refuel-
  excluded behavior — NOT a Phase 0 regression. Logged in §6 with a
  rebase-onto-main recommendation; optimizer behavior parked as separate.
- **2026-06-04** — **Task 0.3 COMPLETE (part c done):** `invalidateMission()` in
  `isr.js` keeps `state.missionId` on config edits (invalidates the solution, not
  the mission); the 3 legitimate null sites (segment-0 reset, loadFromJson, load-
  new-env) untouched. `node --check` passes. Net a+b+c: mid-mission edits no longer
  fork a new mission on client OR server — missions persist across edits, end only
  by explicit action.
- **2026-06-04** — **Task 0.3 part (b) done:** `agent_chat_v4` env/cfg mismatch
  branches now keep the active mission's id and only drop the stale solution
  (separating identity from solution-validity); not-found still mints fresh.
  Verified (split test + 6 suite tests). Only 0.3c (frontend `invalidateMission`)
  remains; end-to-end forking persists until it lands.
- **2026-06-04** — **Task 0.3 part (a) done:** `get_or_create_mission` keeps an
  ACTIVE mission's id across env/cfg edits (mints only for absent/not-found/ended);
  added `_mission_is_active()` + local status constants in `main.py`. Verified (5
  unit cases + solve/mission tests green). Confirmed `/solve` already overwrites the
  store entry, so the kept mission's env updates in place. Parts (b)/(c) pending.
- **2026-06-03** — **Pre-0.3 investigation:** found fork-on-edit is **3 mechanisms**
  — identity fork (`get_or_create_mission`, the safe 0.3 target; `continuity` flag
  is never read by the frontend), a solution-staleness guard in `agent_chat_v4`
  (must preserve its fresh-solve effect, separate it from identity), and the
  frontend `invalidateMission()` nulling `missionId` on 9 drone-config edits (the
  real coupling; conflicts with §1a-0; a committed-segments carve-out already
  preserves state). Recorded a revised 3-part 0.3 sub-plan in §7.
- **2026-06-03** — **Task 0.2 done:** centralized mission-id minting in `main.py`
  via `new_mission_id()` + `get_or_create_mission()`; replaced all 4 scattered
  mints. Behavior intentionally unchanged (fork-on-mismatch preserved); the single
  decision point carries an explicit 0.3 note. Tests green.
- **2026-06-03** — **Task 0.1 done:** extended `MissionRecord` with
  `name`/`status`/`created_at`/`ended_at` (+ `is_active`, `end()`), backward-
  compatible (defaults; original fields kept positional). Discovered
  `MISSION_STORE` is written as plain dicts today, so the dataclass was
  doc-only — 0.2/0.4 will wire real construction.
- **2026-06-03** — §7: **wrote the additive migration**
  `docs/architecture/migrations/001_sessions_to_missions.sql` (adds `name`,
  `status`, `ended_at`, `env`, `drone_configs`, `env_hash`, `cfg_hash` + status
  CHECK + indexes to `sessions`; idempotent). Added the **Mission ↔ storage column
  mapping** table as the unambiguous target for task 0.1.
- **2026-06-03** — §7: **schema check done** — `sessions`/`conversations`/
  `mission_plans` tables are defined in `docs/architecture/supabase_schema.sql`
  (`conversations`/`mission_plans` ready as-is). `sessions` is conversation-
  oriented, so Phase 0 needs a **small additive migration** (`name`, `status`,
  `ended_at`, `env`). Residual unknown: whether the schema is applied to the live
  Supabase (verify via `\dt`).
- **2026-06-03** — §7: **resolved the persistence-layer question** — build the
  Mission's durable home on the `session_storage.py` model
  (`sessions`/`conversations`/`mission_plans`) and activate the dormant
  `session_store.py` (`InMemorySessionStore` replaces RAM `MISSION_STORE`,
  `SupabaseSessionStore` for durability); keep `mission_ledger.py` for per-run
  traces. Flagged that this code is well-designed but unproven (no live callers) and
  that the Supabase tables must be verified/migrated. Updated §6.
- **2026-06-03** — §7: added detailed **Phase 0 task breakdown** (0.1–0.6 +
  acceptance check) with real code references: 6 `uuid4` mint sites and 3
  `MISSION_STORE` writes in `main.py`, `MissionRecord` missing name/status,
  `create_mission_run` legacy no-op, `session_storage.save_mission_plan` as an
  alternate persistence home, and frontend `state.missionId` reset sites.
- **2026-06-03** — Added §7 Implementation Plan: identified key code seams
  (animation guard in `sendAgentMessage()` blocks the Agent today;
  `acceptEditsWithCheckpoint()` cut machinery; per-turn-discarded
  `parse_constraints`; ephemeral RAM `mission_id`). Laid out the dependency spine
  (Mission → ledger → SEND) and a 4-phase plan, recommending **Phase 0 (Mission as
  a first-class object)** as the starting point.
- **2026-06-03** — §1a-0: agreed lifecycle/ownership — mission ends by **explicit
  officer action** (not implicit; edits stay inside the same mission), and the
  **Mission owns the environment**. Deferred mission reload/replay to §6.
  Seeded §6 Open Questions (reload/replay, freeze-line rule, condition persistence,
  End-Mission UX).
- **2026-06-03** — Added §1e (Agent memory & per-mission constraint ledger
  vision: recall + structured ledger + contradiction/override advisory, e.g. the
  "never land at A4" case). Documented the substantial **existing** infrastructure
  (`constraints.py`, `constraint_parser.py`, `mission_ledger.py` Supabase tables)
  and the three gaps: landing-airport constraint type, cross-turn accumulation +
  contradiction check, and wiring the ledger as the agent's working memory.
- **2026-06-05/06** — **§8.8 cut+resume RESOLVED** (root: `missionDistance`
  conflated cumulative segment-logic frame vs segment-relative position frame).
  Drone position now computed segment-relative; frozen prefix drawn as static
  history; animated trajectory is segment-only. Plus §8.5–8.7 cut/refuel fixes.
- **2026-06-07** — **Fuel model (§11) made canonical.** Durable per-leg store
  `state.legFuel[did]` + `state.legFuelHistory[did]` so the stats numerator
  survives cuts (was reset by the `startAnimation` wipe). Refuel box shows
  `⛽leg1+leg2 / (tank×N)`; cut is fuel-neutral; budget = tank × num_legs.
  (NOTE: the separate `4c66ad3` fuel-VARIABLE rename — fuel_budget IS the tank,
  retire fuel_capacity — is ORPHANED, not on `main`; north-star refactor.)
- **2026-06-08** — **RESET → continuous clean replay + destructive confirm** (§12.4):
  RESET truncates segments and re-flies the whole original mission as one flight
  instead of stopping at the first old cut; warns (only when >1 segment).
- **2026-06-08** — **Full-mission replay after completion** (§12.5): pressing
  Animate on a finished cut mission replays it; **"Replay segments"** checkbox
  chooses continuous (stitched flown path, default) vs segment-by-segment (stop at
  each diamond, swap env+replan). Fixes: stitched trajectory measured by haversine;
  all targets marked by vertex; repeatable; cut diamonds shown.
- **2026-06-08** — **Refuel config fix:** a drone showing refuel "None" still
  refueled — `updateAirportDropdowns` read the cleared dropdown not the config,
  losing a real "-" (Any). Now reads `state.droneConfigs` as the source of truth.
- **2026-06-08** — **DISTANCE + VISITED unification (§12.2/§12.3).** Phase 1:
  `computeAnimationCumDist` self-selects matrix vs haversine from the inputs (no
  `forceHaversine` flag; 0.9 ratio cutoff). Phase 2: visited targets centralized to
  four named writers (`clearVisited` / `markTargetVisitedByDrone` /
  `seedVisitedFromSegment` / `markAllVisited`), owner map inline, no scattered
  writes. Verified across solve/cut/refuel/both-replay-modes; merged to `main`
  (`61edae3`) and pushed. Phase 3 (whole-mission route) deferred.
