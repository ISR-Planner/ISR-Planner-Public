# ISR Mission Planner — Quarterly Report Source Material

**Prepared for:** Claude Cowork (or whoever drafts the quarterly report)
**Project:** ISR Mission Planner — multi-drone mission planning with SAM avoidance, refueling, and LLM-driven planning
**Repo:** github.com/ISR-Planner/isr-planner (private)
**Active branch:** `feat/optimizer-pipeline`
**Reporting period:** ~3 months (Feb-mid through May 2026)
**Activity:** 158 commits this quarter on top of 533 lifetime commits

---

## What this project is (one paragraph)

ISR Mission Planner is a browser-based tool for planning multi-drone Intelligence, Surveillance, Reconnaissance (ISR) missions. Drones depart airports, visit prioritized targets, avoid SAM (Surface-to-Air Missile) zones, manage fuel (with optional refueling stops), and return to landing airports. The system combines a **classical orienteering solver** (Held-Karp dynamic programming) with an **LLM-driven agent layer** (two competing architectures: a single reasoning agent and a LangGraph multi-agent system). Users can plan missions either by clicking through a UI or by typing natural-language commands. A **benchmark harness** lets us evaluate agent quality at scale on randomized environments.

---

## The arc of the project (where it started → where it is)

The project began as a small grid-based mission planner with a single drone, a handful of targets, and a TSP-style optimizer. Over the past quarter it grew into something much bigger:

- **From grid → real maps.** Lat/lng coordinates throughout, Leaflet map tiles, geolocation default view, persistent map state per user.
- **From single drone → multi-drone with type-aware allocation.** Five drones, type-restricted target access (A/B/C/D/E classes), per-drone fuel budgets and speeds, five allocation strategies (geographic, greedy, balanced, efficient, exclusive).
- **From hard-coded solve → SAM-aware path planning.** Trajectories now wrap around SAM zones using boundary navigation; the distance matrix accounts for SAM detours; drone configs filter targets inside SAM polygons.
- **From single solve → segmented missions.** Cut a mission at any animation point, replan from frozen drone positions, track which targets were visited per segment. A "checkpoint replan" flow lets users say "stop here, change the env, continue."
- **From manual planning → agentic planning.** Two LLM agent architectures (single-agent and LangGraph multi-agent), both interoperating with the same solver, both invokable from the UI's Agent tab.
- **From "does it work?" → "can we measure it?"** Built a comparison framework and a benchmark harness. Ran the first 100-run automated benchmark this quarter.

This is a substantial system. The codebase has ~14k lines of frontend JavaScript, a multi-thousand-line FastAPI backend, two agent implementations, a documented optimizer pipeline, and a deployable Docker container for Railway.

---

## Major deliverables this quarter (in approximate timeline order)

### 1. Two-leg refueling

Drones can now plan missions that touch a refueling airport partway through, effectively doubling their reach. This required:

- A two-leg solver path that runs orienteering on each leg independently, sharing the refuel point
- Exhaustive leg-split optimization (try multiple split points, pick the best)
- UI selector for refuel airport (`""` = none, `"-"` = solver picks, `"A1"` = specific)
- Refueling animation pause (visible 0.5s hold at refuel airports)
- Per-leg fuel validation in the post-optimizer (newly added this quarter — fixed a Swap bug that could empty one leg and overload the other)

### 2. Loiter fuel modeling

Drones can now "loiter" at targets for a per-type-per-drone-configurable time, consuming fuel proportional to speed × time. This required:

- UI Loiter tab where the user configures time-per-target-type for each drone
- Solver-side loiter accounting (iterative drop when over-budget)
- Animation-side loiter pause that visually holds the drone at the target while consuming fuel
- Per-target visited marking that fires correctly even with loiter inserted into the trajectory
- A late-quarter fix that made the post-optimizer pipeline preserve loiter through Swap and No-Cross stages (this was a real bug — the displayed fuel was missing 50–100 km of consumption on tight runs)

### 3. Lat/lng migration (Phase 1–3)

