# Quickstart: Agent Benchmark Harness

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

Operator-facing runbook for `scripts/benchmark_agents.py`.

---

## Prerequisites

1. Planner server running:
   ```
   ./run_planner.sh
   ```
   Confirm `http://localhost:8893/api/agents/compare` is reachable.
2. `ANTHROPIC_API_KEY` set in the **server's** `.env` (the harness needs no key of its own).
3. Python deps already installed (`pip install -r requirements.txt`). The harness uses only
   `requests` beyond stdlib.

---

## Basic run (10 environments, default parameters)

```
python3 scripts/benchmark_agents.py --runs 10 --seed 42 --name smoke
```

Output goes to `benchmarks/<UTC-timestamp>-smoke/`:
- `manifest.json` — batch config + aggregate metrics
- `runs.jsonl` — one line per run
- `summary.md` — human-readable head-to-head

Stdout shows one progress line per run and prints `summary.md` at completion.

---

## Overnight run (500 environments, detached)

```
nohup python3 scripts/benchmark_agents.py \
    --runs 500 \
    --seed 4242 \
    --targets 8 --airports 2 --sams 2 --drones 3 \
    --bbox 32.0,33.0,-90.5,-89.5 \
    --name overnight-sweep \
    --inter-run-delay 2 \
    > benchmarks/overnight-sweep.log 2>&1 &
```

- `nohup ... &` detaches from the terminal; safe to close the SSH session.
- `tail -f benchmarks/overnight-sweep.log` to watch progress when you reconnect.
- `tail -f benchmarks/<ts>-overnight-sweep/runs.jsonl` to watch per-run JSON records stream in.
- Ctrl-C (or `kill -INT <pid>`) cleanly finishes the current run, writes `manifest.json` with
  `completed: false`, and exits.

---

## Reproducing a batch

```
python3 scripts/benchmark_agents.py --runs 500 --seed 4242 --name repro \
    --targets 8 --airports 2 --sams 2 --drones 3 \
    --bbox 32.0,33.0,-90.5,-89.5
```

The generated environments are **byte-for-byte identical** to the original run with seed
4242 (FR-003 + SC-002). Agent responses will differ run-to-run due to LLM nondeterminism.

---

## CLI reference

| Flag | Default | Purpose |
|---|---|---|
| `--runs N` | **required** | Number of environments to generate (≥ 1). |
| `--seed N` | random (logged) | Batch seed. Same seed + same other flags ⇒ identical envs. |
| `--name SLUG` | `default` | Slug for the output directory. `[a-z0-9-]+` only. |
| `--output-dir DIR` | `benchmarks/` | Parent directory for the batch subfolder. |
| `--server-url URL` | `http://localhost:8893` | Planner server base URL. |
| `--prompt "…"` | fixed default (see spec) | Overrides the per-run prompt. Same for all runs in batch. |
| `--airports N` | 2 | Airports per environment. |
| `--targets N` | 8 | Targets per environment. |
| `--sams N` | 2 | SAM zones per environment. |
| `--drones N` | 3 | Drones per environment (all enabled by default). |
| `--bbox lat_min,lat_max,lng_min,lng_max` | ~100 km box around (32.5, −90.0) | Bounding box for all generated points. |
| `--fuel-budget-km N` | 250 | Default drone fuel budget. |
| `--inter-run-delay SECONDS` | 2.0 | Sleep between runs (rate-limit safety). |
| `--per-agent-timeout-s N` | 120 | Hard wall-clock timeout per agent call. |
| `--max-retries N` | 1 | Retries on transient errors only. |
| `--max-regen-attempts N` | 10 | Feasibility-filter regeneration budget per env slot. |
| `--min-reachable-targets N` | 2 | Feasibility threshold. |
| `--dry-run` | off | Generate envs and print summary without calling the server. |

---

## Analyzing results

### From the terminal

Head-to-head at a glance:
```
cat benchmarks/<ts>-<name>/summary.md
```

Which runs had each agent winning on points?
```
jq -c 'select(.reasoning.success and .langgraph_v4.success)
       | {run: .run_index,
          reasoning_pts: .reasoning.total_points,
          v4_pts: .langgraph_v4.total_points}' \
   benchmarks/<ts>-<name>/runs.jsonl
```

### From Python

```python
import json
from pathlib import Path

batch = Path("benchmarks/20260421T213005Z-overnight-sweep")
manifest = json.loads((batch / "manifest.json").read_text())
runs = [json.loads(line) for line in (batch / "runs.jsonl").read_text().splitlines()]

# Which agent won more on points?
r_wins = manifest["aggregate"]["head_to_head"]["points"]["reasoning_wins"]
v_wins = manifest["aggregate"]["head_to_head"]["points"]["v4_wins"]
print(f"reasoning {r_wins} vs langgraph_v4 {v_wins}")
```

---

## Failure modes and what to do

| Symptom | Likely cause | Remedy |
|---|---|---|
| `ERROR: cannot reach http://localhost:8893` at startup | Planner server not running | `./run_planner.sh`, then retry. |
| `error.kind: "http_429"` on many runs | LLM rate limit | Increase `--inter-run-delay`, reduce `--runs`. |
| `error.kind: "timeout"` on many runs | Server overload or LLM slow | Increase `--per-agent-timeout-s` (but keeps you in longer-batch territory), or inspect server logs. |
| `low_feasibility_runs` > 10 % of batch | Bbox too small or fuel budget too high | Widen `--bbox` or lower `--fuel-budget-km` so fuel actually constrains. |
| `summary.md` shows both agents tying on every run | Bbox too tight, trivially satisfiable | Same remedy as above. |

---

## Running the harness's own tests

```
pytest tests/benchmark/
```

Tests cover environment generator determinism, feasibility filter correctness, metric
aggregation, JSONL crash-safety, and error classification. No test requires a live API key
or running server.
