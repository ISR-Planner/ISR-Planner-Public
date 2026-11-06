#!/usr/bin/env python3
"""Agent benchmark harness — runs randomized ISR environments against both the
single-agent (reasoning) and LangGraph v4 planners via /api/agents/compare, and
accumulates per-run + aggregate metrics under benchmarks/<ts>-<name>/.

Design and contract: specs/004-agent-benchmark-harness/plan.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import requests


DEFAULT_PROMPT = (
    "Plan a mission to maximize priority points. Visit as many targets as possible."
)
DEFAULT_SERVER_URL = "http://localhost:8893"
DEFAULT_OUTPUT_DIR = "benchmarks"
DEFAULT_BATCH_NAME = "default"
SLUG_PATTERN = re.compile(r"^[a-z0-9-]+$")

DEFAULT_AIRPORTS = 2
DEFAULT_TARGETS = 8
DEFAULT_SAMS = 2
DEFAULT_DRONES = 3
DEFAULT_BBOX = (32.0, 33.0, -90.5, -89.5)  # lat_min, lat_max, lng_min, lng_max
DEFAULT_FUEL_BUDGET_KM = 250.0
DEFAULT_SPEED_KMH = 120.0

TARGET_PRIORITY_RANGE = (1, 10)        # inclusive
SAM_RANGE_KM_RANGE = (5.0, 25.0)       # inclusive
TARGET_TYPES = ("a", "b", "c")

PER_AGENT_TIMEOUT_S = 120               # reported / conceptual budget (FR-009a)
HTTP_TIMEOUT_S = 300                    # bumped from 130 after tight-budget runs
                                        # started timing out when the fuel-budget
                                        # bug fix forced the solver to do real work.
MAX_RETRIES = 1                         # FR-009b
INTER_RUN_DELAY_S = 2.0                 # FR-010
MIN_REACHABLE_TARGETS = 2               # FR-005b + clarification Q5
MAX_REGEN_ATTEMPTS_PER_SLOT = 10        # FR-005b default
REACHABILITY_SAFETY_MARGIN = 1.10       # SAM-detour inflation, research §4
HEARTBEAT_INTERVAL_S = 300              # research §5
FUEL_TIE_TOLERANCE_KM = 0.1             # data-model §3 aggregate rules
# v2: per-agent blocks are keyed by entrant name rather than a fixed trio, and
# records carry a `scale_point` for the drone/target scaling sweep.
SCHEMA_VERSION = 2


# ---------------------------------------------------------------------------
# Phase 2: Foundational helpers
# ---------------------------------------------------------------------------


def validate_slug(name: str) -> str:
    """argparse type for --name. Rejects non-slug characters."""
    if not SLUG_PATTERN.match(name):
        raise argparse.ArgumentTypeError(
            f"batch name must match [a-z0-9-]+, got: {name!r}"
        )
    return name


def parse_fuel_range(spec: str) -> tuple[float, float]:
    """argparse type for --fuel-budget-km-range. Format: min,max (both > 0, min <= max)."""
    parts = spec.split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            "fuel range must be two comma-separated floats: min,max"
        )
    try:
        lo, hi = (float(p) for p in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"fuel range values must be floats: {exc}")
    if lo <= 0 or hi <= 0:
        raise argparse.ArgumentTypeError("fuel range values must be > 0")
    if lo > hi:
        raise argparse.ArgumentTypeError("fuel range min must be <= max")
    return (lo, hi)


def parse_bbox(spec: str) -> tuple[float, float, float, float]:
    """argparse type for --bbox. Format: lat_min,lat_max,lng_min,lng_max."""
    parts = spec.split(",")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            "bbox must have 4 comma-separated floats: lat_min,lat_max,lng_min,lng_max"
        )
    try:
        lat_min, lat_max, lng_min, lng_max = (float(p) for p in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"bbox values must be floats: {exc}")
    if lat_min >= lat_max:
        raise argparse.ArgumentTypeError("bbox lat_min must be < lat_max")
    if lng_min >= lng_max:
        raise argparse.ArgumentTypeError("bbox lng_min must be < lng_max")
    return (lat_min, lat_max, lng_min, lng_max)


def _parse_int_list(
    parser: argparse.ArgumentParser, flag: str, spec: str | None
) -> list[int]:
    """Parse a comma-separated list of positive ints for the scaling sweep."""
    if not spec:
        return []
    try:
        values = [int(part.strip()) for part in spec.split(",") if part.strip()]
    except ValueError:
        parser.error(f"{flag} must be comma-separated integers, got {spec!r}")
    if not values or any(v < 1 for v in values):
        parser.error(f"{flag} values must all be >= 1")
    return values


def selected_entrants(args: argparse.Namespace) -> list[str]:
    """Which planners this batch runs, in report order."""
    entrants = [] if args.no_llm_agents else list(_LLM_AGENTS)
    if args.gnn:
        entrants.append("gnn")
    if args.gnn_decoder:
        entrants.append("gnn_decoder")
    if args.solver_control:
        entrants.append("solver_control")
    return entrants


def compare_options_for(args: argparse.Namespace) -> dict:
    """Entrant-selection fields sent to /api/agents/compare."""
    return {
        "include_llm_agents": not args.no_llm_agents,
        "include_gnn": bool(args.gnn),
        "include_gnn_decoder": bool(args.gnn_decoder),
        "include_solver_control": bool(args.solver_control),
        "gnn_model_path": args.gnn_model_path,
        "gnn_route_model_path": args.gnn_route_model_path,
        "allocation_strategy": args.allocation_strategy,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run the agent benchmark harness against /api/agents/compare."
    )
    p.add_argument("--runs", type=int, required=True,
                   help="Number of environments to generate (>=1).")
    p.add_argument("--seed", type=int, default=None,
                   help="Batch seed (default: random; logged at startup).")
    p.add_argument("--name", type=validate_slug, default=DEFAULT_BATCH_NAME,
                   help="Slug for the output directory ([a-z0-9-]+).")
    p.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR,
                   help="Parent directory for the batch subfolder.")
    p.add_argument("--server-url", default=DEFAULT_SERVER_URL,
                   help="Planner server base URL.")
    p.add_argument("--prompt", default=DEFAULT_PROMPT,
                   help="Request prompt reused across every run in the batch.")
    p.add_argument("--airports", type=int, default=DEFAULT_AIRPORTS)
    p.add_argument("--targets", type=int, default=DEFAULT_TARGETS)
    p.add_argument("--sams", type=int, default=DEFAULT_SAMS)
    p.add_argument("--drones", type=int, default=DEFAULT_DRONES)
    p.add_argument("--bbox", type=parse_bbox, default=DEFAULT_BBOX,
                   help="lat_min,lat_max,lng_min,lng_max")
    p.add_argument("--fuel-budget-km", type=float, default=DEFAULT_FUEL_BUDGET_KM)
    p.add_argument("--fuel-budget-km-range", type=parse_fuel_range, default=None,
                   help="If set, draw each drone's fuel_budget_km uniformly from "
                        "this range (min,max) per run. Overrides --fuel-budget-km.")
    p.add_argument("--inter-run-delay", type=float, default=INTER_RUN_DELAY_S)
    p.add_argument("--per-agent-timeout-s", type=int, default=PER_AGENT_TIMEOUT_S)
    p.add_argument("--max-retries", type=int, default=MAX_RETRIES)
    p.add_argument("--max-regen-attempts", type=int, default=MAX_REGEN_ATTEMPTS_PER_SLOT)
    p.add_argument("--min-reachable-targets", type=int, default=MIN_REACHABLE_TARGETS)
    p.add_argument("--dry-run", action="store_true",
                   help="Generate envs and print summary; skip HTTP calls.")

    # --- Entrant selection (GNN comparison) ---
    p.add_argument("--no-llm-agents", action="store_true",
                   help="Skip reasoning/langgraph_v4/crewai. Use at large drone "
                        "counts where LLM agents are neither affordable nor reliable.")
    p.add_argument("--gnn", action="store_true",
                   help="Include the GNN planner (allocation only; exact solver routes).")
    p.add_argument("--gnn-decoder", action="store_true",
                   help="Include the GNN with its learned route decoder (no Held-Karp). "
                        "This is the configuration that can scale past the exact solver.")
    p.add_argument("--solver-control", action="store_true",
                   help="Include the deterministic allocator + exact solver control.")
    p.add_argument("--gnn-model-path", default=None,
                   help="Assignment-model checkpoint (else GNN_MODEL_PATH, else heuristic).")
    p.add_argument("--gnn-route-model-path", default=None,
                   help="Route-edge checkpoint for --gnn-decoder (else GNN_ROUTE_MODEL_PATH).")
    p.add_argument("--allocation-strategy", default="geographic",
                   help="Allocation strategy for the deterministic control.")

    # --- Scaling sweep ---
    p.add_argument("--drone-sweep", default=None,
                   help="Comma-separated drone counts to sweep, e.g. '1,2,5,10,50,100'. "
                        "Runs --runs environments at each point. Overrides --drones.")
    p.add_argument("--target-sweep", default=None,
                   help="Comma-separated target counts, paired positionally with "
                        "--drone-sweep. A single value applies to every point.")

    args = p.parse_args(argv)

    if args.no_llm_agents and not (args.gnn or args.gnn_decoder or args.solver_control):
        p.error("--no-llm-agents leaves no entrants; add --gnn, --gnn-decoder "
                "and/or --solver-control")

    args.drone_sweep_values = _parse_int_list(p, "--drone-sweep", args.drone_sweep)
    args.target_sweep_values = _parse_int_list(p, "--target-sweep", args.target_sweep)
    if args.drone_sweep_values and args.target_sweep_values:
        if len(args.target_sweep_values) not in (1, len(args.drone_sweep_values)):
            p.error("--target-sweep must have 1 value or the same count as --drone-sweep")

    if args.runs < 1:
        p.error("--runs must be >= 1")
    if args.airports < 1:
        p.error("--airports must be >= 1")
    if args.targets < 2:
        p.error("--targets must be >= 2 (otherwise feasibility filter cannot find "
                "min_reachable_targets targets)")
    if args.drones < 1:
        p.error("--drones must be >= 1")
    if args.sams < 0:
        p.error("--sams must be >= 0")
    return args


def derive_sub_seed(batch_seed: int, run_index: int, attempt: int = 0) -> int:
    """Deterministic per-run sub-seed. Same (batch_seed, run_index, attempt) => same seed."""
    key = f"{batch_seed}:{run_index}:{attempt}".encode()
    digest = hashlib.sha256(key).digest()[:8]
    return int.from_bytes(digest, byteorder="little", signed=False)


def make_run_rng(batch_seed: int, run_index: int, attempt: int = 0) -> random.Random:
    """A fresh random.Random seeded deterministically from (batch_seed, run_index, attempt)."""
    return random.Random(derive_sub_seed(batch_seed, run_index, attempt))


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two (lat, lng) pairs in kilometers."""
    earth_radius_km = 6371.0
    lat1, lng1 = a
    lat2, lng2 = b
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    h = (math.sin(dphi / 2) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return 2 * earth_radius_km * math.asin(math.sqrt(h))


def utc_now_iso() -> str:
    """ISO 8601 with trailing Z, second resolution."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_timestamp_slug() -> str:
    """Compact slug-safe timestamp for output-directory names."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# Error classification — see spec FR-009b/c, contracts/compare-endpoint.md
# Transient (retry once): network, http_5xx, http_429
# Permanent (no retry): timeout, http_4xx, parse, validation, unknown
_TRANSIENT_KINDS = frozenset({"network", "http_5xx", "http_429"})


def classify_error(exc_or_response: object) -> str:
    """Map a requests exception or non-200 Response to an error.kind enum value.

    Returns one of: timeout, network, http_5xx, http_429, http_4xx, parse, validation, unknown.
    The caller handles parse/validation separately after decoding the response body.
    """
    if isinstance(exc_or_response, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc_or_response, requests.exceptions.ConnectionError):
        return "network"
    if isinstance(exc_or_response, requests.exceptions.RequestException):
        # Any other requests-layer exception (SSL, invalid URL, etc.)
        return "unknown"
    if isinstance(exc_or_response, requests.Response):
        status = exc_or_response.status_code
        if status == 429:
            return "http_429"
        if 500 <= status < 600:
            return "http_5xx"
        if 400 <= status < 500:
            return "http_4xx"
        return "unknown"
    return "unknown"


def is_transient(error_kind: str) -> bool:
    """Retry exactly once only for transient kinds (FR-009b)."""
    return error_kind in _TRANSIENT_KINDS


# ---------------------------------------------------------------------------
# Phase 3 (US1): environment generator + feasibility filter
# ---------------------------------------------------------------------------


def generate_environment(rng: random.Random, params: dict) -> dict:
    """Produce one environment. Same rng state => byte-identical output.

    Draw order (important for determinism — do not reorder without bumping seeds):
    1. airports: lat, lng (one draw pair per airport)
    2. targets:  lat, lng, priority, type (four draws per target)
    3. sams:     center_lat, center_lng, range_km (three draws per sam)
    4. drones:   start-airport choice (one draw per drone; end_airport fixed to "-")
    """
    lat_min, lat_max, lng_min, lng_max = params["bbox"]

    airports = []
    for i in range(params["airports"]):
        airports.append({
            "id": f"A{i + 1}",
            "lat": rng.uniform(lat_min, lat_max),
            "lng": rng.uniform(lng_min, lng_max),
        })

    targets = []
    pmin, pmax = TARGET_PRIORITY_RANGE
    for i in range(params["targets"]):
        targets.append({
            "id": f"T{i + 1}",
            "lat": rng.uniform(lat_min, lat_max),
            "lng": rng.uniform(lng_min, lng_max),
            "priority": rng.randint(pmin, pmax),
            "type": rng.choice(TARGET_TYPES),
        })

    sams = []
    rmin, rmax = SAM_RANGE_KM_RANGE
    for _ in range(params["sams"]):
        sams.append({
            "center": {
                "lat": rng.uniform(lat_min, lat_max),
                "lng": rng.uniform(lng_min, lng_max),
            },
            "range_km": rng.uniform(rmin, rmax),
        })

    drone_configs = {}
    fuel_range = params.get("fuel_budget_km_range")
    for d in range(params["drones"]):
        start = airports[d % len(airports)]["id"] if airports else "A1"
        # This draw is always consumed to keep rng state stable whether or not
        # fuel_budget_km_range is set.
        fuel_draw = rng.uniform(fuel_range[0], fuel_range[1]) if fuel_range else rng.random()
        fuel_budget = fuel_draw if fuel_range else float(params["fuel_budget_km"])
        drone_configs[str(d + 1)] = {
            "enabled": True,
            "fuel_budget_km": float(fuel_budget),
            "speed_kmh": DEFAULT_SPEED_KMH,
            "start_airport": start,
            "end_airport": "-",
        }

    return {
        "airports": airports,
        "targets": targets,
        "sams": sams,
        "drone_configs": drone_configs,
    }


def _latlng(item: dict) -> tuple[float, float]:
    """Extract (lat, lng) from either a flat dict or a {'center': {...}} wrapper."""
    if "center" in item:
        return (item["center"]["lat"], item["center"]["lng"])
    return (item["lat"], item["lng"])


def _point_segment_distance_km(
    p: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    """Great-circle distance from point P to segment A->B, approximated via
    local-flat projection. Accurate to ~0.1% inside the default bbox.
    See research.md §4.1.
    """
    # Project to meters relative to A.
    km_per_deg_lat = 111.0
    km_per_deg_lng = 111.0 * math.cos(math.radians(a[0]))
    ax, ay = 0.0, 0.0
    bx = (b[1] - a[1]) * km_per_deg_lng
    by = (b[0] - a[0]) * km_per_deg_lat
    px = (p[1] - a[1]) * km_per_deg_lng
    py = (p[0] - a[0]) * km_per_deg_lat

    abx, aby = bx - ax, by - ay
    ab_len_sq = abx * abx + aby * aby
    if ab_len_sq == 0.0:
        # A == B; just return point-to-A distance.
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * abx + (py - ay) * aby) / ab_len_sq
    t = max(0.0, min(1.0, t))
    closest_x = ax + t * abx
    closest_y = ay + t * aby
    return math.hypot(px - closest_x, py - closest_y)


def _target_reachable_by_drone(
    target: dict, drone: dict, airport_by_id: dict, sams: list[dict]
) -> bool:
    """Two-stage check (research §4):
    1. Fuel budget: 2 * haversine * safety_margin <= fuel_budget_km
    2. Straight-line path does not pass within any SAM's range_km of its center.
    """
    start = airport_by_id.get(drone["start_airport"])
    if start is None:
        return False
    t_pos = _latlng(target)
    a_pos = _latlng(start)

    round_trip_km = 2 * haversine_km(a_pos, t_pos) * REACHABILITY_SAFETY_MARGIN
    if round_trip_km > float(drone["fuel_budget_km"]):
        return False

    for sam in sams:
        sam_pos = _latlng(sam)
        dist = _point_segment_distance_km(sam_pos, a_pos, t_pos)
        if dist < float(sam["range_km"]):
            return False
    return True


def reachable_target_count(env: dict) -> int:
    """Count targets reachable by at least one drone under the two-stage check."""
    airport_by_id = {a["id"]: a for a in env["airports"]}
    drones = [
        dc for dc in env["drone_configs"].values()
        if dc.get("enabled", True)
    ]
    sams = env.get("sams", [])
    count = 0
    for target in env["targets"]:
        for drone in drones:
            if _target_reachable_by_drone(target, drone, airport_by_id, sams):
                count += 1
                break
    return count


def accept_environment(
    batch_seed: int, run_index: int, params: dict,
) -> tuple[dict, dict]:
    """Generate candidates until one passes the feasibility filter, or the
    retry budget is exhausted. Returns (env, env_meta).

    env_meta shape (data-model §4):
      regen_attempts: number of REJECTED candidates before the accepted one
                      (0 on first accept; MAX_REGEN_ATTEMPTS_PER_SLOT when budget
                      was exhausted).
      low_feasibility: True iff budget exhausted AND final count < MIN_REACHABLE_TARGETS.
      reachable_target_count: count on the accepted env.
      generated_at: UTC iso timestamp.
    """
    max_regen = int(params.get("max_regen_attempts", MAX_REGEN_ATTEMPTS_PER_SLOT))
    min_reach = int(params.get("min_reachable_targets", MIN_REACHABLE_TARGETS))
    accepted_env = None
    accepted_count = 0
    attempt = 0
    while attempt < max_regen:
        rng = make_run_rng(batch_seed, run_index, attempt)
        candidate = generate_environment(rng, params)
        count = reachable_target_count(candidate)
        if count >= min_reach:
            accepted_env = candidate
            accepted_count = count
            break
        attempt += 1
    else:
        # while-else: budget exhausted without break. Accept the last candidate.
        accepted_env = candidate
        accepted_count = count  # type: ignore[possibly-unassigned]
        attempt = max_regen

    low_feasibility = (
        attempt == max_regen
        and accepted_count < min_reach
    )
    env_meta = {
        "regen_attempts": attempt,
        "low_feasibility": low_feasibility,
        "reachable_target_count": accepted_count,
        "generated_at": utc_now_iso(),
    }
    return accepted_env, env_meta


# ---------------------------------------------------------------------------
# Phase 3 (US1): HTTP client, retry, and result normalization
# ---------------------------------------------------------------------------


# The LLM agents are always present in the response; the GNN and deterministic
# control are opt-in per batch, so they are validated only when requested.
_LLM_AGENTS = ("reasoning", "langgraph_v4", "crewai")
_OPTIONAL_AGENTS = ("gnn", "gnn_decoder", "solver_control")
ALL_AGENTS = _LLM_AGENTS + _OPTIONAL_AGENTS

_REQUIRED_TOP_KEYS = _LLM_AGENTS
_REQUIRED_AGENT_KEYS = ("agent_type",)


def build_compare_request(
    env: dict, prompt: str, compare_options: dict | None = None
) -> dict:
    """Build the POST body for /api/agents/compare.

    Drone configs go at the TOP LEVEL (server prefers that copy over env.drone_configs).
    The `env` we send omits drone_configs to avoid ambiguity.

    `compare_options` carries entrant selection (which planners to run) so a
    scaling sweep can drop the LLM agents at high drone counts while keeping the
    GNN and the deterministic control.
    """
    env_without_dc = {k: v for k, v in env.items() if k != "drone_configs"}
    body = {
        "message": prompt,
        "env": env_without_dc,
        "drone_configs": env["drone_configs"],
    }
    body.update(compare_options or {})
    return body


def _validate_compare_response(body: dict) -> str | None:
    """Return None if body passes schema validation, else an error message."""
    for k in _REQUIRED_TOP_KEYS:
        if k not in body:
            return f"response missing top-level key: {k!r}"
        if not isinstance(body[k], dict):
            return f"response key {k!r} is not an object"
        for ak in _REQUIRED_AGENT_KEYS:
            if ak not in body[k]:
                return f"response.{k} missing required key: {ak!r}"
    return None


def _invoke_compare_with_retry(
    session: requests.Session,
    url: str,
    body: dict,
    max_retries: int = MAX_RETRIES,
) -> tuple[dict | None, dict | None, int, float]:
    """POST to url with retry on transient errors only.

    Returns (response_json, error_dict, retries_used, elapsed_s).
    Exactly one of response_json or error_dict is non-None.
    elapsed_s is harness wall-clock (used only for failure records).
    """
    attempts = 0
    elapsed = 0.0
    error_dict: dict | None = None
    while True:
        t0 = time.monotonic()
        try:
            resp = session.post(url, json=body, timeout=HTTP_TIMEOUT_S)
            elapsed = time.monotonic() - t0
            if resp.status_code != 200:
                kind = classify_error(resp)
                if is_transient(kind) and attempts < max_retries:
                    if kind == "http_429":
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after:
                            try:
                                time.sleep(float(retry_after))
                            except ValueError:
                                pass
                    attempts += 1
                    continue
                error_dict = {
                    "kind": kind,
                    "message": f"HTTP {resp.status_code}: {resp.text[:200]}",
                    "retries_used": attempts,
                    "http_status": resp.status_code,
                }
                return None, error_dict, attempts, round(elapsed, 1)
            # Parse + validate
            try:
                parsed = resp.json()
            except ValueError as exc:
                error_dict = {
                    "kind": "parse",
                    "message": f"json decode failed: {exc}",
                    "retries_used": attempts,
                    "http_status": resp.status_code,
                }
                return None, error_dict, attempts, round(elapsed, 1)
            validation_err = _validate_compare_response(parsed)
            if validation_err is not None:
                error_dict = {
                    "kind": "validation",
                    "message": validation_err,
                    "retries_used": attempts,
                    "http_status": resp.status_code,
                }
                return None, error_dict, attempts, round(elapsed, 1)
            return parsed, None, attempts, round(elapsed, 1)
        except requests.exceptions.RequestException as exc:
            elapsed = time.monotonic() - t0
            kind = classify_error(exc)
            if is_transient(kind) and attempts < max_retries:
                attempts += 1
                continue
            error_dict = {
                "kind": kind,
                "message": f"{type(exc).__name__}: {exc}",
                "retries_used": attempts,
                "http_status": None,
            }
            return None, error_dict, attempts, round(elapsed, 1)


def normalize_agent_result(
    agent_block: dict | None,
    server_agent_type: str,
    harness_latency_s: float,
    retries_used: int,
    error: dict | None,
) -> dict:
    """Emit per-agent JSONL block per data-model §4.

    On success: include all metric fields + server's latency_s.
    On failure: include only agent_type, latency_s (harness), retries_used, error.
    """
    if error is not None or agent_block is None:
        return {
            "agent_type": server_agent_type,
            "success": False,
            "latency_s": round(harness_latency_s, 1),
            "retries_used": retries_used,
            "error": error or {"kind": "unknown", "message": "no agent block",
                               "retries_used": retries_used, "http_status": None},
        }

    # Per-agent error field in the response body
    if agent_block.get("error"):
        return {
            "agent_type": server_agent_type,
            "success": False,
            "latency_s": float(agent_block.get("latency_s", 0.0) or 0.0),
            "retries_used": retries_used,
            "error": {
                "kind": "validation",
                "message": str(agent_block["error"]),
                "retries_used": retries_used,
                "http_status": 200,
            },
        }

    return {
        "agent_type": server_agent_type,
        "success": True,
        "latency_s": float(agent_block.get("latency_s", 0.0) or 0.0),
        "llm_calls": int(agent_block.get("llm_calls", 0) or 0),
        "total_points": int(agent_block.get("total_points", 0) or 0),
        "total_fuel_km": float(agent_block.get("total_fuel_km", 0.0) or 0.0),
        "total_budget_km": float(agent_block.get("total_budget_km", 0.0) or 0.0),
        "fuel_efficiency": float(agent_block.get("fuel_efficiency", 0.0) or 0.0),
        "targets_visited": int(agent_block.get("targets_visited", 0) or 0),
        "targets_total": int(agent_block.get("targets_total", 0) or 0),
        "target_coverage": float(agent_block.get("target_coverage", 0.0) or 0.0),
        "valid_solution": bool(agent_block.get("valid_solution", False)),
        "drone_metrics": agent_block.get("drone_metrics") or {},
        "response_text": agent_block.get("response_text") or "",
        "retries_used": retries_used,
        "error": None,
    }


_REQUIRED_RECORD_KEYS_BASE = (
    "schema_version", "run_index", "sub_seed", "env_meta", "env",
    "prompt", "drone_configs", "started_at", "ended_at",
)

_REQUIRED_RECORD_KEYS = _REQUIRED_RECORD_KEYS_BASE + _LLM_AGENTS


def _all_agents_from_response(
    response_json: dict | None,
    error_dict: dict | None,
    harness_latency: float,
    retries: int,
    agents: Sequence[str] = _LLM_AGENTS,
) -> dict:
    """Split the compare-response into per-agent normalized blocks.

    On whole-call failure, all agents share the same error record. Returns a
    mapping keyed by agent name so entrants can be added without changing every
    caller's tuple arity.
    """
    blocks: dict = {}
    for name in agents:
        block = response_json.get(name) if response_json is not None else None
        err = error_dict if response_json is None else None
        if response_json is not None and block is None:
            # Requested entrant absent from the response — record it as a
            # failure rather than silently dropping it from the batch.
            err = {
                "kind": "validation",
                "message": f"server response missing agent block: {name!r}",
                "retries_used": retries,
                "http_status": 200,
            }
        blocks[name] = normalize_agent_result(
            block, name, harness_latency, retries, err
        )
    return blocks


def _build_run_record(
    batch_seed: int, run_index: int, env: dict | None, env_meta: dict,
    prompt: str, started_at: str, agent_blocks: dict,
) -> dict:
    record = {
        "schema_version": SCHEMA_VERSION,
        "run_index": run_index,
        "sub_seed": derive_sub_seed(batch_seed, run_index,
                                    env_meta.get("regen_attempts", 0)),
        "env_meta": env_meta,
        "env": env,
        "prompt": prompt,
        "drone_configs": env["drone_configs"] if env else {},
        "started_at": started_at,
        "ended_at": utc_now_iso(),
    }
    record.update(agent_blocks)
    return record


def _build_unknown_failure_record(
    batch_seed: int, run_index: int, prompt: str, started_at: str, exc: BaseException,
    agents: Sequence[str] = _LLM_AGENTS,
) -> dict:
    """Failure record for an exception outside the HTTP path (e.g., generator crash)."""
    err = {
        "kind": "unknown",
        "message": f"{type(exc).__name__}: {exc}",
        "retries_used": 0,
        "http_status": None,
    }
    record = {
        "schema_version": SCHEMA_VERSION,
        "run_index": run_index,
        "sub_seed": derive_sub_seed(batch_seed, run_index, 0),
        "env_meta": {
            "regen_attempts": 0, "low_feasibility": False,
            "reachable_target_count": 0, "generated_at": utc_now_iso(),
        },
        "env": None,
        "prompt": prompt,
        "drone_configs": {},
        "started_at": started_at,
        "ended_at": utc_now_iso(),
    }
    for name in agents:
        record[name] = normalize_agent_result(None, name, 0.0, 0, err)
    return record


def run_single_batch_step(
    session: requests.Session,
    batch_seed: int,
    run_index: int,
    params: dict,
    prompt: str,
    server_url: str,
    jsonl_writer,
    entrants: Sequence[str] = _LLM_AGENTS,
    compare_options: dict | None = None,
    required_record_keys: Sequence[str] | None = None,
) -> dict:
    """Orchestrate one row of the state machine (data-model §6)."""
    started_at = utc_now_iso()
    required = tuple(required_record_keys or _REQUIRED_RECORD_KEYS)
    try:
        env, env_meta = accept_environment(batch_seed, run_index, params)
        request_body = build_compare_request(env, prompt, compare_options)
        url = server_url.rstrip("/") + "/api/agents/compare"
        response_json, error_dict, retries, harness_latency = (
            _invoke_compare_with_retry(session, url, request_body)
        )
        agent_blocks = _all_agents_from_response(
            response_json, error_dict, harness_latency, retries, entrants,
        )
        record = _build_run_record(
            batch_seed, run_index, env, env_meta, prompt,
            started_at, agent_blocks,
        )
        for k in required:
            if k not in record:
                print(f"[warn] run {run_index}: record missing {k}, skipping write",
                      file=sys.stderr, flush=True)
                return record
        jsonl_writer(record)
        return record
    except Exception as exc:  # noqa: BLE001 — overnight-safety guard (T037)
        record = _build_unknown_failure_record(
            batch_seed, run_index, prompt, started_at, exc, entrants,
        )
        try:
            jsonl_writer(record)
        except Exception:  # noqa: BLE001
            pass
        return record


# ---------------------------------------------------------------------------
# Phase 3 (US1): startup checks, batch directory, JSONL writer
# ---------------------------------------------------------------------------


def assert_server_reachable(url: str) -> None:
    """Probe the server before starting the batch (FR-008)."""
    probe = url.rstrip("/") + "/docs"
    try:
        r = requests.get(probe, timeout=5)
        if r.status_code >= 500:
            print(f"ERROR: planner server at {url} returned {r.status_code}",
                  file=sys.stderr, flush=True)
            sys.exit(2)
    except requests.exceptions.RequestException as exc:
        print(f"ERROR: cannot reach {url} ({type(exc).__name__}: {exc}). "
              f"Start the planner with ./run_planner.sh.",
              file=sys.stderr, flush=True)
        sys.exit(2)


def create_batch_dir(output_dir: str, name: str) -> Path:
    """Create benchmarks/<UTC-ts>-<name>/, never overwriting an existing dir."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    batch_dir = root / f"{utc_timestamp_slug()}-{name}"
    batch_dir.mkdir(parents=False, exist_ok=False)
    return batch_dir


def open_jsonl_writer(batch_dir: Path):
    """Return (write_fn, close_fn). write_fn flushes+fsyncs after every record."""
    path = batch_dir / "runs.jsonl"
    f = open(path, "a", buffering=1, encoding="utf-8")

    def write(record: dict) -> None:
        f.write(json.dumps(record) + "\n")
        f.flush()
        os.fsync(f.fileno())

    def close() -> None:
        try:
            f.flush()
            os.fsync(f.fileno())
        finally:
            f.close()

    return write, close


# ---------------------------------------------------------------------------
# Phase 3 (US1): aggregation + manifest + summary
# ---------------------------------------------------------------------------


def percentile(values: list[float], p: float) -> float:
    """Linear-interpolation percentile over an unsorted list. p in [0, 1]."""
    if not values:
        return 0.0
    vs = sorted(values)
    if len(vs) == 1:
        return vs[0]
    idx = p * (len(vs) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return vs[lo]
    return vs[lo] + (vs[hi] - vs[lo]) * (idx - lo)


def _stats(values: list[float], include_p95: bool = False) -> dict:
    if not values:
        base = {"mean": 0.0, "median": 0.0, "min": 0.0, "max": 0.0}
        if include_p95:
            base["p95"] = 0.0
        return base
    s = sorted(values)
    mean = sum(s) / len(s)
    median = s[len(s) // 2] if len(s) % 2 else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2
    out = {"mean": round(mean, 3), "median": round(median, 3),
           "min": round(s[0], 3), "max": round(s[-1], 3)}
    if include_p95:
        out["p95"] = round(percentile(values, 0.95), 3)
    return out


def _per_agent_aggregate(records: list[dict], agent_key: str) -> dict:
    successes = [r[agent_key] for r in records if r.get(agent_key, {}).get("success")]
    failures = [r[agent_key] for r in records if not r.get(agent_key, {}).get("success")]
    if not records:
        success_rate = 0.0
    else:
        success_rate = round(len(successes) / len(records), 4)

    points = [r["total_points"] for r in successes]
    fuel = [r["total_fuel_km"] for r in successes]
    fuel_eff = [r["fuel_efficiency"] for r in successes]
    coverage = [r["target_coverage"] for r in successes]
    latency = [r["latency_s"] for r in successes]
    llm_calls = [float(r["llm_calls"]) for r in successes]
    valid_count = sum(1 for r in successes if r.get("valid_solution"))
    valid_rate = round(valid_count / len(successes), 4) if successes else 0.0

    return {
        "runs_successful": len(successes),
        "runs_failed": len(failures),
        "success_rate": success_rate,
        "points": _stats([float(p) for p in points]),
        "fuel_km": _stats(fuel),
        "fuel_efficiency": _stats(fuel_eff),
        "coverage": _stats(coverage),
        "latency_s": _stats(latency, include_p95=True),
        "llm_calls": _stats(llm_calls),
        "valid_solution_rate": valid_rate,
    }


def _agents_present(records: list[dict]) -> list[str]:
    """Entrant names that appear in these records, in canonical report order."""
    return [name for name in ALL_AGENTS if any(name in r for r in records)]


def _aggregate_by_scale_point(records: list[dict]) -> list[dict]:
    """Per-(drones, targets) aggregates — the core of the scaling comparison.

    Each point reports every entrant's success rate, latency and solution
    quality, so the scale at which a planner stops returning is visible rather
    than being averaged away across the whole batch.
    """
    grouped: dict[tuple[int, int], list[dict]] = {}
    for r in records:
        sp = r.get("scale_point") or {}
        key = (int(sp.get("drones", 0)), int(sp.get("targets", 0)))
        grouped.setdefault(key, []).append(r)

    if len(grouped) <= 1 and (0, 0) in grouped:
        return []  # No sweep was run; the batch-level block already covers it.

    points = []
    for (drones, targets), rows in sorted(grouped.items()):
        points.append({
            "drones": drones,
            "targets": targets,
            "runs": len(rows),
            "agents": {
                name: _per_agent_aggregate(rows, name)
                for name in _agents_present(rows)
            },
        })
    return points


def aggregate_runs(records: list[dict]) -> dict:
    """Compute the manifest.aggregate block per data-model §3."""
    reasoning = _per_agent_aggregate(records, "reasoning")
    v4 = _per_agent_aggregate(records, "langgraph_v4")
    crewai = _per_agent_aggregate(records, "crewai")

    def _ok(r, key):
        return r.get(key, {}).get("success", False)

    all_three = [r for r in records if _ok(r, "reasoning") and _ok(r, "langgraph_v4") and _ok(r, "crewai")]
    all_failed = [r for r in records if not _ok(r, "reasoning") and not _ok(r, "langgraph_v4") and not _ok(r, "crewai")]

    r_wins = v_wins = c_wins = pts_ties = 0
    for r in all_three:
        rp = r["reasoning"]["total_points"]
        vp = r["langgraph_v4"]["total_points"]
        cp = r["crewai"]["total_points"]
        best = max(rp, vp, cp)
        winners = sum([rp == best, vp == best, cp == best])
        if winners > 1:
            pts_ties += 1
        elif rp == best:
            r_wins += 1
        elif vp == best:
            v_wins += 1
        else:
            c_wins += 1

    r_fuel_wins = v_fuel_wins = c_fuel_wins = fuel_ties = 0
    for r in all_three:
        rf = r["reasoning"]["total_fuel_km"]
        vf = r["langgraph_v4"]["total_fuel_km"]
        cf = r["crewai"]["total_fuel_km"]
        best = min(rf, vf, cf)
        close = {
            "r": abs(rf - best) <= FUEL_TIE_TOLERANCE_KM,
            "v": abs(vf - best) <= FUEL_TIE_TOLERANCE_KM,
            "c": abs(cf - best) <= FUEL_TIE_TOLERANCE_KM,
        }
        if sum(close.values()) > 1:
            fuel_ties += 1
        elif close["r"]:
            r_fuel_wins += 1
        elif close["v"]:
            v_fuel_wins += 1
        else:
            c_fuel_wins += 1

    low_feas = sum(1 for r in records
                   if (r.get("env_meta") or {}).get("low_feasibility"))

    # Any optional entrant present in the records gets the same per-agent block.
    optional_blocks = {
        name: _per_agent_aggregate(records, name)
        for name in _OPTIONAL_AGENTS
        if any(name in r for r in records)
    }

    return {
        "reasoning": reasoning,
        "langgraph_v4": v4,
        "crewai": crewai,
        **optional_blocks,
        "by_scale_point": _aggregate_by_scale_point(records),
        "head_to_head": {
            "all_succeeded": len(all_three),
            "all_failed": len(all_failed),
            "points": {
                "reasoning_wins": r_wins,
                "langgraph_v4_wins": v_wins,
                "crewai_wins": c_wins,
                "ties": pts_ties,
            },
            "fuel_km": {
                "reasoning_wins": r_fuel_wins,
                "langgraph_v4_wins": v_fuel_wins,
                "crewai_wins": c_fuel_wins,
                "ties": fuel_ties,
            },
        },
        "low_feasibility_runs": low_feas,
    }


def _box_size_km_approx(bbox: tuple[float, float, float, float]) -> float:
    lat_min, lat_max, lng_min, lng_max = bbox
    diag = haversine_km((lat_min, lng_min), (lat_max, lng_max))
    return round(diag, 1)


def build_manifest(
    config: dict,
    batch_meta: dict,
    aggregate: dict,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": batch_meta,
        "config": config,
        "aggregate": aggregate,
    }


def write_manifest_atomic(batch_dir: Path, manifest: dict) -> None:
    tmp = batch_dir / "manifest.json.tmp"
    final = batch_dir / "manifest.json"
    tmp.write_text(json.dumps(manifest, indent=2))
    with open(tmp, "r+b") as f:
        os.fsync(f.fileno())
    os.replace(tmp, final)


def _duration_str(started_at: str, ended_at: str) -> str:
    try:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        t0 = datetime.strptime(started_at, fmt).replace(tzinfo=timezone.utc)
        t1 = datetime.strptime(ended_at, fmt).replace(tzinfo=timezone.utc)
        secs = int((t1 - t0).total_seconds())
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}h {m}m"
        if m:
            return f"{m}m {s}s"
        return f"{s}s"
    except Exception:  # noqa: BLE001
        return "?"


_AGENT_LABELS = {
    "reasoning": "Reasoning",
    "langgraph_v4": "LangGraph v4",
    "crewai": "CrewAI",
    "gnn": "GNN (solver routing)",
    "gnn_decoder": "GNN (learned decoder)",
    "solver_control": "Solver control",
}


def _render_planner_comparison(agg: dict) -> list[str]:
    """Entrant table covering the GNN variants and the deterministic control."""
    present = [n for n in _OPTIONAL_AGENTS if n in agg]
    if not present:
        return []

    rows = ["reasoning", "langgraph_v4", "crewai"] + present
    rows = [n for n in rows if n in agg and agg[n]["runs_successful"] + agg[n]["runs_failed"]]

    lines = [
        "## Planner comparison (GNN vs control)",
        "",
        "| Planner | Success | Mean points | Mean coverage | Mean latency (s) | p95 latency (s) | LLM calls |",
        "|---|---|---|---|---|---|---|",
    ]
    for name in rows:
        a = agg[name]
        total = a["runs_successful"] + a["runs_failed"]
        lines.append(
            f"| {_AGENT_LABELS.get(name, name)} "
            f"| {a['success_rate']*100:.0f} % ({a['runs_successful']}/{total}) "
            f"| {a['points']['mean']} "
            f"| {a['coverage']['mean']*100:.0f} % "
            f"| {a['latency_s']['mean']} "
            f"| {a['latency_s'].get('p95', 0.0)} "
            f"| {a['llm_calls']['mean']} |"
        )
    lines.append("")
    return lines


def _render_scaling_section(agg: dict) -> list[str]:
    """Per-scale-point table: where each planner stops returning solutions."""
    points = agg.get("by_scale_point") or []
    if not points:
        return []

    names: list[str] = []
    for p in points:
        for n in p["agents"]:
            if n not in names:
                names.append(n)
    names = [n for n in ALL_AGENTS if n in names]

    header = "| Drones | Targets | Runs | " + " | ".join(
        _AGENT_LABELS.get(n, n) for n in names
    ) + " |"
    sep = "|---|---|---|" + "---|" * len(names)

    lines = [
        "## Scaling sweep",
        "",
        "Cells show `success% · mean latency s · mean points`. A success rate "
        "below 100 % is the planner failing or timing out at that scale.",
        "",
        header,
        sep,
    ]
    for p in points:
        cells = []
        for n in names:
            a = p["agents"].get(n)
            if not a or (a["runs_successful"] + a["runs_failed"]) == 0:
                cells.append("—")
                continue
            cells.append(
                f"{a['success_rate']*100:.0f}% · "
                f"{a['latency_s']['mean']}s · "
                f"{a['points']['mean']}"
            )
        lines.append(
            f"| {p['drones']} | {p['targets']} | {p['runs']} | " + " | ".join(cells) + " |"
        )
    lines.append("")
    return lines


def render_summary_markdown(manifest: dict) -> str:
    batch = manifest["batch"]
    cfg = manifest["config"]
    agg = manifest["aggregate"]
    r = agg["reasoning"]
    v = agg["langgraph_v4"]
    c = agg["crewai"]
    h2h = agg["head_to_head"]
    pts = h2h["points"]

    scores = {
        "Reasoning": pts.get("reasoning_wins", 0),
        "LangGraph v4": pts.get("langgraph_v4_wins", 0),
        "CrewAI": pts.get("crewai_wins", 0),
    }
    max_wins = max(scores.values())
    leaders = [name for name, w in scores.items() if w == max_wins]
    winner_line = "Tied" if len(leaders) > 1 else leaders[0]

    def _bold_if(val, condition):
        return f"**{val}**" if condition else str(val)

    lines = [
        f"# Benchmark Summary: {batch['name']}",
        "",
        f"**Started**: {batch['started_at']}",
        f"**Ended**:   {batch['ended_at']}",
        f"**Duration**: {_duration_str(batch['started_at'], batch['ended_at'])}",
        f"**Runs**: {cfg['runs_completed']} completed / {cfg['runs_requested']} requested",
        f"**Seed**: {cfg['seed']}",
        f"**Output**: `{batch['output_dir']}`",
        f"**Completed cleanly**: {batch['completed']}",
        "",
        "## Head-to-head (points, higher is better)",
        "",
        "| Outcome | Count |",
        "|---|---|",
        f"| All three succeeded | {h2h['all_succeeded']} |",
        f"| All three failed    | {h2h['all_failed']} |",
        "",
        "| Points winner | Runs |",
        "|---|---|",
        f"| Reasoning wins    | {_bold_if(pts.get('reasoning_wins', 0), winner_line == 'Reasoning')} |",
        f"| LangGraph v4 wins | {_bold_if(pts.get('langgraph_v4_wins', 0), winner_line == 'LangGraph v4')} |",
        f"| CrewAI wins       | {_bold_if(pts.get('crewai_wins', 0), winner_line == 'CrewAI')} |",
        f"| Ties              | {pts.get('ties', 0)} |",
        "",
        "## Aggregate metrics (successful runs only)",
        "",
        "| Metric | Reasoning | LangGraph v4 | CrewAI |",
        "|---|---|---|---|",
        f"| Success rate     | {r['success_rate']*100:.1f} % ({r['runs_successful']}/{r['runs_successful']+r['runs_failed']}) "
        f"| {v['success_rate']*100:.1f} % ({v['runs_successful']}/{v['runs_successful']+v['runs_failed']}) "
        f"| {c['success_rate']*100:.1f} % ({c['runs_successful']}/{c['runs_successful']+c['runs_failed']}) |",
        f"| Mean points      | {r['points']['mean']}  | {v['points']['mean']}  | {c['points']['mean']}  |",
        f"| Median points    | {r['points']['median']}    | {v['points']['median']}    | {c['points']['median']}    |",
        f"| Mean fuel (km)   | {r['fuel_km']['mean']} | {v['fuel_km']['mean']} | {c['fuel_km']['mean']} |",
        f"| Mean coverage    | {r['coverage']['mean']*100:.0f} %  | {v['coverage']['mean']*100:.0f} %  | {c['coverage']['mean']*100:.0f} %  |",
        f"| Mean latency (s) | {r['latency_s']['mean']}  | {v['latency_s']['mean']}  | {c['latency_s']['mean']}  |",
        f"| p95 latency  (s) | {r['latency_s'].get('p95', 0.0)}  | {v['latency_s'].get('p95', 0.0)}  | {c['latency_s'].get('p95', 0.0)}  |",
        f"| Valid-solution rate | {r['valid_solution_rate']*100:.1f} % | {v['valid_solution_rate']*100:.1f} % | {c['valid_solution_rate']*100:.1f} % |",
        "",
        *_render_planner_comparison(agg),
        *_render_scaling_section(agg),
        "## Configuration",
        "",
        f"- {cfg['generator']['targets']} targets, {cfg['generator']['airports']} airports, "
        f"{cfg['generator']['sams']} SAMs, {cfg['generator']['drones']} drones per environment",
        f"- Bounding box: lat [{cfg['generator']['bbox']['lat_min']}, {cfg['generator']['bbox']['lat_max']}] "
        f"× lng [{cfg['generator']['bbox']['lng_min']}, {cfg['generator']['bbox']['lng_max']}] "
        f"(~{cfg['generator']['box_size_km_approx']} km diagonal)",
        f"- Per-agent timeout: {cfg['execution']['per_agent_timeout_s']} s",
        f"- Retry on transient errors: {cfg['execution']['max_retries']} attempt(s)",
        "",
        f"## Low-feasibility runs: {agg['low_feasibility_runs']} / {cfg['runs_completed']} (segment out for clean analysis)",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry point (batch driver)
# ---------------------------------------------------------------------------


_STOP = False  # set by SIGINT handler
_LAST_PROGRESS_TS = 0.0


def _set_stop(*_args) -> None:
    global _STOP
    _STOP = True
    print("\n[interrupt] SIGINT received; finishing current run then exiting.",
          file=sys.stderr, flush=True)


def _heartbeat_loop(total_runs: int, get_run_index) -> None:
    """Daemon thread: emit a heartbeat if no progress in HEARTBEAT_INTERVAL_S.
    Reads _LAST_PROGRESS_TS; single-float read is atomic under the GIL — no lock needed.
    """
    while not _STOP:
        time.sleep(60)
        if _STOP:
            return
        if _LAST_PROGRESS_TS == 0.0:
            continue
        idle = time.monotonic() - _LAST_PROGRESS_TS
        if idle >= HEARTBEAT_INTERVAL_S:
            idx = get_run_index()
            print(f"[heartbeat] run {idx}/{total_runs} in progress for {idle:.0f}s",
                  file=sys.stderr, flush=True)


def _params_from_args(args: argparse.Namespace) -> dict:
    return {
        "airports": args.airports, "targets": args.targets,
        "sams": args.sams, "drones": args.drones,
        "bbox": args.bbox, "fuel_budget_km": args.fuel_budget_km,
        "fuel_budget_km_range": args.fuel_budget_km_range,
        "max_regen_attempts": args.max_regen_attempts,
        "min_reachable_targets": args.min_reachable_targets,
    }


def _run_dry(args: argparse.Namespace, batch_seed: int) -> None:
    """Generate all envs and print one line each; skip HTTP + output files."""
    params = _params_from_args(args)
    for i in range(args.runs):
        _, meta = accept_environment(batch_seed, i, params)
        sub = derive_sub_seed(batch_seed, i, meta["regen_attempts"])
        print(f"[{i+1}/{args.runs}] sub_seed={sub} "
              f"reachable={meta['reachable_target_count']} "
              f"low_feasibility={meta['low_feasibility']}", flush=True)
    print("[dry-run] done; no HTTP calls made.", flush=True)


_PROGRESS_ABBREV = {
    "reasoning": "reason",
    "langgraph_v4": "lg_v4",
    "crewai": "crew",
    "gnn": "gnn",
    "gnn_decoder": "gnn_dec",
    "solver_control": "solver",
}


def _print_progress(i: int, total: int, rec: dict) -> None:
    """One line per run, covering whichever entrants this batch is running."""
    parts = []
    for name in ALL_AGENTS:
        block = rec.get(name)
        if not block:
            continue
        if block.get("success"):
            detail = f"{block.get('latency_s', 0.0):.1f}s/{block.get('total_points', '?')}pts"
        else:
            detail = "FAIL"
        parts.append(f"{_PROGRESS_ABBREV.get(name, name)}={detail}")

    scale = rec.get("scale_point") or {}
    scale_str = f" d={scale['drones']} t={scale['targets']}" if scale else ""
    print(f"[{i+1}/{total}] seed={rec['sub_seed']}{scale_str} " + " ".join(parts),
          flush=True)


def _build_config_block(args: argparse.Namespace, batch_seed: int,
                        runs_completed: int) -> dict:
    lat_min, lat_max, lng_min, lng_max = args.bbox
    return {
        "seed": batch_seed,
        "runs_requested": args.runs,
        "runs_completed": runs_completed,
        "server_url": args.server_url,
        "prompt": args.prompt,
        "generator": {
            "airports": args.airports,
            "targets": args.targets,
            "sams": args.sams,
            "drones": args.drones,
            "bbox": {"lat_min": lat_min, "lat_max": lat_max,
                     "lng_min": lng_min, "lng_max": lng_max},
            "box_size_km_approx": _box_size_km_approx(args.bbox),
            "default_drone": {
                "fuel_budget_km": args.fuel_budget_km,
                "fuel_budget_km_range": list(args.fuel_budget_km_range) if args.fuel_budget_km_range else None,
                "speed_kmh": DEFAULT_SPEED_KMH,
                "enabled": True,
            },
        },
        "execution": {
            "per_agent_timeout_s": args.per_agent_timeout_s,
            "max_retries": args.max_retries,
            "inter_run_delay_s": args.inter_run_delay,
            "max_regen_attempts_per_slot": args.max_regen_attempts,
            "min_reachable_targets": args.min_reachable_targets,
        },
    }


def _scale_points(args: argparse.Namespace) -> list[tuple[int, int]]:
    """Resolve (drones, targets) pairs for the sweep.

    Without --drone-sweep this is a single point from --drones/--targets, so
    existing invocations behave exactly as before.
    """
    drone_values = getattr(args, "drone_sweep_values", None) or [args.drones]
    target_values = getattr(args, "target_sweep_values", None) or [args.targets]
    if len(target_values) == 1:
        target_values = target_values * len(drone_values)
    return list(zip(drone_values, target_values))


def _run_batch_loop(
    args: argparse.Namespace,
    batch_seed: int,
    session: requests.Session,
    write_record,
    current_idx: list,
) -> tuple[list[dict], int]:
    global _LAST_PROGRESS_TS
    base_params = _params_from_args(args)
    entrants = selected_entrants(args)
    compare_options = compare_options_for(args)
    # Optional entrants are validated per batch, so the required-key set has to
    # follow the entrant selection rather than the fixed LLM trio.
    required_keys = tuple(_REQUIRED_RECORD_KEYS_BASE) + tuple(entrants)

    # Each sweep point is (drones, targets). Without --drone-sweep this is a
    # single point using the plain --drones/--targets values.
    scale_points = _scale_points(args)
    records: list[dict] = []
    runs_completed = 0
    total_runs = args.runs * len(scale_points)
    run_index = 0

    for drones, targets in scale_points:
        params = dict(base_params, drones=drones, targets=targets)
        if len(scale_points) > 1:
            print(f"\n[sweep] drones={drones} targets={targets} "
                  f"({args.runs} runs)", flush=True)
        for _ in range(args.runs):
            if _STOP:
                break
            current_idx[0] = run_index + 1
            _LAST_PROGRESS_TS = time.monotonic()
            rec = run_single_batch_step(
                session, batch_seed, run_index, params, args.prompt,
                args.server_url, write_record, entrants, compare_options,
                required_keys,
            )
            # Stamp the scale point so the report can group by it.
            rec["scale_point"] = {"drones": drones, "targets": targets}
            records.append(rec)
            runs_completed += 1
            _print_progress(run_index, total_runs, rec)
            _LAST_PROGRESS_TS = time.monotonic()
            run_index += 1
            if run_index < total_runs and args.inter_run_delay > 0:
                time.sleep(args.inter_run_delay)
        if _STOP:
            break
    return records, runs_completed


def _finalize_batch(
    args: argparse.Namespace, batch_seed: int, batch_dir: Path,
    started_at: str, records: list[dict], runs_completed: int,
) -> None:
    aggregate = aggregate_runs(records)
    config = _build_config_block(args, batch_seed, runs_completed)
    batch_meta = {
        "name": args.name,
        "started_at": started_at,
        "ended_at": utc_now_iso(),
        "completed": not _STOP,
        "output_dir": str(batch_dir),
    }
    manifest = build_manifest(config, batch_meta, aggregate)
    write_manifest_atomic(batch_dir, manifest)
    summary = render_summary_markdown(manifest)
    (batch_dir / "summary.md").write_text(summary)
    # FR-015: summary prints to stdout on BOTH success and interrupt paths.
    print("\n" + summary, flush=True)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    batch_seed = args.seed if args.seed is not None else random.randrange(2**31)
    print(f"[startup] batch_seed={batch_seed} runs={args.runs} name={args.name}",
          flush=True)

    if args.dry_run:
        _run_dry(args, batch_seed)
        return

    assert_server_reachable(args.server_url)
    batch_dir = create_batch_dir(args.output_dir, args.name)
    print(f"[startup] output={batch_dir}", flush=True)

    write_record, close_writer = open_jsonl_writer(batch_dir)
    signal.signal(signal.SIGINT, _set_stop)

    import threading
    current_idx = [0]
    threading.Thread(
        target=_heartbeat_loop,
        args=(args.runs, lambda: current_idx[0]),
        daemon=True,
    ).start()

    started_at = utc_now_iso()
    session = requests.Session()
    try:
        records, runs_completed = _run_batch_loop(
            args, batch_seed, session, write_record, current_idx,
        )
    finally:
        close_writer()
    _finalize_batch(args, batch_seed, batch_dir, started_at, records, runs_completed)


if __name__ == "__main__":
    main()
