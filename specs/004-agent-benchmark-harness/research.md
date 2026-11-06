# Phase 0 Research: Agent Benchmark Harness

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Date**: 2026-04-21

All five clarification topics were resolved in the `/speckit-clarify` session (see `spec.md` →
`## Clarifications → Session 2026-04-21`). This document captures the *implementation-level*
decisions that follow from those clarifications, plus the small number of additional questions
surfaced during planning. No `NEEDS CLARIFICATION` markers remain.

---

## 1. Script structure: single file vs. module package

**Decision**: Single file, `scripts/benchmark_agents.py`.

**Rationale**: The harness is ~400–600 LOC of straight-line generate → POST → record logic.
A module (`scripts/benchmark/__init__.py`, `generator.py`, etc.) would add import ceremony
with no readability benefit at this size, and would diverge from the adjacent
[scripts/test_agent_compare.py](../../scripts/test_agent_compare.py) which is also a single
file. Constitution Principle I (Code Quality) calls for decomposition into named functions;
that is satisfied inside one file.

**Alternatives considered**:
- Multi-file package under `scripts/benchmark/`: rejected for the reason above.
- Add to `server/` as an internal tool: rejected; the harness is an operator utility, not a
  server component, and shouldn't import from the server at all (talks only via HTTP).

---

## 2. HTTP client: `requests` vs. `httpx`

**Decision**: `requests` (already in `requirements.txt`, already used by the sibling script).

**Rationale**: Zero new dependencies. Per the constitution's decision-type mapping, a new
dependency must cite a capability that cannot reasonably be built in-tree; `requests` gives us
everything we need (per-call timeout, header read for `Retry-After`, connection reuse via
`requests.Session`). Async is not required — the harness is sequential by design.

**Alternatives considered**:
- `httpx` async client: rejected; no concurrency is desired (see §6).
- `urllib.request` stdlib only: rejected; `Retry-After` header handling and connection reuse
  are more awkward, and `requests` is already present.

---

## 3. Environment generator: reproducibility mechanism

**Decision**: Two-level seeding.
- Batch seed (from `--seed` or random) feeds a `random.Random` instance.
- Per-run sub-seed is derived as `hashlib.sha256(f"{batch_seed}:{run_index}".encode()).digest()[:8]`
  interpreted as a little-endian unsigned int, then used to seed a per-run `random.Random`.

**Rationale**: FR-003 mandates byte-for-byte reproducibility for the same (seed, run_count,
generator parameters). Using one global `random` instance across the whole batch would
couple the N-th environment's identity to the retry history of the (N-1)-th; deriving the
sub-seed deterministically from `(batch_seed, run_index)` decouples them, so a failing retry
on run 3 doesn't reshuffle runs 4..N. It also makes per-run regeneration (feasibility filter
retries) reproducible: retries use seeds derived from `(batch_seed, run_index, attempt)`.

**Alternatives considered**:
- Single global `Random`: rejected for the coupling reason above.
- `numpy.random` PRNG: rejected; new dependency for no semantic gain.

---

## 4. Feasibility filter: reachability check

**Decision**: Approximate reachability via haversine straight-line distance, *not* full
SAM-aware path planning. A target is considered "reachable by drone D" when
`2 × haversine(D.start_airport, target) + SAM_inflation ≤ D.fuel_budget_km`, where
`SAM_inflation` is a conservative additive term (e.g., +10 %) to account for detours around
SAMs without actually running the SAM-aware distance matrix.

