# LangGraph Multi-Agent Flow (Project Scan)

## Scope and intent

This document is a code-first scan of how multi-agent orchestration works in this repository today.

- It focuses on the **active runtime path** (`v4`) used by the API.
- It also documents the **legacy `v3` LangGraph** flow because that file still exists and is easy to confuse with active code.
- It calls out implementation details that differ from high-level architecture docs.

---

## 1. Which multi-agent path is active?

The active backend path is **v4**:

- API endpoint `/api/agents/chat-v4` calls `run_multi_agent_v4(...)`.
- `run_multi_agent_v4` is in `server/agents/isr_agent_multi_v4.py`.
- `README.md` and `CLAUDE.md` also mark v4 as active and v1-v3 as legacy.

Important context:

- `server/agents/isr_agent_multi_v3.py` is still present and implements a full LangGraph workflow, but it is not the primary endpoint path.
- `server/agents/graph.py` is a separate simple single-node planner graph and is not the v4 multi-agent flow.

---

## 2. Top-level execution path (v4)

### 2.1 API ingress

`server/main.py` (`/api/agents/chat-v4`) does the orchestration wrapper around the agent:

1. Resolve incoming `env`, `drone_configs`, optional `mission_id`, optional `existing_solution`.
2. Optionally precompute SAM-aware matrix (`calculate_sam_aware_matrix`) before agent call.
3. Create `agent_run` record in mission ledger.
4. Call `run_multi_agent_v4(...)`.
5. Persist run results, trace, optimizer steps, and mission store state.
6. Build UI-facing response (routes, trajectories, allocations, trace_events).

### 2.2 Agent core entrypoint

`run_multi_agent_v4(...)` in `server/agents/isr_agent_multi_v4.py` does:

1. Load policy rules (`mission_ledger`) for learning/policy context.
2. Normalize drone config contract (fuel/start/home/end/accessibility fields).
3. Build distance matrix (prefer precomputed SAM matrix; fallback compute).
4. Run deterministic Coordinator pre-pass (`run_coordinator(...)`).
5. Parse additional constraints (`parse_constraints`) for sequencing hints.
6. Build initial `MissionState`.
7. Invoke compiled LangGraph workflow (`build_reasoning_workflow().invoke(...)`).
8. Compute mission metrics, trajectories, and trace payload.
9. Return unified result object to API layer.

---

## 3. Coordinator pre-pass (deterministic, before LangGraph)

Coordinator is in `server/agents/coordinator_v4.py` and runs **before** graph invocation.

### 3.1 What it does

- Classifies intent from user text (plan/replan/reallocate/explain/what_if/debug).
- Extracts structured constraints:
  - Allocation moves (e.g., `move T5 to D1`)
  - Required targets (e.g., `include T2 and T3`)
  - Algorithm override commands
- Validates environment/matrix and synthetic starts.
- Builds policy contract used downstream.
- Builds per-drone contracts (start/home/end/flexible endpoint flags).
- Emits structured `trace_events` for monitoring.

### 3.2 Output contract injected into `MissionState`

Coordinator returns `CoordinatorDecision` with:

- `intent`
- `policy`
- `constraints`
- `warnings`, `errors`
- `trace_events`
- `guardrails`, `drone_contracts`, `synthetic_starts` (legacy/back-compat fields)

If validation errors exist, `run_multi_agent_v4` can short-circuit and return error response.

---

## 4. LangGraph v4 workflow as implemented

Graph builder: `build_reasoning_workflow()` in `server/agents/isr_agent_multi_v4.py`.

### 4.1 Nodes

- `strategist`
- `mission_planner`
- `allocator`
- `route_optimizer`
- `optimizer`
- `critic`
- `responder`

### 4.2 Edges

Entry point:

- `strategist`

Conditional routing:

- After `strategist`:
  - `request_type == question` -> `responder`
  - else -> `mission_planner`

- After `mission_planner`:
  - `request_type == question` -> `responder`
  - else -> `allocator`

Linear tail:

- `allocator -> route_optimizer -> optimizer -> critic -> responder -> END`

### 4.3 Operational meaning

