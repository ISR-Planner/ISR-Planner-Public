# ISR Planner Refactor Completion Plan

## Purpose

This document records the completed refactor of the ISR Planner codebase into clearer API, solver, agent, and infrastructure boundaries while preserving current behavior.

The guiding rule is: make one cohesive move at a time, keep the test suite green after every move, and avoid mixing behavior changes with structural cleanup unless a bug fix is explicitly required.

## Current State

The planned refactor is complete. The remaining work called out in this document is optional hardening/backlog, not required for refactor completion.

- API request/response schemas moved from `server/main.py` to `server/api/models.py`.
- Mission state, mission identity, continuity hashes, and solution validation moved to `server/api/mission_state.py`.
- Environment file/cache/export behavior moved to `server/api/environment_service.py`.
- Mission lifecycle endpoints moved to `server/api/mission_routes.py`.
- Environment endpoints moved to `server/api/environment_routes.py`.
- LLM and TTS endpoints moved to `server/api/llm_routes.py`.
- Matrix and manual sequence endpoints moved to `server/api/matrix_routes.py`.
- Solve endpoints moved to `server/api/solve_routes.py`.
- Agent chat, reasoning, comparison, and memory endpoints moved to `server/api/agent_routes.py`.
- Optimizer, coverage, wrapped polygon, and wrapping calculation endpoints moved to `server/api/optimizer_routes.py`.
- Legacy run/env/plan ledger behavior now lives in `server/api/run_ledger_service.py`.
- `EnvironmentService` construction with temporary paths and fake matrix callbacks is covered by focused tests.
- Initial `RoutingContext` object and optimizer matrix-selection helper are in place.
- Optimizer route entrypoints now build and pass `RoutingContext` through local projection, split, optimize, merge, and regeneration flow.
- Solve routes now build `RoutingContext` around solver results before validation and post-solve optimizer handoff.
- Phase 4 optimizer extraction is complete. `server/solver/post_optimizer.py` is now a compatibility facade, with shared helpers in `server/solver/optimizers/common.py`, trajectory regeneration in `regenerate.py`, post-insert behavior in `post_insert.py`, Insert Missed in `insert_missed.py`, trajectory swap in `trajectory_swap.py`, Or-opt in `or_opt.py`, crossing removal in `crossing_removal.py`, refuel-leg compatibility helpers in `refuel_legs.py`, the fixed-order optimizer pipeline in `pipeline.py`, and the legacy unified optimizer in `unified.py`.
- Phase 5 solver bridge extraction is structurally complete. Projection/unprojection helpers moved to `server/solver/projection.py`, airport/runway/refuel-stop rules moved to `server/solver/airport_rules.py`, environment parsing moved to `server/solver/mission_parser.py`, ordering constraint helpers moved to `server/solver/constraints.py`, single-leg solving moved to `server/solver/solve_one_leg.py`, refuel solving moved to `server/solver/refuel_solver.py`, matrix/cache helpers moved to `server/solver/matrix_cache.py`, drone config normalization moved to `server/solver/solve_config.py`, post-solve pipeline running moved to `server/solver/pipeline_runner.py`, legacy orienteering interface bootstrap moved to `server/solver/solver_interface.py`, and public solve entrypoints moved to `server/solver/solve_mission.py`. `server/solver/solver_bridge.py` is now a compatibility facade.
- Phase 6 v4 agent extraction is structurally complete. Shared graph state and LLM helpers moved to `server/agents/v4/state.py`, prompt context builders moved to `context.py`, mission metric calculation moved to `metrics.py`, optimizer tool callbacks moved to `optimizer_tools.py`, workflow assembly moved to `workflow.py`, and the strategist, mission planner, allocator, route optimizer, optimizer, critic, and responder nodes now live in focused modules. `server/agents/isr_agent_multi_v4.py` remains as the run orchestration compatibility entrypoint.
- Bootstrap-adjacent health/debug, executive tick, and legacy heuristic agent routes moved out of `server/main.py`; `main.py` is now under the completion target and acts as app assembly plus static serving.
- Focused route coverage now exercises debug, executive tick, and legacy heuristic agent routes directly.
- Active Anthropic/Claude runtime dependencies were removed from active code paths.
- Default LLM provider is now OpenAI/env-driven rather than Anthropic.
- Test suite is clean with no pytest warning summary: `176 passed, 5 skipped` as of the latest local run.
- `make check` is clean locally and remains the required quality gate: autoflake, isort, black, flake8, and typed-core mypy.