**Rationale**: The filter only needs to decide *admit or regenerate*, not to score the
environment. FR-005b explicitly says the reachability check need not be exact path planning.
Running the real SAM-aware distance matrix (which is what the server does) would (a) duplicate
server logic in the harness (Principle I violation, since we'd need to port `sam_distance_matrix.py`)
and (b) cost extra compute per rejected candidate that we don't need. A loose straight-line
check with a safety margin will reject the clearly infeasible candidates and admit the rest;
the `low_feasibility: true` flag in FR-005b captures edge cases where even the loose check
was wrong.

**Two-stage check**: A target is "reachable by drone D" when BOTH hold:
1. `2 × haversine(D.start_airport, target) × REACHABILITY_SAFETY_MARGIN ≤ D.fuel_budget_km`
   (fuel-budget stage — the 10 % inflation absorbs plausible SAM detours on an otherwise
   clear corridor).
2. The straight-line segment from `D.start_airport` to `target` does **not** pass within
   any SAM's `range_km` of that SAM's center (SAM-blocking stage — computed as
   point-to-segment great-circle distance; see §4.1 below).

Stage 2 was added because the 10 % inflation alone admits corridors that are physically
blocked by SAMs directly on the path. The combined check matches operator intent
("reachable" = "fuel budget covers a straight out-and-back AND the path is not blocked").
It is still an approximation — a real route would go around the SAM and succeed — but for a
**filter** (admit / regenerate) it errs on the side of rejecting blocked configurations,
which is the safer default. Borderline cases are backstopped by `low_feasibility: true`.

**§4.1 Point-to-segment great-circle distance (approximation)**: For a segment `A → B` and
a point `P` on a small area (≤ ~200 km across, which our default bbox satisfies), use a
local-flat approximation:

1. Project `A`, `B`, `P` to meters relative to `A` using equirectangular projection
   (multiply `lat` by `111.0` km/°, `lng` by `111.0 × cos(A.lat)` km/°).
2. Compute point-to-segment distance in the projected plane with the standard formula:
   clamp the segment parameter `t = dot(AP, AB) / dot(AB, AB)` to `[0, 1]`, return
   `‖P − (A + t·AB)‖`.

Over our bbox this is accurate to within ~0.1 % — far inside the noise the 10 % margin
already accepts. Implementers should NOT reach for a full geodesic library.

**Alternatives considered**:
- Call the server's SAM matrix endpoint per candidate: rejected; adds a network round-trip
  inside a tight regeneration loop.
- Skip the filter, rely only on per-run flagging: rejected; the user explicitly chose filter+flag
  in clarification Q5.

**Default constants** (exposed as module-level):
- `REACHABILITY_SAFETY_MARGIN = 1.10` (inflate straight-line distance by 10 % for SAM detours).
- `MIN_REACHABLE_TARGETS = 2` (per clarification Q5).
- `MAX_REGEN_ATTEMPTS_PER_SLOT = 10` (per FR-005b default).

---

## 5. Overnight operation: interrupt safety and progress visibility

**Decision**:
- `runs.jsonl` is opened in append+line-buffered mode; every completed run is written as
  one full JSON line, then `flush()` + `os.fsync()` before the next run begins.
- A `SIGINT` handler installs on `main()` entry, sets a `STOP` flag, and the per-run loop
  checks `STOP` between runs. Current-run in-progress agent calls complete naturally
  (they already have the 120 s timeout from FR-009a); the batch exits cleanly with a
  partial manifest and a final summary of completed runs.
- Progress is emitted as one line per run to stdout, unbuffered, including
  `[i/N] seed=<sub> reasoning_s=... lg_v4_s=... points_r=... points_v4=...`.
- A heartbeat line is emitted every 5 minutes when no run has completed (for cases where an
  agent is hanging inside its 120 s window).

**Rationale**: The user explicitly stated the harness should run overnight. Overnight runs
mean (a) SSH disconnects / `nohup` / terminal closure, so output must be appendable and
durable even if the TTY dies; (b) partial interrupts must not corrupt the record; (c) visible
progress is needed when the operator reconnects to check on it. The `flush+fsync` model
satisfies FR-014's crash-safety clause and the US2 interrupt acceptance scenario.

**Alternatives considered**:
- Write all runs to one JSON array at the end: rejected; a crash loses the whole batch.
- Write a SQLite database: rejected; heavier dep and harder to `tail -f`.
- Spawn a background `&` process: rejected; the user wants a foreground script they can
  launch and walk away from (via `nohup`, `tmux`, or `screen`), not the script itself
  forking to the background.

---

## 6. Parallelism: concurrent agent calls per run?

**Decision**: No. Strictly sequential — reasoning agent, then LangGraph v4, then next run.

**Rationale**: The server's `/api/agents/compare` already runs the two agents sequentially
(comment in `server/main.py` line 2413: "Run both agents sequentially (not parallel — they
share global state)"). The harness honors that contract. Running two comparisons in parallel
from two harness processes is possible but is an operator concern, not a harness feature —
`--name` exists precisely to disambiguate concurrent batch directories.

**Alternatives considered**:
- Hit `/agent/plan` (single agent) and `/agent/v4/plan` (LangGraph) in parallel per run:
  rejected; would bypass `/api/agents/compare` which already computes `ComparisonResult.compute_winners()`
  for us. We'd have to reimplement that logic, and we'd diverge from what the UI compare
  page measures.

---

## 7. Metric aggregation: how to handle mixed success/failure runs

**Decision**: Aggregate metrics (mean, median) are computed over **successful runs only** for
each agent independently. A run where agent A succeeded and agent B failed contributes A's
metrics to A's aggregate and nothing to B's aggregate. Head-to-head win/tie counts are
computed over **runs where both agents succeeded**; runs where only one agent succeeded
are counted in a separate `one_sided_success` bucket per agent.

**Rationale**: FR-013 and FR-009 explicitly say failures are recorded but do not corrupt
averages. The one-sided-success bucket makes it visible when one agent is systematically
more robust than the other — an axis of comparison that would be lost if we only counted
two-sided runs.

**Alternatives considered**:
- Impute zeros for failures: rejected; pollutes the distributions and makes means misleading.
- Drop any run where either side failed: rejected; throws away information about robustness.

---

## 8. Retry and timeout implementation

**Decision**: `requests.post(..., timeout=130)` (slightly more than 120 s to give the server
and network headroom to return a real response rather than having `requests` time out before
the server's own 120 s does). Retry policy lives in one helper `_invoke_compare_with_retry()`:

```
classify_error(response_or_exception):
    → transient (retry once): ConnectionError (connection refused / DNS failure / reset),
                               HTTP 5xx, HTTP 429
    → permanent (record, no retry): ReadTimeout / Timeout, 4xx non-429,
                                    JSONDecodeError, schema-validation error
```

**Rationale**: Maps one-to-one to FR-009a, FR-009b, FR-009c. The classification is centralized
so the retry policy is testable in isolation (per Principle II).

**Why `ReadTimeout` is permanent (not transient)**: FR-009c is explicit — retrying a timeout
"would just burn the budget again". If an agent took ≥ 120 s on attempt 1, it is almost
certainly busy doing real work (long LLM call, SAM matrix compute, etc.), not failing
transiently. A retry would likely time out a second time, doubling the per-run budget from
130 s → 260 s and eroding overnight throughput. ConnectionError, by contrast, typically means
the server briefly restarted or the TCP handshake failed before any work began — retrying is
cheap and usually succeeds.

**Alternatives considered**:
- `urllib3.Retry` built into `requests.adapters.HTTPAdapter`: rejected; harder to enforce the
  "no retry on parse/validation error" clause since those are detected after the HTTP response
  is decoded, not at the adapter layer.

---

## 9. Output directory naming

**Decision**: `benchmarks/<UTC-timestamp>-<batch-name>/` where timestamp is
`YYYYMMDDTHHMMSSZ` (seconds resolution, UTC) and `batch-name` defaults to `default` but is
overridable with `--name <slug>`. The slug MUST match `[a-z0-9-]+`; invalid characters are
rejected at argparse time.

**Rationale**: UTC + second resolution + operator-supplied slug makes collision impossible
in practical overnight use (operator is the only one invoking). Forcing a slug pattern means
paths stay shell-friendly (no quoting needed in downstream tools).

---

## 10. `.gitignore` entry

**Decision**: Add `/benchmarks/` to the repo's top-level `.gitignore` as part of the feature.

**Rationale**: Batch artifacts are local, potentially large (25 MB for a 500-run overnight),
and contain timestamps / machine-specific identifiers that have no business being committed.
