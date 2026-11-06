# Feature Specification: Agent Benchmark Harness

**Feature Branch**: `feat/agent-comparison`
**Created**: 2026-04-21
**Status**: Draft
**Input**: User description: "i want you to create a benchmarking script that generates random environments and runs both the single agent and langgraphv4 agent and accumulates metrics for each run"

## Clarifications

### Session 2026-04-21

- Q: Geographic bounds for generated environments? → A: Parameterized bounding box via CLI flags, defaulting to a wider box (on the order of 100 km × 100 km) sized so drone fuel budgets are actively constraining and agents must make real trade-offs.
- Q: Output format and location for batch artifacts? → A: A dedicated per-batch directory `benchmarks/<timestamp>-<short-name>/` containing `manifest.json` (batch config + aggregate summary), `runs.jsonl` (one run record per line, appended as each run completes), and `summary.md` (human-readable head-to-head).
- Q: Request prompt strategy across runs? → A: Single fixed default prompt ("Plan a mission to maximize priority points. Visit as many targets as possible.") reused across every run in a batch, with an optional `--prompt` CLI flag to override. Holds prompt variance constant so the benchmark isolates agent-implementation differences.
- Q: Failure-handling policy (per-run timeout + retries)? → A: 120 s per-agent wall-clock timeout; one retry on transient errors only (network errors, HTTP 5xx, HTTP 429 with respect-retry-after); no retry on parse/validation errors. Inter-run delay remains operator-configurable per FR-010.
- Q: Infeasible-environment filtering? → A: Reject and regenerate any environment where fewer than 2 targets are reachable by at least one drone within its fuel budget under SAM constraints. Retry up to N attempts per slot; if all attempts fail, accept the environment anyway and flag it in the run record so it can be segmented out during analysis.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run a headless benchmark batch across random environments (Priority: P1)

As a mission-planner developer comparing the single-agent and multi-agent planners, I can launch one command that generates N randomized ISR environments, plans each with both agents, and saves per-run and aggregated metrics to disk. I can then open a single summary file (or table printed to stdout) and see which agent wins on points, fuel, coverage, latency, and cost — without manually clicking through the UI.

**Why this priority**: This is the core request. Today comparison happens one-environment-at-a-time through the UI or the one-shot `scripts/test_agent_compare.py`, which is insufficient to draw statistically meaningful conclusions about agent quality or regressions. A batch harness is the minimum thing that delivers the user's stated value.

**Independent Test**: Run the harness with `--runs 10 --seed 42`. Confirm (a) 10 distinct environments were generated, (b) both agents executed on each environment, (c) a summary report is produced containing per-agent aggregate metrics and per-run rows, and (d) re-running with the same seed reproduces the same environments and the same metric rows (agent nondeterminism aside).

**Acceptance Scenarios**:

1. **Given** a running planner server and a valid API key, **When** the operator runs the harness with `--runs N`, **Then** the harness generates N random environments, invokes both agents on each, and writes per-run records plus an aggregate summary to an output directory.
2. **Given** a batch run completes, **When** the operator opens the summary output, **Then** they can see side-by-side aggregate metrics for each agent: mean/median points, mean fuel, mean coverage, mean latency, success rate, and count of runs where each agent won on points.
3. **Given** the operator passes a `--seed` value, **When** they re-run the harness with the same seed and same run count, **Then** the generated environments are identical across runs (same airports, targets, SAMs, drones).
4. **Given** the operator passes parameters like `--targets 8 --sams 2 --drones 3`, **When** the harness generates environments, **Then** every generated environment respects those parameter counts.

---

### User Story 2 - Survive agent failures and partial outages without losing the whole batch (Priority: P2)

As an operator running long overnight benchmarks, when one agent call times out, errors, or returns an invalid solution, I want the harness to record the failure with diagnostic context and continue the batch, so that one flaky run doesn't throw away hours of work.

**Why this priority**: Agent runs involve LLM calls that can take 30+ seconds each and occasionally fail (rate limits, network errors, parsing errors). A benchmark without fault tolerance is effectively unusable at meaningful run counts. This is required for the P1 story to be practically useful but is separable — a strict-mode batch of 10 runs still has value.

**Independent Test**: Temporarily break one agent (e.g., invalid API key or unreachable port for one of the two agents). Run with `--runs 5`. Confirm the harness completes all 5 environments, records failures with an error reason for the broken agent, still records successes for the healthy agent, and the summary report clearly separates successful from failed runs in aggregate metrics.

**Acceptance Scenarios**:

1. **Given** a run where one agent returns an error, **When** the harness processes that run, **Then** the error is captured with agent identity, error type, and message; the other agent's result is still recorded; and the batch proceeds to the next run.
2. **Given** a batch completes with some failed runs, **When** the operator views the summary, **Then** success rate per agent is reported and aggregate metrics are computed over successful runs only (failed runs do not corrupt averages).
3. **Given** the harness is interrupted mid-batch (Ctrl-C or crash), **When** the operator re-reads the output directory, **Then** per-run records written so far are intact and readable.