- Question/explain/debug-like flows can terminate early at responder.
- Solve/replan/reallocate flows run the full chain.
- There are no separate LangGraph `ToolNode` nodes in v4; tool execution happens inside node functions (especially optimizer).

---

## 5. Node responsibilities (v4)

### 5.1 Strategist node

Purpose:

- Interpret user request type and route intent.
- Build analysis using hard constraints + mission context + current solution summary + memory context.

Behavior highlights:

- If Coordinator policy marks `explain_only`, Strategist forces question-like behavior and routes to responder path.
- Parses `REQUEST_TYPE` from LLM output (default fallback `optimize`).
- Parses `PHASE1_TARGETS` for phased mission hints.

State updates:

- `strategy_analysis`
- `request_type`
- `phase1_target_ids` (optional)
- LLM success/error diagnostics

### 5.2 Mission Planner node

Purpose:

- Analyze whether complex segmentation/orchestration is needed.

Behavior highlights:

- For question mode, returns no orchestration.
- Prompts for strict JSON plan (`requires_segmentation`, `segments`, `constraint_operations`, etc.).
- Parses JSON if present and stores parsed fields.

Important implementation note:

- In current graph wiring, `mission_planner` output does not branch to alternate graph paths; non-question requests still go to `allocator`.
- So this node currently acts as an analysis/planning stage, not as a separate sub-graph executor.

### 5.3 Allocator node

Purpose:

- Produce target-to-drone allocation.

Behavior highlights:

- Reads Coordinator policy (strategy, use-existing-allocation mode, modifications, required targets).
- If `use_existing_allocation` and modifications exist, it applies move operations directly to seeded allocation.
- Otherwise performs algorithmic allocation via `target_allocator.allocate_targets`.
- Supports phased logic from strategist (`phase1_target_ids` + deferred `phase2_target_ids`).
- Enforces required targets by forcing missing targets into allocation using heuristic fallback.

State updates:

- `allocation`
- `allocation_reasoning`
- `allocation_strategy`
- `phase2_target_ids`
- algorithm usage tracking (`algorithms_used`)

### 5.4 Route Optimizer node

Purpose:

- Compute routes for each drone from allocation under constraints.

Behavior highlights:

- Uses `OrienteeringSolverInterface` if available; otherwise fallback route construction.
- Honors drone contract start/home/end semantics, including synthetic starts and flexible endpoints.
- Applies sequencing hints (for example `start_with` rules) by route reordering and can freeze segment 0.
- Supports Phase 2 continuation by extending Phase 1 routes with deferred targets.

State updates:

- `routes`
- `route_analysis`
- `solver_type`
- `solver_runtime_ms`
- `algorithms_used` append

### 5.5 Optimizer node

Purpose:

- Apply post-optimization tools to improve existing routes.

Tools bound in node:

- `insert_unvisited_tool`
- `swap_closer_tool`
- `remove_crossings_tool`

Behavior highlights:

- LLM chooses tool calls (`tool_choice="auto"`).
- Each tool enforces improvement checks before mutating state.
- Tools preserve frozen segments and reject non-improving transformations.

State updates:

- `optimizer_analysis`
- `algorithms_used` append

### 5.6 Critic node

Purpose:

- Review solution quality and surface issues/suggestions.

State updates:

- `critic_review`
- `suggestions`

### 5.7 Responder node

Purpose:

- Produce final user-facing response.

Modes:

- Question mode -> LLM answer from context/metrics (`handle_question_response`).
- Solution mode -> deterministic textual mission summary (`handle_solution_response`).

State updates:

- `final_response`

---

## 6. MissionState contract (v4)

`MissionState` is a large typed dict carrying:

- Conversation/messages
- Mission inputs (`environment`, `drone_configs`, `distance_matrix`, `excluded_targets`)
- Coordinator outputs (`intent`, `policy`, `guardrails`, `drone_contracts`, strategy)
- Constraint program fields (`constraint_program`, `sequencing_hints`)
- Agent outputs (`strategy_analysis`, `mission_plan`, `allocation_reasoning`, `route_analysis`, `optimizer_analysis`, `critic_review`, `suggestions`)
- Solution artifacts (`allocation`, `routes`, phase fields)
- Runtime metadata (`solver_type`, `solver_runtime_ms`, `algorithms_used`, `policy_rules`, etc.)
- Final output and error fields