Optional future simplification targets:

- `server/agents/isr_agent_multi_v4.py`
- `path_planning_core/boundary_navigation.py`

## Non-Negotiable Guardrails

- Run `pytest` after each meaningful slice.
- Do not refactor solver math and API routing in the same change.
- Preserve endpoint paths and response shapes until an intentional API migration is planned.
- Keep user/worktree changes intact; do not revert unrelated dirty files.
- Prefer wrapper/re-export compatibility during module splits.
- Remove Claude/Anthropic from active runtime code first; historical docs/archive cleanup can be separate.

## Phase 1: Finish API Router Extraction

Goal: make `server/main.py` a lightweight app assembly file.

Recommended order:

1. [x] Extract environment routes to `server/api/environment_routes.py`.
   - Move `/api/environment`, `/api/export_environment`, `/debug/env`.
   - Keep `EnvironmentService` as the backing service.
   - Keep Supabase logging behavior unchanged initially.

2. [x] Extract LLM and TTS routes.
   - Create `server/api/llm_routes.py`.
   - Move `/api/llm/providers`, `/api/llm/test`, `/api/llm/ollama/models`.
   - Create `server/api/tts_routes.py` or keep TTS in `llm_routes.py` if small.
   - Keep provider listing behavior unchanged except for Claude removal.

3. [x] Extract matrix and sequence routes.
   - Create `server/api/matrix_routes.py`.
   - Move `/api/prepare_matrix`, `/api/matrix_status`, `/api/clear_matrix`, `/api/apply_sequence`.
   - This prepares for explicit routing context later.

4. [x] Extract solve routes.
   - Create `server/api/solve_routes.py`.
   - Move `/api/solve` and `/api/solve_with_allocation`.
   - Keep `main.py` globals for run IDs only temporarily if needed.

5. [x] Extract agent routes.
   - Create `server/api/agent_routes.py`.
   - Move `/api/agents/chat`, `/api/agents/chat-v4`, `/api/agents/reason`, `/api/agents/compare`, memory endpoints.
   - Keep trace formatting helpers close to the agent route module.

6. [x] Extract optimizer routes.
   - Create `server/api/optimizer_routes.py`.
   - Move trajectory swap, crossing removal, OR-opt, insert missed, wrapped polygons, and wrapping calculation.
   - Keep request models in `server/api/models.py` until route modules stabilize.

Done criteria:

- `server/main.py` contains app creation, middleware, static mount, router includes, root/health endpoints, and minimal bootstrap only.
- `pytest` passes.

## Phase 2: Remove Runtime Globals from API Layer

Goal: make API state explicit and easier to test.

Work items:

1. [x] Replace `_current_run_id`, `_current_env_version_id`, and `_current_plan_id` with a small service.
   - Suggested module: `server/api/run_ledger_service.py`.
   - Owns environment version creation, run creation, and plan draft creation.

2. [x] Hide direct `MISSION_STORE` access behind functions.
   - Add helpers to `mission_state.py`:
     - `get_mission_record`
     - `set_mission_name`
     - `get_existing_solution_if_fresh`
     - `end_mission_record`
   - Remove direct `MISSION_STORE[...]` usage from route modules.

3. [x] Make `EnvironmentService` injectable.
   - Keep a singleton in production app assembly.
   - Allow test construction with temporary paths and fake matrix callbacks.

Done criteria:

- API route modules do not mutate module-level globals directly.
- Mission store access is centralized.
- Tests can target services without importing the full FastAPI app.

## Phase 3: Routing Context and Matrix Cache Cleanup

Goal: reduce hidden coupling through global distance matrix state.

Work items:

1. [x] Introduce a `RoutingContext` data object.
   - Suggested module: `server/solver/routing_context.py`.
   - Fields:
     - `env`
     - `drone_configs`
     - `distance_matrix`
     - `ref_lat`
     - `ref_lng`
     - `env_hash`
     - `routing_model_hash`

2. [x] Pass `RoutingContext` through solve and optimizer entrypoints where possible.
   - Optimizer route entrypoints now use `RoutingContext`.
   - Solve routes now use `RoutingContext` after solver result creation.

3. [x] Keep the current global matrix cache as a compatibility fallback during migration.

4. [x] Add tests that prove optimizer endpoints use the request solution matrix before falling back to cached matrix.
   - Added a focused solve-route context helper test.

Done criteria:

- New code paths prefer explicit matrix/context arguments.
- Global cache remains only as a backward-compatible fallback.

## Phase 4: Split `post_optimizer.py`

Goal: break the largest solver file into focused optimizer modules without changing behavior.

Suggested package:

```text
server/solver/optimizers/
  __init__.py
  common.py
  constraints.py
  regenerate.py
  insert_missed.py
  trajectory_swap.py
  crossing_removal.py
  or_opt.py
  refuel_legs.py
  pipeline.py
  unified.py
```

Migration strategy:

1. [x] Move shared helpers first:
   - ordering constraints
   - priority parsing
   - loiter fuel
   - route stats
   - waypoint index rebuild

2. [x] Move one optimizer class/function at a time.
   - [x] `regenerate_solution_trajectories` moved to `server/solver/optimizers/regenerate.py`.
   - [x] `PostOptimizer` and legacy post-insert wrapper functions moved to `server/solver/optimizers/post_insert.py`.
   - [x] `insert_missed_optimize` moved to `server/solver/optimizers/insert_missed.py`.
   - [x] `or_opt_optimize` moved to `server/solver/optimizers/or_opt.py`.
   - [x] `CrossingRemovalOptimizer` and `crossing_removal_optimize` moved to `server/solver/optimizers/crossing_removal.py`.
   - [x] Refuel-leg helpers moved to `server/solver/optimizers/refuel_legs.py`.
   - [x] `TrajectorySwapOptimizer` and `trajectory_swap_optimize` moved to `server/solver/optimizers/trajectory_swap.py`.
   - [x] `apply_optimizer_pipeline` moved to `server/solver/optimizers/pipeline.py`.
   - [x] `UnifiedOptimizer` and `unified_optimize` moved to `server/solver/optimizers/unified.py`.

3. [x] Leave `server/solver/post_optimizer.py` as a compatibility facade that re-exports public functions.

4. [x] Run targeted optimizer tests after every move:
   - `tests/test_unified_optimizer.py`
   - `tests/test_or_opt.py`
   - `tests/test_post_optimizer_distance_fallback.py`
   - `tests/test_trajectory_swap_debug.py`
   - `tests/test_refuel_floor_invariant.py`

Done criteria:

- `post_optimizer.py` is a small facade.
- Optimizer behavior tests remain green.

## Phase 5: Split `solver_bridge.py`

Goal: turn solver orchestration into smaller testable components.

Suggested modules:

- `server/solver/projection.py`
- `server/solver/airport_rules.py`
- `server/solver/mission_parser.py`
- `server/solver/constraints.py`
- `server/solver/refuel_solver.py`
- `server/solver/solve_one_leg.py`
- `server/solver/solve_mission.py`
- `server/solver/matrix_cache.py`
- `server/solver/solve_config.py`
- `server/solver/pipeline_runner.py`
- `server/solver/solver_interface.py`

Migration order:

1. [x] Move projection helpers first.
   - `_project_env_to_local_km`
   - `_project_solution_trajectories`
   - `_unproject_solution_trajectories`

2. [x] Move airport/runway predicates and waypoint parsing.
   - [x] Airport/runway/refuel-stop rules moved to `server/solver/airport_rules.py`.
   - [x] Environment parsing moved to `server/solver/mission_parser.py`.

3. [x] Move constraint normalization/order legality.
   - `_normalize_constraints`
   - `_constraints_for_drone`
   - `_subset_constraints_for_leg`
   - `_ordering_legal`

