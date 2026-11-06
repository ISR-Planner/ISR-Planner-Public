# Contract: `/api/agents/compare` — as consumed by the harness

**Source of truth**: [server/main.py:2370-2451](../../../server/main.py#L2370-L2451),
[server/agents/agent_metrics.py](../../../server/agents/agent_metrics.py)

This document freezes the subset of the endpoint contract the benchmark harness depends on.
Any future server change to this endpoint that breaks these guarantees requires a corresponding
update to the harness (and a note in this file).

---

## Endpoint

`POST http://<host>:<port>/api/agents/compare`

Default `host:port` = `localhost:8893` (overridable via harness `--server-url`).

## Request body

```jsonc
{
  "message": "string — the prompt (required)",
  "env": {
    "airports":  [ { "id": "A1", "lat": 32.31, "lng": -90.18 }, ... ],
    "targets":   [ { "id": "T1", "lat": 32.33, "lng": -90.19, "priority": 8, "type": "a" }, ... ],
    "sams":      [ { "center": { "lat": 32.34, "lng": -90.18 }, "range_km": 12.5 }, ... ],
    "drone_configs": {                     // optional inside env; may also be sent at top level
      "1": { "enabled": true, "fuel_budget_km": 250, "speed_kmh": 120,
             "start_airport": "A1", "end_airport": "-" }
    }
  },
  "drone_configs": { /* optional top-level override of env.drone_configs */ },
  "llm_config":    { /* optional; harness does not send this */ }
}
```

**Harness contract**: The harness sends `drone_configs` at the **top level** and omits
`env.drone_configs` to avoid ambiguity. The server prefers the top-level copy
(see `main.py:2392`).

**Harness contract**: `message` is the fixed default prompt or the `--prompt` override.

## Response body (HTTP 200)

Returned by `ComparisonResult.to_dict()` (see `server/agents/agent_metrics.py`). The fields
the harness relies on are listed below; any additional fields are tolerated and passed through
to `runs.jsonl`.

```jsonc
{
  "request": "<echoed prompt>",
  "env_summary": {
    "airports": 2, "targets": 5, "sams": 1, "drones_enabled": 2
  },
  "reasoning":    { /* AgentRunMetrics */ },
  "langgraph_v4": { /* AgentRunMetrics */ },
  "winners": { "points": "v4", "fuel": "reasoning", "latency": "reasoning" }  // best-effort
}
```

### AgentRunMetrics (per agent, required subset)

| Field | Type | Notes |
|---|---|---|
| `agent_type` | `"reasoning"` \| `"langgraph_v4"` | Harness uses this to verify shape. |
| `latency_s` | float | Wall-clock seconds. |
| `llm_calls` | int | May be 0 for langgraph_v4 per current implementation. |
| `total_points` | int | Primary comparison metric. |
| `total_fuel_km` | float | Secondary comparison metric. |
| `total_budget_km` | float | Used to compute `fuel_efficiency` client-side if missing. |
| `fuel_efficiency` | float | Server-provided; 0.0 when budget is 0. |
| `targets_visited` | int | |
| `targets_total` | int | |
| `target_coverage` | float | 0.0–1.0. |
| `valid_solution` | bool | If false, the harness treats the run as successful-but-invalid — metrics still recorded; flagged in summary as "invalid solutions". |
| `drone_metrics` | object | Per-drone route + fuel breakdown; passed through verbatim. |
| `response_text` | string | Passed through verbatim. |
| `error` | string \| null | Non-null ⇒ harness records `success: false` for that agent. |

## Latency measurement

The server returns `reasoning.latency_s` and `langgraph_v4.latency_s` per agent. These are
**agent-only** wall-clock latencies (excluding SAM matrix precompute, JSON (de)serialization,
and HTTP framing) and are what the harness records in the per-run JSONL on success.

The harness also wraps the entire `POST /api/agents/compare` call in a local stopwatch for
its own bookkeeping (used only when `success: false` — see [data-model §4](../data-model.md#4-runsjsonl--one-json-record-per-line)).
On HTTP failure / timeout / parse error, the server's `latency_s` is unavailable, so the
harness substitutes its own wall-clock measurement from request-send to exception-raise,
rounded to one decimal place. This harness-side value is never used on successful runs —
the server's per-agent `latency_s` remains authoritative there — so aggregate latency stats
in `manifest.aggregate.*.latency_s` always come from the server.

## Top-level `winners` field — harness behavior

The response contains a best-effort top-level `winners` object. The harness **ignores** it.
Head-to-head counts in `manifest.aggregate.head_to_head` are always recomputed client-side
from per-run `total_points` and `total_fuel_km`, because:

1. The harness defines ties more tightly than the server (fuel tie = 0.1 km tolerance — see
   `FUEL_TIE_TOLERANCE_KM` in plan Constants & Defaults).
2. A server-side `winners` value on a failed run is not meaningful.
3. Re-derivation guarantees that manifest numbers always add up exactly.

If the server's `winners` field disappears in a future release, the harness is unaffected.

## Failure classification (harness side)

The harness treats the following as **transient** (retry once, per FR-009b):
- `requests.exceptions.ConnectionError` (e.g., server restarted; TCP handshake failed before
  any real work began). `error.kind = "network"`.
- HTTP status 500–599. `error.kind = "http_5xx"`.
- HTTP status 429. If response includes `Retry-After: <n>` header, wait `n` seconds before
  retry. `error.kind = "http_429"`.

Treated as **permanent** (no retry, per FR-009c):
- `requests.exceptions.ReadTimeout` / `Timeout` on the HTTP call (≥ `HTTP_TIMEOUT_S`).
  `error.kind = "timeout"`. Rationale: once the server has spent 120 s+ of LLM/compute time,
  a retry is overwhelmingly likely to time out a second time (the agent is doing real work,
  not failing transiently), and retrying would double the per-run budget.
- HTTP status 400–428, 430–499. `error.kind = "http_4xx"`.
- `json.JSONDecodeError` on the response. `error.kind = "parse"`.
- Response JSON missing required fields (`reasoning`, `langgraph_v4`, or either's
  `agent_type`). `error.kind = "validation"`.

## Contract assumptions

1. The endpoint runs **both** agents on every 200 response — harness never needs to call
   single-agent and v4-agent endpoints separately.
2. The server's own per-request timeout is ≥ 120 s (matches harness per-agent budget).
3. The endpoint does not stream — one request, one JSON body response.
4. SAM-aware distance matrix caching inside the endpoint (line 2399) is server-side and
   opaque to the harness.

If any of these assumptions change, update this file and re-evaluate the Constitution Check
in `plan.md`.