`messages` uses `add_messages` reducer for LangGraph message accumulation.

---

## 7. Data products returned from v4

`run_multi_agent_v4` returns a unified dict including:

- `response`
- `routes`
- `trajectories`
- `allocation`, `allocation_strategy`
- `total_points`, `total_fuel`
- `mission_metrics`
- `strategy_analysis`, `mission_plan`, `allocation_reasoning`, `route_analysis`, `optimizer_analysis`, `critic_review`
- Coordinator contract fields (`intent`, `actions`, `policy`, `constraints`, `warnings`, `errors`)
- Trace payloads:
  - `trace` (decision trace structure including coordinator/env/allocation/solver/final evidence)
  - `coordinator_trace_events`

API layer then maps these into `AgentChatResponse` and persists ledger records.

---

## 8. Legacy v3 LangGraph flow (still present, not primary runtime)

File: `server/agents/isr_agent_multi_v3.py`

### 8.1 v3 graph shape

v3 has explicit agent + tool-node pairs:

- `coordinator -> coordinator_tools`
- `allocator -> allocator_tools`
- `router -> router_tools`
- `validator -> validator_tools`
- `optimizer -> optimizer_tools`

with conditional routing between stages and END.

### 8.2 Distinguishing v3 from v4

- v3 uses `ToolNode` objects and strong tool-forcing (`tool_choice={"type": "any"}` for most agents).
- v4 uses fewer nodes, deterministic Coordinator pre-pass, and in-node tool handling for optimizer.
- v4 is currently the production path in `main.py`.

---

## 9. Important implementation realities and gaps

These are relevant if you are extending this architecture.

1. `mission_planner` is currently advisory in practice.
- It parses orchestration JSON into state, but graph routing still proceeds directly to allocator for non-question requests.

2. `run_multi_agent_v4_segmented` appears referenced but not implemented in scanned code.
- `main.py` imports and calls it in segmented branch, but no definition was found in repository scan.

3. `server/agents/graph.py` is a separate lightweight graph.
- It should not be confused with v4 multi-agent orchestration.

4. Policy skip flags are partially mediated through strategist behavior.
- Example: explain/debug handling effectively relies on strategist forcing `request_type=question`.

---

## 10. Practical extension points

If you want to modify flow behavior cleanly, these are the leverage points:

1. **Intent and policy control**
- `CoordinatorV4.classify_intent` and `select_policy`.

2. **Graph routing changes**
- `build_reasoning_workflow()` conditional edges.

3. **Allocation logic**
- `allocator_node` and `server/solver/target_allocator.py`.

4. **Route computation contracts**
- `route_optimizer_node` with `OrienteeringSolverInterface` and start/home/end semantics.

5. **Post-optimization policy**
- Optimizer tool set and invocation rules.

6. **Trace and observability**
- Coordinator `trace_events` + v4 `trace` structure + mission ledger persistence in `main.py`.

---

## 11. End-to-end sequence (condensed)

1. User calls `/api/agents/chat-v4`.
2. API resolves mission context and optional existing solution.
3. API precomputes SAM matrix and creates run record.
4. `run_multi_agent_v4` normalizes configs and matrix.
5. Coordinator pre-pass sets intent/policy/constraints/guardrails.
6. Initial `MissionState` is built.
7. LangGraph runs:
- `strategist`
- maybe `mission_planner`
- `allocator`
- `route_optimizer`
- `optimizer`
- `critic`
- `responder`
8. v4 builds trajectories + metrics + trace payload.
9. API persists run/trace, updates mission store, and returns response.

---

## 12. Quick file map

- Active workflow: `server/agents/isr_agent_multi_v4.py`
- Deterministic pre-pass: `server/agents/coordinator_v4.py`
- API orchestration wrapper: `server/main.py`
- Legacy full multi-agent graph: `server/agents/isr_agent_multi_v3.py`
- Separate simple graph (not v4): `server/agents/graph.py`
- Orchestration helper library: `server/agents/mission_orchestration_tools.py`
- Existing architecture writeup: `docs/agents/AGENTIC_SYSTEM_DESCRIPTION.md`