4. [x] Move refuel-specific logic.
   - [x] `_refuel_options`
   - [x] `_relax_positions_to_detection_radii`
   - [x] `_relocate_refuel_stops`
   - [x] `_solve_one_leg`
   - [x] `_solve_opportunistic_drone`
   - [x] `_solve_two_leg_refuel`

5. [x] Move matrix/cache and config normalization helpers.
   - `_compute_env_hash`
   - `_build_distance_matrix`
   - `prepare_distance_matrix`
   - `get_current_matrix`
   - `clear_cached_matrix`
   - `_normalize_fuel_budget_keys`

6. [x] Move post-solve pipeline runner.
   - `run_pipeline_on_solution`

7. [x] Move legacy solver interface bootstrap.
   - path setup for Docker/local imports
   - `OrienteeringSolverInterface` loading
   - global solver instance construction

8. [x] Move public solve entrypoints last.
   - `solve_mission`
   - `solve_mission_with_allocation`
   - compatibility wrappers for private refuel/leg helpers

Done criteria:

- [x] `solver_bridge.py` is either removed or becomes a compatibility facade.
- [x] Solver tests remain green.

## Phase 6: Split the v4 Agent

Goal: reduce `server/agents/isr_agent_multi_v4.py` into agent nodes and shared state helpers.

Suggested package:

```text
server/agents/v4/
  __init__.py
  state.py
  context.py
  metrics.py
  strategist.py
  mission_planner.py
  allocator.py
  route_optimizer.py
  optimizer.py
  critic.py
  responder.py
  workflow.py
```

Migration order:

1. [x] Move `MissionState` and trace helpers.
   - `MissionState`
   - `trace_event`
   - tool callback state accessors
   - provider-agnostic LLM selection/description helpers

2. [x] Move context/metrics helpers.
   - `HARD_CONSTRAINTS`
   - `build_mission_context`
   - `build_current_solution_context`
   - `compute_mission_metrics`

3. [x] Move node functions one at a time.
   - [x] `strategist_node` moved to `server/agents/v4/strategist.py`.
   - [x] `mission_planner_node` moved to `server/agents/v4/mission_planner.py`.
   - [x] `optimizer_node` moved to `server/agents/v4/optimizer.py`.
   - [x] `allocator_node` moved to `server/agents/v4/allocator.py`.
   - [x] `route_optimizer_node` moved to `server/agents/v4/route_optimizer.py`.
   - [x] `critic_node` moved to `server/agents/v4/critic.py`.
   - [x] `responder_node` moved to `server/agents/v4/responder.py`.
4. [x] Keep `isr_agent_multi_v4.py` as a thin facade exposing `run_multi_agent_v4`.
   - [x] Move optimizer tool callbacks to `server/agents/v4/optimizer_tools.py`.
   - [x] Move workflow assembly out of the compatibility entrypoint.

Done criteria:

- [x] Agent nodes are testable independently.
- [x] Public `run_multi_agent_v4` behavior is preserved.

## Phase 7: Complete Claude/Anthropic Removal

Active runtime code no longer depends on Claude/Anthropic. Archived agent modules that
previously initialized Anthropic models have been replaced with retired compatibility
stubs, and active CrewAI routing now imports the maintained reasoning agent.

Remaining cleanup options:

1. Archive purge:
   - [x] Remove or quarantine `server/agents/archive/*` and `server/agents/versions/*` references to `langchain_anthropic`.
   - [x] If preservation is required, move them outside active import paths or clearly mark them non-runtime.

2. Docs/spec cleanup:
   - [x] Replace active deployment docs mentioning `ANTHROPIC_API_KEY` with `OPENAI_API_KEY`.
   - [x] Update active architecture docs or explicitly mark historical docs that describe Claude as the runtime model.
   - Leave historical session notes unchanged only if they are explicitly historical.

3. Generated/script cleanup:
   - [x] Update PDF-generation scripts that embed Claude references.
   - [x] Ignore future `scripts/comparison_result_*.json` outputs; existing tracked comparison JSONs are retained as historical artifacts.