Internal coordinate system migrated from arbitrary "grid units" to actual lat/lng degrees. Three phases:
- Phase 1: helpers, migration script, fixture conversion
- Phase 2: backend solver shim (projects lat/lng → local km for the OP solver, unprojects results back)
- Phase 3: frontend updates throughout (all distances now haversine km; all trajectories in lat/lng)

This unlocked Leaflet map integration and geolocation. SAM polygons render correctly at non-equatorial latitudes (oval-vs-round fix). Multiple subtle bugs (mostly aspect-ratio related at non-zero latitudes) got fixed along the way.

### 4. Leaflet map integration

The canvas now overlays a real map. Grid/Map toggle preserved for users who want abstract view. Zoom controls, map view persistence in localStorage, geolocation default centering, schema migration for localStorage upgrades. The map became a foundation feature — every mission is now visualized on real geography.

### 5. Agent layer — two competing architectures

**Single Reasoning Agent** (`server/agents/reasoning_agent.py`):
- Direct tool-use loop
- Tools: get_targets, get_airports, get_drones, get_sams, solve_full_mission, solve_with_leg_split, solve_route, commit_route, compare_routes, get_distance
- Reasoning agent honors LLM_PROVIDER / LLM_MODEL env vars (supports Anthropic, OpenAI, Z.AI)
- Agent memory integration (persists user preferences and corrections across calls)

**LangGraph Multi-Agent V4** (`server/agents/isr_agent_multi_v4.py`):
- State-machine architecture: Strategist → Allocator → Route Optimizer → Critic → Responder
- Deterministic Coordinator V4 pre-pass for intent classification
- Per-node trace events surfaced to the UI Trace tab
- Wired into the two-leg refueling solver this quarter

Both agents share the same solver bridge and post-optimizer pipeline, so improvements to the solver layer benefit both equally.

### 6. Comparison framework and benchmark harness

Built `/api/agents/compare` endpoint that runs both agents on the same input and returns side-by-side metrics. UI Compare tab surfaces this for interactive evaluation.

Built `scripts/benchmark_agents.py` — a reproducibility framework for automated evaluation:
- Randomized ISR environments under a deterministic seed
- Per-run metrics: points, fuel, target coverage, valid-solution rate, latency, head-to-head winners
- JSONL records crash-safely fsynced per run
- Feasibility filter (up to 30 retries per environment if reachable-target count is too low)
- Configurable bbox, fuel-budget range, drone/target/SAM counts, prompt, timeouts
- Full specs documented under `specs/004-agent-benchmark-harness/`

This is reusable infrastructure. Any future agent architecture can be benchmarked against the same environments.

### 7. Post-optimizer pipeline

Mature three-stage pipeline that runs after the initial solve:
1. **Insert** — pack unvisited targets into routes with fuel slack
2. **Swap** — re-balance targets between drones (auto-iterates to convergence)
3. **No-Cross** — eliminate self-crossings via 2-opt

The pipeline is the canonical post-pass for both Run Planner and the agents. Each step is a pure function that can be invoked alone (separate endpoints for testing). Helper `run_pipeline_on_solution` handles the lat/lng↔km projection dance and trajectory regeneration. This quarter saw multiple bug fixes ensuring the pipeline preserves loiter fuel, per-leg refuel constraints, and SAM-aware distances through every stage.

### 8. UI / UX polish

- Mission Control panel with grouped buttons (Planning / Display / Animation)
- Compare tab with side-by-side agent metrics + Apply-to-Map for either agent's solution
- Trace tab with per-node execution timeline for the multi-agent
- Loiter tab for per-drone target-type time configuration
- Settings modal for LLM provider/model/API-key configuration
- Bottom panel split into side-by-side Mission Control + Edit (room for longer mission sequences)
- Context-sensitive dropdowns: Priority/Type appear only with Target active, Runway with Airport active, SAM-range with SAM active
- Placeholder runway-length dropdown (L/M/S) for future drone-runway-compatibility feature
- Removed duplicate Edit and unused Cut buttons (cosmetic cleanup)

### 9. Late-quarter critical fixes