---

### User Story 3 - Reproducible, analyzable output format (Priority: P2)

As an analyst comparing agent behavior over time, I want each batch to produce a machine-readable output format that captures the environment, both agent results, and derived metrics per run — so I can load the data into a notebook, diff two batches, or plot trends without re-running anything.

**Why this priority**: Without structured output, the benchmark's value evaporates after the terminal closes. But it's P2 because a first-cut harness that just prints to stdout is still useful for ad-hoc runs; structured output is a quality-of-life boost that enables downstream analysis.

**Independent Test**: Run two batches with different seeds. Load both output files in a separate script and compute: (a) which agent wins more often on points, (b) median latency difference, (c) the exact environment (airports/targets/SAMs) of any run where the agents disagreed on the winner. All of this must be possible from the output alone, without re-invoking the harness.

**Acceptance Scenarios**:

1. **Given** a completed batch, **When** the operator inspects the per-run output, **Then** each record contains the full generated environment, the request prompt used, both agents' raw responses, and all computed metrics.
2. **Given** two batches produced on different days, **When** an analyst loads them programmatically, **Then** the schemas are identical and runs can be compared by metric name without format adaptation.

---

### Edge Cases

- **Infeasible environments**: The harness filters low-feasibility candidates up front via the FR-005b reachability check. If regeneration still produces a low-feasibility environment after the retry budget is exhausted, the harness accepts it, flags the run with `low_feasibility: true`, and still records it as a valid comparison run (both agents legitimately score 0 or near-0 points) rather than treat it as a failure.
- **One agent times out, the other succeeds**: Recorded as a partial run — the successful agent's metrics count toward its aggregates, the failing agent's do not.
- **Both agents return the same tie score**: Ties are reported explicitly in the summary rather than being attributed to either agent.
- **API rate limits across the batch**: If repeated rate-limit failures occur, the harness should respect a configurable inter-run delay and/or back off, so a long run does not fail wholesale on a transient limit.
- **Zero-target or single-airport environments**: Generator parameters must prevent degenerate environments (e.g., `--targets 0`) or reject them explicitly at startup rather than producing meaningless runs.
- **Planner server not running**: The harness must detect connectivity failure before starting the batch and exit with a clear error, rather than silently logging N consecutive connection failures.

## Requirements *(mandatory)*

### Functional Requirements

**Environment generation**