4. Repository hygiene:
   - [x] Keep `CLAUDE.md` as generic coding-agent guidance and update stale active-runtime provider references.
   - [x] Keep `.claude/` because it contains Spec Kit skills/config still useful for local workflows; it is not active runtime configuration.

Done criteria:

- [x] `rg -n "anthropic|Claude|claude|ANTHROPIC|ChatAnthropic|langchain_anthropic" server requirements.txt docker-compose.yml Dockerfile README.md tests` returns no active references.
- Any remaining matches are only in explicitly historical docs or deleted.

## Phase 8: Test and Tooling Hardening

Goal: make refactors safer and cheaper.

Work items:

1. Add route/service tests for:
   - [x] mission lifecycle
   - [x] environment import/export
   - [x] matrix status/clear
   - [x] solve error responses
   - [x] optimizer endpoint matrix fallback

2. Restore local quality tools in the environment.
   - [x] Local `.venv` restored `mypy`, `flake8`, `autoflake`, `black`, FastAPI, and CrewAI.
   - [x] Added GitHub Actions CI so `pytest` and `make check` run in a clean environment.
   - [x] `make check` is the required format/lint/full-project type-check gate.
   - [x] `make type-check` now passes across the active server, path-planning, and test code.
   - [x] Added `make type-check-core` for modules that are clean today: `path_planning_core`, core mission/environment/agent/LLM/matrix/optimizer/solve API modules, run ledger/database helpers, agent session/config/v4 workflow helpers, memory helpers, low-level solver support modules, typed optimizer/pipeline modules, and mission solve orchestration.
   - [x] Expanded typed-core coverage to 64 source files after fixing local optional-value typing issues in the LLM factory, v4 agent context/metrics/allocation/routing/optimizer/responder helpers, CrewAI/reasoning-agent compatibility wrappers, matrix cache, virtual-drone refuel splitting, solver package initialization, crossing removal, trajectory swap, trajectory regeneration, post-insert optimization, solver interface imports, mission solve orchestration, optimizer route projection round-tripping, animation-frame waypoint index handling, and manual sequence matrix-route response shaping.

3. [x] Add import smoke tests for router modules.

4. Consider a minimal FastAPI `TestClient` suite once routers are split.
   - [x] Added targeted `TestClient` coverage for mission lifecycle, environment, matrix, solve-error, and optimizer fallback routes.
   - [x] Added direct route coverage for debug health/Supabase probe, executive tick, and legacy heuristic agent run behavior.
   - FastAPI-dependent route tests are dependency-gated so lean solver-only environments still run the rest of `pytest`.

Done criteria:

- `pytest` remains the baseline.
- [x] `make check` works locally and in CI as the required format/lint/full-project type-check gate.
- [x] Decide whether to create a separate typed-core mypy gate or burn down the full-project mypy backlog.
- [x] Burn down the full-project mypy backlog behind `make type-check`.

## Recommended Next Three PRs

These are post-refactor hardening options, not completion blockers.

1. Continue incremental type-check hardening.
   - Full-project mypy is now clean; keep `make type-check` green as part of every quality pass.
   - Future typing work should focus on adding stricter annotations inside currently untyped function bodies rather than clearing blocking mypy errors.

2. Keep route and integration tests hermetic.
   - Preserve the no-warning pytest baseline by avoiding accidental `.env`/Supabase use in local solver and agent tests.

3. Continue shrinking large runtime modules where useful.
   - `server/main.py` is now under target; optional future targets are the v4 agent orchestration facade and boundary navigation.

## Completion Definition

The refactor is complete when:

- [x] `server/main.py` is under roughly 300 lines and acts as app assembly only.
- [x] Active runtime code contains no Claude/Anthropic dependencies.
- [x] Solver entrypoints accept explicit context/matrix data where needed.
- [x] `post_optimizer.py`, `solver_bridge.py`, and `isr_agent_multi_v4.py` are facades or small orchestrators rather than monoliths.
- [x] Full test suite passes.
- [x] Deployment docs match the actual supported providers.