- **Fuel-budget key normalization**: discovered a long-running bug where three different key spellings (`fuelBudget`, `fuel_budget`, `fuel_budget_km`) caused agents to silently default to a hardcoded 200 km budget when the harness sent the third spelling. Fixed with a shared `resolve_fuel_budget` helper that accepts all three and raises on missing. This invalidated earlier benchmark findings.
- **Loiter-stripping in pipeline**: Mission Fuel display under-reported by 50–100 km because Swap and No-Cross recomputed distance as travel-only. Fixed with a shared `compute_route_loiter_fuel` helper and per-class loiter-aware distance methods. Animation and display numbers now agree.
- **Per-leg refueling Swap**: Swap was moving targets between legs of a refueling drone without checking per-leg capacity. Routes like `A2→A2→T6→...` could result. Fixed with explicit per-leg validation.
- **Stats consistency during animation**: real-time mission stats now update as drones fly, not just at end. Loiter fuel correctly accumulates. Refueling drones correctly track leg-1 → leg-2 transition.
- **SAM-aware distance enforcement**: removed Euclidean fallbacks (they silently under-reported when SAMs were present), enforced SAM-aware distance everywhere a route distance is computed.

---

## Code organization (current state)

```
isr-planner/
├── server/
│   ├── main.py                          FastAPI app + all endpoints (~2,800 lines)
│   ├── agents/
│   │   ├── reasoning_agent.py           Single reasoning agent (active)
│   │   ├── isr_agent_multi_v4.py        LangGraph multi-agent (active, ~3,000 lines)
│   │   ├── coordinator_v4.py            Deterministic pre-pass
│   │   ├── drone_config.py              Shared fuel-budget helper
│   │   ├── agent_metrics.py             Per-agent metrics collection
│   │   └── archive/                     Legacy v1–v3 agents
│   ├── solver/
│   │   ├── solver_bridge.py             Orchestration (~2,700 lines)
│   │   ├── target_allocator.py          5 allocation strategies
│   │   ├── post_optimizer.py            Insert/Swap/No-Cross (~3,400 lines)
│   │   ├── sam_distance_matrix.py       SAM-aware distance matrix
│   │   └── trajectory_planner.py        SAM boundary navigation
│   └── database/
│       └── mission_ledger.py            Supabase + local-JSON fallback
├── webapp/
│   ├── index.html                       Main UI
│   ├── isr.js                           Monolithic frontend (~14,000 lines)
│   ├── isr.css                          Styling
│   └── segmented_mission.js             Segment state manager
├── scripts/
│   └── benchmark_agents.py              Reproducibility harness
├── specs/
│   └── 004-agent-benchmark-harness/     Full design specs for benchmark
├── tests/
│   ├── test_multi_agent_v4.py
│   └── benchmark/                       Harness test suite
├── benchmarks/                          Run artifacts
├── docs/
│   ├── agents/                          Agent architecture docs
│   ├── architecture/                    System architecture docs
│   ├── solver/                          Solver/optimizer docs
│   └── session-notes/                   Handoff and session docs
└── orienteering_with_matrix.py          Held-Karp DP (project root)
```

---

## Honest findings — what the work has taught us

### Benchmark results (preliminary, deserve caveat)

The first 100-run automated benchmark suggested LangGraph V4 outperformed the single agent. Subsequent investigation revealed that **both agents were solving an unconstrained problem** due to the fuel-budget bug. After that fix, the apparent quality gap shrank and a different story emerged: both architectures struggle with feasibility under tight budgets (valid-solution rates of ~34–36%). The headline finding from the first benchmark is not yet publishable — re-running under the corrected agents is the next step.

This is uncomfortable but important: **we found measurement bugs that invalidated earlier numbers**. That we found them is the value of building the benchmark harness in the first place.

### Architectural debt surfaced

The edit-during-animation and segmented-mission subsystems accumulated complexity over time. Multiple checkpoint counters, state spread across globals, dual cut-vs-accept paths, special-casing for refueling drones — these interact in non-obvious ways. Several late-quarter bugs were not isolated defects but symptoms of structural fragility.

Future direction: this subsystem likely needs a simplification pass (single source of truth for drone position state, fewer modes, unified accept-or-cancel) rather than continued patching. Documenting this honestly because it's a real piece of the project's state.

### Solver-vs-agent quality is a known open question