- **FR-001**: The harness MUST generate ISR environments containing airports, prioritized targets, SAM zones, and drone configurations, each with randomized positions and attributes.
- **FR-002**: The generator MUST accept configurable counts for airports, targets, SAM zones, and drones via command-line parameters, and MUST produce every environment in a batch with the requested counts.
- **FR-003**: The generator MUST accept an explicit random seed and, given the same seed and parameters, MUST produce byte-for-byte identical environments across invocations.
- **FR-004**: Generated environments MUST be structurally valid inputs to both agents (all required fields present, coordinates in-range, drone start airports exist).
- **FR-005**: Generated target priorities and SAM ranges MUST vary across runs within realistic ranges such that different environments exercise different agent decisions (i.e., not all runs produce trivially identical solutions).
- **FR-005a**: The harness MUST accept a geographic bounding box for environment generation via command-line parameters (e.g., explicit lat/lng bounds or a box-size-km flag). The default bounding box MUST be sized such that drone fuel budgets are actively constraining (targets cannot all be visited within a single drone's fuel budget by default), forcing meaningful agent trade-offs.
- **FR-005b**: After generating each candidate environment, the harness MUST compute a feasibility check counting how many targets are reachable by at least one drone within its fuel budget under SAM constraints (reachability being a straight-line or SAM-avoiding distance check sufficient for filtering — exact path planning is not required here). If fewer than 2 targets are reachable, the harness MUST reject the candidate and regenerate with a fresh sub-seed, up to a configurable maximum of retry attempts per slot (default: 10). If every retry is rejected, the harness MUST accept the final candidate and flag the run record with `low_feasibility: true` plus the reachable-target count, so downstream analysis can segment these runs out.

**Execution**

- **FR-006**: For each generated environment, the harness MUST invoke the single-agent planner (v1) and the LangGraph v4 planner using the same request prompt and the same environment.
- **FR-006a**: The harness MUST use a single fixed default request prompt for every run in a batch (default: "Plan a mission to maximize priority points. Visit as many targets as possible."), so prompt variance is held constant and aggregate metrics reflect agent-implementation differences rather than prompt-wording differences. The harness MUST accept a `--prompt` CLI flag to override the default; when overridden, the same overridden prompt MUST still be reused across all runs in that batch.
- **FR-007**: The harness MUST support configuring the total number of runs in the batch via a command-line parameter.
- **FR-008**: The harness MUST verify planner-server reachability before starting the batch and abort with a clear error if the server is unreachable.
- **FR-009**: The harness MUST capture and record the failure (agent identity, error type, error message) when an agent call fails, and MUST continue the batch to the next run rather than aborting.
- **FR-009a**: Each per-agent invocation MUST be bounded by a 120-second wall-clock timeout. On timeout, the harness MUST record the failure and proceed to the next step without hanging the batch.
- **FR-009b**: The harness MUST retry a failed per-agent invocation exactly once when the failure is a transient error — specifically: network error (connection refused, DNS failure, connection reset), HTTP 5xx response, or HTTP 429 rate-limit response. A 429 response with a `Retry-After` header MUST respect that header before retrying.
- **FR-009c**: The harness MUST NOT retry on parse/validation errors, 4xx non-429 responses, timeouts (retrying would just burn the budget again), or any error that reflects an agent producing a structurally invalid response. These are recorded as single-shot failures.
- **FR-010**: The harness MUST support an operator-configurable inter-run delay to avoid overwhelming LLM rate limits.

**Metrics and output**

- **FR-011**: For each run, the harness MUST record the complete generated environment, the request prompt, and each agent's full response (or error).
- **FR-012**: For each successful agent result, the harness MUST compute per-run metrics covering: total points scored, total fuel used, fuel efficiency, targets visited, target coverage percent, number of LLM calls or iterations, wall-clock latency, and validity of the produced solution.
- **FR-013**: At batch completion, the harness MUST produce an aggregate summary reporting for each agent: count of successful runs, count of failed runs, mean and median for each numeric metric, and head-to-head counts (runs won by points, runs tied).
- **FR-014**: Per-batch output MUST be written to a dedicated directory `benchmarks/<timestamp>-<short-name>/` (timestamp prevents overwrite across batches) containing: (a) `manifest.json` with batch configuration and aggregate summary, (b) `runs.jsonl` with one JSON record per completed run appended as the run finishes, and (c) `summary.md` with a human-readable head-to-head report. The JSONL per-run format MUST ensure that interrupting the batch mid-run leaves all previously completed run records intact and parseable line-by-line.
- **FR-015**: The harness MUST also print a concise human-readable summary to stdout at batch completion (the same content as `summary.md`), so an operator sees the head-to-head comparison without opening files.

**Reproducibility**

- **FR-016**: The recorded per-run output MUST include the seed used, the generator parameters, and any harness configuration flags so that a batch is fully reproducible from its output alone.

### Key Entities

- **Benchmark Run**: One generated environment plus both agents' results against it. Attributes: run index, seed-derived sub-seed, generated environment, request prompt, single-agent result, langgraph-v4 result, start/end timestamps.
- **Agent Result**: One agent's outcome on one environment. Attributes: agent identity, success/failure flag, latency, LLM-call count, produced drone routes, computed per-agent metrics (points, fuel, coverage, validity), raw response or error diagnostic.
- **Batch Summary**: Aggregate view over all runs in a batch. Attributes: total runs, per-agent success rate, per-agent mean/median for each numeric metric, head-to-head win/tie counts, batch configuration (seed, generator parameters, harness flags).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator can run a 20-environment benchmark against both agents with a single command and receive a complete summary report without manual intervention between runs.
- **SC-002**: Re-running the harness with an identical seed and parameter set produces identical generated environments (100% structural match) across invocations.
- **SC-003**: A batch of 20 runs completes end-to-end even when up to 20% of individual agent calls fail, with all failures reflected in the output and all successful runs contributing to aggregate metrics.
- **SC-004**: After a batch completes, an analyst can answer "which agent won more often on points, and by what margin on average?" within one minute by reading the summary, without re-running the harness or loading the per-run output.
- **SC-005**: A completed batch's output file is sufficient to reconstruct any run's environment and re-invoke either agent on it, with no information lost to stdout-only logs.

## Assumptions

- The existing planner server exposes a working agent-comparison endpoint (currently `/api/agents/compare`) that accepts an environment and drone configs and returns both agents' results with metrics — the harness is a batch-mode client of that endpoint rather than a reimplementation of the planners.
- Both the single-agent (v1) and LangGraph v4 agent remain available through the planner server and are selected by the server's existing agent-type contract; the harness does not need to import either agent module directly.
- The operator runs the harness on the same machine or network as the planner server and can reach it over HTTP.
- Anthropic API credentials are configured in the planner server's environment; the harness itself does not need API keys.
- Benchmark output is stored locally in a new top-level `benchmarks/` directory (one subdirectory per batch). The existing `scripts/comparison_result_*.json` files from ad-hoc one-shot comparisons are unaffected and remain in place. Long-term archival or upload to shared storage is out of scope for v1.
- Generator default counts (airport/target/SAM/drone) and fuel budgets follow the existing defaults in `scripts/test_agent_compare.py` unless the operator overrides them. The default *geographic bounding box*, however, is widened from that script's tight Mississippi footprint to a ~100 km × 100 km box so that fuel becomes a real constraint (see FR-005a).
- LLM nondeterminism is accepted: identical seeds reproduce identical *environments* but not necessarily identical agent *responses*, and the summary is expected to reflect real agent variance.
- Statistical significance testing, plotting, and dashboarding are out of scope for v1; structured output is provided so downstream tools can perform them.