Now that we have honest measurements, the real research question is sharper:
- Does multi-agent reasoning beat single-agent reasoning *when both are constrained correctly*?
- Are LLM agents better as **route planners** or as **constraint editors** (e.g., "re-plan but skip T5")?
- What's the right division of labor between the deterministic solver and the LLM layer?

---

## Future directions

### Near-term — finish what's started (next 4–6 weeks)

These are concrete items already partially built or with clear scope:

- **Re-run the agent benchmark** with all late-quarter fixes applied. The goal: honest comparative numbers we can publish or use to drive architecture decisions.
- **Drone runway classification.** Placeholder UI dropdown (L/M/S) is in place. Wire the logic so drones can declare a minimum runway length and only land at compatible airports. Affects allocator + solver feasibility checks.
- **Allocator loiter-awareness.** All five strategies (geographic, greedy, balanced, efficient, exclusive) currently ignore loiter when budgeting per-drone capacity. With loiter-aware allocation, target distribution should better match true fuel constraints.
- **`loiter_fuel` field in API response.** Server now computes it correctly through the pipeline; main.py response handler still strips it. Adding it would unlock a travel-vs-loiter breakdown in the UI.

### Mid-term — pay down architectural debt (next 1–2 quarters)

The system has matured to the point where structural simplification pays off more than feature additions:

- **Refactor the segmented-mission / edit-during-animation subsystem.** Late-quarter bugs revealed structural fragility: multiple checkpoint counters, state spread across globals, dual cut-vs-accept code paths, special-case branches for refueling drones. The right move is not more patches — it's a simplification pass that establishes one source of truth for drone state, fewer mode transitions, and a unified accept-or-cancel flow.
- **Split the monolithic `webapp/isr.js` (~14k lines).** A modular structure (canvas rendering, UI controls, animation engine, mission state, API client) would make changes safer and onboarding faster. `docs/architecture/MODULAR_ARCHITECTURE.md` already sketches a plan.
- **Unified test harness for agent + solver behavior.** Today tests are scattered (some pytest, some ad-hoc scripts, some manual UI testing). A regression suite that runs end-to-end mission plans through both agents on fixed seeds would catch many of the bugs that this quarter's manual testing found.

### Research directions — the interesting questions

The benchmark harness opens up real research questions worth exploring:

- **Where do LLM agents actually help?** The cleanest hypothesis: LLMs are stronger as **constraint editors** (interpreting "skip T5, prioritize south region") than as **route planners** (which the classical solver does optimally). Designing experiments that isolate these two roles would clarify when to use which.
- **Multi-agent vs. single-agent under realistic constraints.** Now that the fuel-budget fix is in place, the v4 multi-agent's apparent advantage shrank dramatically. Is multi-agent worth the latency overhead? Under what conditions does it pay off? Need more data.
- **Hybrid planning loops.** A current open question: should the agent invoke the solver multiple times (try different allocations, compare results) or commit to one solve? The single-agent's `compare_routes` tool started exploring this; needs more rigor.
- **Adaptive allocation.** `docs/ADAPTIVE_ALLOCATION_STRATEGY.md` and `docs/MULTI_PHASE_MISSION_PLANNING.md` sketch ideas for context-aware allocation that adapts to environment topology. Not yet implemented.
- **Vision integration.** `docs/AGENT_VISION_DESIGN.md` proposes letting agents "see" the map (canvas screenshots or rendered tiles) instead of receiving text-only descriptions. Could change what kinds of reasoning the agent layer enables.

### Longer-term — where this could go

Three plausible directions, in increasing ambition:

1. **Decision-support tool for operators.** Polish into a UX a real ISR planner would use. Means: cleaner state model, better error messaging, configurable mission templates, exportable plans, integration with real airspace data, audit log of agent decisions. The current system has all the algorithmic pieces; what's missing is the operator-grade polish.

2. **Research platform for spatial-AI planning.** Lean into the benchmark harness. Add more agent architectures (LangGraph variants, tool-use without orchestration, retrieval-augmented planning, fine-tuned models). Publish the harness as a reusable spatial-planning benchmark. The infrastructure is more valuable than any single result.

3. **Real-time replanning under uncertainty.** The biggest gap between the current system and real ISR: missions don't unfold as planned. Drones get lost, weather changes, new SAM zones appear, new targets surface. The system already has the building blocks (segmented missions, replan-from-checkpoint), but the *trigger* logic — recognizing when to replan, deciding what's changed enough to matter — is a different problem class. Probably the most interesting research direction if the goal is military-realistic ISR.

---

## Strengths worth emphasizing in the report

1. **The benchmark harness is reusable research infrastructure.** Most hobby/research projects don't have a reproducibility framework. Future agent comparisons inherit this for free.

2. **End-to-end LLM integration with a real solver.** Two competing agent architectures, both interoperating with the same classical optimization layer, both invokable from a real UI on real geographic data. This is non-trivial integration.

3. **The system is honestly evaluated.** When measurement bugs invalidated benchmark findings, the work continued rather than papering over them. The current state of agent comparison is honestly preliminary, not over-claimed.

4. **Real engineering rigor on subtle bugs.** This quarter's bug fixes weren't surface-level — fuel-budget key normalization required correlating server-side numbers with browser-side numbers across three layers of Python. Loiter-stripping required mapping the entire post-pipeline data flow. These are the kinds of bugs that hide in mature systems and require deep tracing to find.

5. **Architecture documented in detail.** `docs/architecture/`, `docs/agents/`, `docs/solver/`, and `docs/session-notes/` contain handoff documents, design proposals, and session summaries. The project is legible to anyone joining the work.

---

## Useful artifacts and references

- **Active branch:** `feat/optimizer-pipeline`
- **Recent representative commits:**
  - `2bb0e2e` — fix: Swap optimizer respects per-leg fuel constraints for refueling drones
  - `1673d60` — fix: post-pipeline preserves loiter fuel in route_data["distance"]
  - `f4061c3` — fix: restore fuel-budget normalization across solver and agents
  - `e36ebbc` — feat: real-time animation stats + loiter fuel fixes
  - `61f9586` — Agent: replace legacy post-opt with two-pass canonical pipeline
  - `10b2a54` — Wire LangGraph v4 multi-agent into the two-leg refuel solver
  - `caf0755` — feat: Compare tab UI for side-by-side agent evaluation
  - `9d1485e` — feat: Phase 2 lat/lng migration - backend solver shim
  - `fef9ab7` — feat: Loiter fuel integration in solver and animation pause
  - `42eac27` — feat: Exhaustive refueling leg split optimization and stat box fixes
- **Benchmark artifact:** `benchmarks/20260422T213919Z-100runs-1drone-15targets/` (100 runs, 2h 26m, JSONL + manifest + summary)
- **Specs:** `specs/004-agent-benchmark-harness/` (data model, contracts, research, quickstart)
- **Architecture docs:** `docs/architecture/SYSTEM_ARCHITECTURE.md`, `docs/agents/AGENTIC_SYSTEM_DESCRIPTION.md`, `docs/agents/V3_TO_V4_MIGRATION.md`
- **Session notes:** `docs/session-notes/SESSION_SUMMARY_MAY19.md`, `SESSION_SUMMARY_MAY19_PART2.md`, `LOITER_AND_REFUELING_HANDOFF.md`, `DEPLOYMENT.md`

---

## Suggested framing for the report

**Open with infrastructure.** Lead with the benchmark harness and comparison framework. These are durable contributions that outlast any specific finding.

**Frame the technical breadth.** This quarter touched the entire stack: lat/lng coordinate migration, refueling, loiter modeling, agent integration, post-optimizer hardening, UI restructuring. It's a wide reach, not just bug fixes.

**Be honest about findings being preliminary.** "We found measurement bugs and are re-running the benchmark" is a *stronger* statement than "v4 wins, here's an unreliable number." Reviewers respect the former.

**Frame architectural debt as discovered, not denied.** Surfacing the edit-during-animation fragility is the precondition for fixing it. That's mature engineering, not failure.

**Tone**: confident, technical, honest. The work is substantive. The challenges are real but normal for a system at this maturity. The arc — from a small grid-based prototype to a deployable multi-agent mission planner with reproducible benchmarking — is genuinely impressive over one quarter.
