"""
Reasoning Agent for ISR Mission Planning

A single agent with tools that can reason about any mission planning request.
No complex multi-agent orchestration - just one smart agent that thinks and acts.
"""

import functools
import re
import sys
from typing import Any, Dict, List, Optional, Set

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

# Import agent memory
from server.agents.agent_memory import get_memory_for_prompt, load_memory, save_memory
from server.agents.drone_config import resolve_fuel_budget

# Import existing solver infrastructure
from server.solver.constraints import validate_and_normalize_constraints
from server.solver.route_evaluation import evaluate_route, route_animation_frame
from server.solver.solver_bridge import (
    get_current_matrix,
)
from server.solver.solver_bridge import (
    solve_mission_with_allocation as _planner_solve_mission_with_allocation,
)


@functools.wraps(_planner_solve_mission_with_allocation)
def solve_mission_with_allocation(*args, **kwargs):
    """The planner call every agent tool uses. Records the SAM wrap polygons the
    planner returns, so the agent's reply carries them like Run Planner's does
    (the UI draws them as the dashed SAM boundary)."""
    result = _planner_solve_mission_with_allocation(*args, **kwargs)
    if isinstance(result, dict):
        _remember_wrapped_polygons(result.get("wrapped_polygons"))
    return result


def _remember_wrapped_polygons(polygons) -> None:
    if polygons:
        _mission_context["wrapped_polygons"] = polygons


# ============================================================================
# MISSION COMMAND MEMORY - persist last command across stateless calls
# ============================================================================

MISSION_COMMAND_CATEGORY = "mission_command"


def _is_mission_command(text: str) -> bool:
    """Check if the user message is an actual mission command (not a question or follow-up)."""
    text_lower = text.strip().lower()
    # Questions are not mission commands
    if text_lower.startswith(
        (
            "what",
            "how",
            "why",
            "when",
            "where",
            "who",
            "which",
            "can you",
            "could you",
            "do you",
            "is the",
            "are the",
            "tell me",
        )
    ):
        return False
    if text_lower.endswith("?"):
        return False
    # Must contain mission-related keywords
    mission_keywords = [
        "visit",
        "solve",
        "plan",
        "priority",
        "target",
        "drone",
        "mission",
        "start with",
        "only",
        "exclude",
        "allocat",
        "route",
        "optimize",
        "greedy",
        "balanced",
        "geographic",
    ]
    return any(kw in text_lower for kw in mission_keywords)


def _store_mission_command(command: str):
    """Store the current mission command in agent memory, replacing any previous one.
    Only stores actual mission commands, not questions or follow-ups."""
    if not _is_mission_command(command):
        print(
            f"📝 Skipping memory store (not a mission command): {command[:80]}",
            file=sys.stderr,
        )
        return

    memories = load_memory()
    # Remove any existing mission_command entries
    memories = [m for m in memories if m.get("category") != MISSION_COMMAND_CATEGORY]
    # Add the new command
    memories.append(
        {
            "id": max((m.get("id", 0) for m in memories), default=0) + 1,
            "content": command,
            "category": MISSION_COMMAND_CATEGORY,
            "timestamp": __import__("datetime").datetime.now().isoformat(),
        }
    )
    save_memory(memories)
    print(f"📝 Stored mission command in memory: {command[:80]}", file=sys.stderr)


# ============================================================================
# TOOLS - These are the capabilities the agent can use
# ============================================================================

# Global state for the current mission context
_mission_context: Dict[str, Any] = {}

# Accumulator for solved routes - stores trajectory data for UI rendering
_solved_routes: Dict[str, Dict[str, Any]] = {}


def _soften_certainty_language(text: Any) -> str:
    """Avoid claiming mathematical optimality unless a tool explicitly proves it."""
    out = str(text)
    replacements = [
        (
            r"\bglobally optimal\b",
            "best found by the current planner pipeline",
        ),
        (
            r"\bthe optimal route\b",
            "the best route found by the current planner pipeline",
        ),
        (
            r"\ban optimal route\b",
            "a strong route found by the current planner pipeline",
        ),
        (
            r"\boptimal route\b",
            "best route found by the current planner pipeline",
        ),
        (
            r"\boptimal solution\b",
            "best solution found by the current planner pipeline",
        ),
        (
            r"\bbest possible\b",
            "best found by the current planner pipeline",
        ),
        (
            r"\balready optimal\b",
            "already processed by the current planner pipeline",
        ),
        (
            r"\bno further improvements possible\b",
            "the current planner pipeline did not find further improvements",
        ),
        (
            r"\bno better solution available\b",
            "no better solution was found by the current planner pipeline",
        ),
        (
            r"\bnot explicitly certified as best found by the current planner pipeline\b",
            "not globally certified",
        ),
        (
            r"\bnot certified as best found by the current planner pipeline\b",
            "not globally certified",
        ),
    ]
    for pattern, replacement in replacements:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
    return out


def set_mission_context(
    env: Dict,
    drone_configs: Dict,
    distance_matrix: Optional[Dict] = None,
    mission_id: Optional[str] = None,
    time_sync: Optional[Dict[str, Any]] = None,
    checkpoint_replan: Optional[Dict[str, Any]] = None,
    current_routes: Optional[Dict[str, List[str]]] = None,
):
    """Set the mission context for tools to use.

    current_routes: each drone's route as the operator currently sees it
    ({did: [start, ..., end]}), for the route-edit tools.

    checkpoint_replan: for a mid-mission turn, Run Planner's checkpoint fields
    ({is_checkpoint_replan, visited_targets, repair_routes}); solve_full_mission
    forwards them so its planner call matches Run Planner's exactly.

    For an agent-driven continue, the frontend has already cut the mission and passes the
    CUT env here (drone start = a D{did}_START synthetic node at the cut, targets filtered,
    fuel carried). So the agent's normal solve tools replan from the cut automatically —
    there is no separate pause/continue tool and no freeze-frame to read.
    """
    global _mission_context, _solved_routes

    # Note: mission-wide cruise altitude (cruise_altitude_km) is handled
    # entirely inside calculate_sam_aware_matrix() (server/solver/
    # sam_distance_matrix.py), which commit_ordered_plan and this agent's
    # other solving tools call directly — the single choke point every
    # distance-matrix computation in this codebase funnels through. Do NOT
    # shrink here too — see that function's comment for why.
    #
    # Known minor gap (out of scope for now): if a caller passes a
    # PRE-COMPUTED distance_matrix into this function (see req.distance_matrix
    # in server/api/agent_routes.py's /api/agents/reason handler), the
    # get_distance query tool (reads _mission_context["distance_matrix"]
    # directly, not calculate_sam_aware_matrix) could report a stale,
    # unshrunk distance for an altitude-affected SAM. Doesn't affect actual
    # routing/solving — only that one inspection tool's answer.
    _mission_context = {
        "environment": env,
        "drone_configs": drone_configs,
        "distance_matrix": distance_matrix,
        "mission_id": mission_id,
        "time_sync": time_sync,
        "checkpoint_replan": checkpoint_replan or {},
        # None = the caller didn't say what the canvas shows (unknown, not empty).
        "current_routes": (
            None
            if current_routes is None
            else {str(k): [str(w) for w in v] for k, v in current_routes.items()}
        ),
        "changed_drone_configs": False,
    }
    # Clear solved routes when starting a new mission
    _solved_routes = {}


def get_solved_routes() -> Dict[str, Dict[str, Any]]:
    """Get all routes that were solved during reasoning."""
    return _solved_routes


def store_solved_route(drone_id: str, route_data: Dict[str, Any], append: bool = False):
    """Store a solved route for later retrieval (and mark the drone as changed
    this turn — only changed drones are sent back to the UI).

    Args:
        drone_id: The drone ID
        route_data: Route data dict with route, fuel_used, points, etc.
        append: If True and routes connect (existing ends where new starts), append.
                Otherwise, replace any existing route.
    """

    _mission_context.setdefault("routes_changed_this_turn", set()).add(str(drone_id))
    if append and drone_id in _solved_routes:
        existing = _solved_routes[drone_id]
        existing_route = existing.get("route", [])
        new_route = route_data.get("route", [])

        # Only append if routes actually connect (existing ends where new starts)
        if existing_route and new_route and existing_route[-1] == new_route[0]:
            # Merge routes - remove duplicate checkpoint
            merged_route = existing_route + new_route[1:]

            existing["route"] = merged_route
            existing["fuel_used"] = existing.get("fuel_used", 0) + route_data.get(
                "fuel_used", 0
            )
            existing["points"] = existing.get("points", 0) + route_data.get("points", 0)
            existing["targets_visited"] = existing.get(
                "targets_visited", []
            ) + route_data.get("targets_visited", [])

            # Merge trajectories if present
            existing_traj = existing.get("trajectory", [])
            new_traj = route_data.get("trajectory", [])
            if existing_traj and new_traj:
                existing["trajectory"] = existing_traj + new_traj
            elif new_traj:
                existing["trajectory"] = new_traj

            existing["end"] = route_data.get("end")
        else:
            # Routes don't connect - replace instead of creating garbage
            _solved_routes[drone_id] = route_data
    else:
        _solved_routes[drone_id] = route_data


def clear_solved_routes():
    """Clear all stored routes. Call at start of new agent run."""
    global _solved_routes
    _solved_routes = {}


def set_solved_routes(routes: Dict[str, Dict[str, Any]]):
    """Seed the route accumulator from a prior session (continue a mission).

    A followup turn must edit the EXISTING plan, not a blank one — so before the
    agent loop we load the session's last solution here. Tools (set_route,
    solve_full_mission, …) then build on it.
    """
    global _solved_routes
    _solved_routes = dict(routes or {})


def _route_list(route_data: Dict[str, Any]) -> List[str]:
    route = route_data.get("route") or route_data.get("waypoints") or []
    if isinstance(route, str):
        return [s.strip() for s in route.split(",") if s.strip()]
    if isinstance(route, list):
        return [str(w) for w in route]
    return []


def _routes_from_current_starts(
    routes: Dict[str, Dict[str, Any]], drone_configs: Dict[str, Any]
) -> Dict[str, Dict[str, Any]]:
    """Keep only routes whose first waypoint is the drone's CURRENT start node."""
    configs = drone_configs or {}
    kept = {}
    for did, rd in (routes or {}).items():
        start = str(
            (configs.get(did) or configs.get(str(did)) or {}).get("start_airport") or ""
        )
        route = _route_list(rd)
        if start and route and str(route[0]) == start:
            kept[did] = rd
    return kept


def _route_targets(route: List[Any]) -> List[str]:
    return [
        str(w) for w in route if str(w).startswith("T") and not str(w).startswith("TK")
    ]


def _route_points(route_data: Dict[str, Any], target_priority: Dict[str, int]) -> int:
    if route_data.get("points") is not None:
        return int(route_data.get("points") or 0)
    return sum(
        target_priority.get(t, 0) for t in _route_targets(_route_list(route_data))
    )


def _route_fuel(route_data: Dict[str, Any]) -> float:
    return float(route_data.get("distance", route_data.get("fuel_used", 0.0)) or 0.0)


def _build_route_delta(
    before: Dict[str, Dict[str, Any]],
    after: Dict[str, Dict[str, Any]],
    target_priority: Dict[str, int],
) -> Dict[str, Any]:
    """Compact route delta for short-term mission memory."""
    drones = sorted(set(before or {}) | set(after or {}))
    per_drone: Dict[str, Any] = {}
    changed = False
    added_all: Set[str] = set()
    removed_all: Set[str] = set()
    fuel_delta = 0.0
    points_delta = 0

    for did in drones:
        prev = before.get(did, {}) if isinstance(before, dict) else {}
        cur = after.get(did, {}) if isinstance(after, dict) else {}
        prev_route = _route_list(prev)
        cur_route = _route_list(cur)
        prev_targets = set(_route_targets(prev_route))
        cur_targets = set(_route_targets(cur_route))
        added = sorted(cur_targets - prev_targets)
        removed = sorted(prev_targets - cur_targets)
        d_fuel = _route_fuel(cur) - _route_fuel(prev)
        d_points = _route_points(cur, target_priority) - _route_points(
            prev, target_priority
        )
        route_changed = prev_route != cur_route
        changed = changed or route_changed
        added_all.update(added)
        removed_all.update(removed)
        fuel_delta += d_fuel
        points_delta += d_points

        per_drone[did] = {
            "route_changed": route_changed,
            "previous_route": prev_route,
            "current_route": cur_route,
            "added_targets": added,
            "removed_targets": removed,
            "fuel_delta": round(d_fuel, 3),
            "points_delta": d_points,
            "previous_first_target": (
                _route_targets(prev_route)[0] if _route_targets(prev_route) else None
            ),
            "current_first_target": (
                _route_targets(cur_route)[0] if _route_targets(cur_route) else None
            ),
        }

    return {
        "route_changed": changed,
        "added_targets": sorted(added_all),
        "removed_targets": sorted(removed_all),
        "fuel_delta": round(fuel_delta, 3),
        "points_delta": points_delta,
        "drones": per_drone,
    }


def _build_turn_memory(
    *,
    user_request: str,
    response: str,
    previous_routes: Dict[str, Dict[str, Any]],
    current_routes: Dict[str, Dict[str, Any]],
    routes_changed_for_ui: bool,
    target_priority: Dict[str, int],
) -> Dict[str, Any]:
    delta = _build_route_delta(previous_routes, current_routes, target_priority)
    return {
        "user_request": user_request,
        "response": response,
        "routes_changed_for_ui": bool(routes_changed_for_ui),
        "route_changed": bool(delta["route_changed"]),
        "route_delta": delta,
    }


def _route_refuel_stops(route: List[Any], route_data: Dict[str, Any]) -> List[str]:
    refuel_indices = route_data.get("refuel_indices") or []
    return [
        str(route[i])
        for i in refuel_indices
        if isinstance(i, int) and 0 <= i < len(route)
    ]


def _route_fuel_status(route_data: Dict[str, Any]) -> Dict[str, Any]:
    """Return per-leg fuel feasibility for an agent route summary."""
    total = float(route_data.get("distance", route_data.get("fuel_used", 0.0)) or 0.0)
    budget = float(route_data.get("fuel_budget", 0.0) or 0.0)
    leg_distances = route_data.get("leg_distances") or []
    if leg_distances:
        legs = [float(d or 0.0) for d in leg_distances]
        feasible = all(d <= budget + 1e-6 for d in legs)
        detail = ", ".join(
            f"L{i}: {d:.1f}/{budget:.1f}" for i, d in enumerate(legs, start=1)
        )
        return {
            "total": total,
            "budget": budget,
            "legs": legs,
            "feasible": feasible,
            "detail": detail,
        }
    feasible = total <= budget + 1e-6 if budget else False
    return {
        "total": total,
        "budget": budget,
        "legs": [],
        "feasible": feasible,
        "detail": f"{total:.1f}/{budget:.1f}",
    }


@tool
def get_current_mission_status() -> str:
    """Summarize the currently committed agent route memory for this mission.

    Use this for follow-up questions like:
    - "How many targets have you visited?"
    - "What route did D1 get?"
    - "Why did you not visit T2 first?"
    - "Which targets are missing?"
    - "What changed?"
    - "Did the last answer change the route?"

    This reads the carried per-mission session routes. It does not solve or change
    the mission.
    """
    env = _mission_context.get("environment", {})
    target_ids = {
        str(t.get("id", t.get("label")))
        for t in env.get("targets", [])
        if t.get("id", t.get("label")) is not None
    }
    target_priority = {
        str(t.get("id", t.get("label"))): int(t.get("priority", 0) or 0)
        for t in env.get("targets", [])
        if t.get("id", t.get("label")) is not None
    }
    routes = get_solved_routes()
    previous_turn = _mission_context.get("previous_turn") or {}

    if not routes:
        return (
            "No committed agent route is stored for this mission yet. If the last "
            "solve reported infeasible or returned no routes, then zero targets are "
            "currently committed as visited by the agent."
        )

    lines = ["Current committed agent mission status:"]
    if previous_turn:
        delta = previous_turn.get("route_delta") or {}
        lines.append(f"Previous completed command: {previous_turn.get('user_request')}")
        lines.append(
            "Previous completed turn changed route: "
            f"{'YES' if previous_turn.get('route_changed') else 'NO'}"
        )
        lines.append(
            "Previous completed turn changed canvas solution: "
            f"{'YES' if previous_turn.get('routes_changed_for_ui') else 'NO'}"
        )
        if delta:
            lines.append(
                "Previous route delta: "
                f"added={delta.get('added_targets') or []}, "
                f"removed={delta.get('removed_targets') or []}, "
                f"points_delta={delta.get('points_delta', 0)}, "
                f"fuel_delta={float(delta.get('fuel_delta', 0.0)):.1f}"
            )
    total_targets: Set[str] = set()
    total_points = 0
    total_fuel = 0.0

    for did, rd in sorted(routes.items()):
        if not isinstance(rd, dict):
            continue
        route = _route_list(rd)

        visited = _route_targets(route)
        points = _route_points(rd, target_priority)
        fuel_status = _route_fuel_status(rd)
        fuel = fuel_status["total"]
        refuel_stops = _route_refuel_stops(route, rd)

        total_targets.update(visited)
        total_points += points
        total_fuel += fuel

        first_target = visited[0] if visited else "none"
        route_text = " -> ".join(str(w) for w in route) if route else "(empty)"
        lines.append(
            f"D{did}: {len(visited)} target(s), {points} point(s), "
            f"{fuel:.1f} fuel, first_target={first_target}, "
            f"refuel_stops={refuel_stops if refuel_stops else 'none'}, "
            f"feasible={'YES' if fuel_status['feasible'] else 'NO'} "
            f"({fuel_status['detail']})"
        )
        lines.append(f"  Route: {route_text}")
        lines.append(f"  Targets: {visited if visited else 'none'}")

    missing = sorted(target_ids - total_targets)
    lines.append(
        f"Mission total: {len(total_targets)}/{len(target_ids)} target(s), "
        f"{total_points} point(s), {total_fuel:.1f} fuel."
    )
    lines.append(f"Unvisited targets: {missing if missing else 'none'}")
    return "\n".join(lines)


@tool
def get_targets() -> str:
    """Get all targets with their IDs, priorities, positions, and types.

    Returns a list of targets that can be visited by drones.
    Use this to understand what targets exist and their properties.
    """
    env = _mission_context.get("environment", {})
    targets = env.get("targets", [])

    result = []
    for t in targets:
        tid = t.get("id", t.get("label", "?"))
        priority = t.get("priority", t.get("value", 5))
        lat, lng = t.get("lat", 0), t.get("lng", 0)
        ttype = t.get("type", "a")
        result.append(
            f"{tid}: priority={priority}, pos=({lat:.4f}, {lng:.4f}), type={ttype}"
        )

    return "\n".join(result) if result else "No targets found."


@tool
def get_airports() -> str:
    """Get all airports with their IDs and positions.

    Airports are where drones start and must return to.
    """
    env = _mission_context.get("environment", {})
    airports = env.get("airports", [])

    result = []
    for a in airports:
        aid = a.get("id", a.get("label", "?"))
        lat, lng = a.get("lat", 0), a.get("lng", 0)
        result.append(f"{aid}: pos=({lat:.4f}, {lng:.4f})")

    return "\n".join(result) if result else "No airports found."


@tool
def get_drones() -> str:
    """Get all enabled drones with their full configuration.

    Returns, per drone:
    - Fuel budget (full-tank range, refreshed at each refuel stop)
    - Start / end airport
    - Target access (which target types this drone may visit)
    - Refuel stops (airports where it tops up to a full tank, in order) — empty
      means no refuel
    - Loiter (dwell time per target type), if loiter is enabled

    These ARE the constraints the mission was configured with — honor them when
    you plan (a refuel drone must route through its stop; loiter consumes fuel).
    """
    configs = _mission_context.get("drone_configs", {})

    result = []
    for did, cfg in sorted(configs.items()):
        if not cfg.get("enabled", did == "1"):
            continue
        fuel = resolve_fuel_budget(cfg, drone_id=str(did))
        start = cfg.get("startAirport", cfg.get("start_airport", "A1"))
        end = cfg.get("endAirport", cfg.get("end_airport", start))
        access = cfg.get("target_access", {})
        allowed_types = [t for t, v in access.items() if v] if access else ["all"]

        line = (
            f"D{did}: fuel={fuel}, start={start}, end={end}, can_visit={allowed_types}"
        )

        # Refuel stops (multi-stop refuel_stops[] is the model; refuel_airport is
        # the legacy single-stop fallback). "-" / "" = no refuel.
        stops = cfg.get("refuel_stops")
        if isinstance(stops, list):
            stops = [s for s in stops if s and s != "-"]
        else:
            stops = []
        legacy = (cfg.get("refuel_airport") or "").strip()
        if not stops and legacy and legacy != "-":
            stops = [legacy]
        line += f", refuel_stops={stops if stops else 'none'}"

        # Loiter (only when enabled and non-zero) — it consumes fuel per visit.
        if cfg.get("loiter_enabled"):
            lt = cfg.get("loiter_times", {}) or {}
            nz = {k: v for k, v in lt.items() if v}
            if nz:
                line += f", loiter={nz}"

        # Cruise altitude: each drone plans around the SAMs at its own altitude.
        from server.solver.altitude import drone_altitude_km

        alt = drone_altitude_km(cfg, _mission_context.get("environment") or {})
        if alt > 0:
            line += f", cruise_altitude_km={alt:g}"
        # Aircraft type + sensors (catalog, nominal), when chosen.
        if cfg.get("platform_id"):
            line += f", platform={cfg['platform_id']} sensors={','.join(cfg.get('sensor_ids') or []) or 'none'}"

        result.append(line)

    return "\n".join(result) if result else "No enabled drones found."


@tool
def get_sams() -> str:
    """Get all SAM (Surface-to-Air Missile) zones that drones must avoid.

    Returns SAM positions and ranges. Drones cannot fly through these zones.
    """
    env = _mission_context.get("environment", {})
    sams = env.get("sams", [])

    result = []
    for i, sam in enumerate(sams):
        center = sam.get("center", {})
        lat = center.get("lat", 0)
        lng = center.get("lng", 0)
        range_val = sam.get("range_km", sam.get("range", 0))
        result.append(f"SAM{i+1}: pos=({lat:.4f}, {lng:.4f}), range={range_val}km")

    return "\n".join(result) if result else "No SAM zones."


# ============================================================================
# ROUTE EDITS — the operator's sequence-bar operation, for the agent. Same
# evaluator (server/solver/route_evaluation.py) the sequence bar uses. A route
# edit changes only a drone's REMAINING route from where it is now; the
# environment is untouched (visited stays visited, unvisited stays unvisited).
# ============================================================================


def _drone_id(drone_id: str) -> str:
    did = str(drone_id).strip()
    return did[1:] if did.upper().startswith("D") else did


def _current_start(did: str) -> str:
    cfg = (_mission_context.get("drone_configs") or {}).get(did) or {}
    return str(cfg.get("start_airport") or cfg.get("startAirport") or "")


def _current_route(did: str) -> List[str]:
    """The drone's route as it stands now: this turn's edit/plan if any, else the
    plan the operator sees (sent by the browser as current_routes)."""
    if (
        did in _mission_context.get("routes_changed_this_turn", set())
        and did in _solved_routes
    ):
        return [str(w) for w in _route_list(_solved_routes[did])]
    return [str(w) for w in (_mission_context.get("current_routes") or {}).get(did, [])]


def _evaluate_and_store(did: str, route: List[str]) -> str:
    cfg = (_mission_context.get("drone_configs") or {}).get(did) or {}
    if not cfg or cfg.get("enabled") is False:
        return f"Error: D{did} is not an enabled drone."
    env = _mission_context.get("environment", {})
    result = evaluate_route(
        route, env, did, distance_matrix=get_current_matrix(), drone_config=cfg
    )
    if not result["success"]:
        return f"Error: route {' -> '.join(route)} for D{did} was not applied: {result['error']}"
    fuel_budget = float(cfg.get("start_fuel") or resolve_fuel_budget(cfg, drone_id=did))
    route_data = {
        "route": result["route"],
        "sequence": ",".join(result["route"]),
        "distance": result["distance"],
        "fuel_used": result["distance"],
        "points": result["points"],
        "trajectory": result["trajectory"],
        "waypoint_indices": result["waypoint_indices"],
        "span_distances": result["span_distances"],
        "anim_frame": route_animation_frame(result, env, cfg, did),
        "fuel_budget": fuel_budget,
    }
    store_solved_route(did, route_data)
    feasible = (
        "YES"
        if result["distance"] <= fuel_budget + 1e-6
        else "NO — exceeds remaining fuel"
    )
    return (
        f"COMMITTED route for D{did}: {' -> '.join(result['route'])}\n"
        f"Fuel: {result['distance']:.1f} / {fuel_budget:.1f}, Points: {result['points']}, "
        f"Feasible: {feasible}"
    )


@tool
def set_route(drone_id: str, waypoints: List[str]) -> str:
    """Replace a drone's REMAINING route with exactly these waypoints — the same
    operation as the operator typing a sequence. The drone's current position (its
    start / latest diamond) is added in front automatically; list only what comes
    AFTER it, ending with the landing airport.

    The environment is NOT changed: targets you leave out stay unvisited in the
    mission. Use this for commander orders that dictate the route:
      - "abort, all drones go directly to A1" → set_route(d, ["A1"]) for EVERY drone
      - "D1 visit T8 then land at A2"          → set_route("1", ["T8", "A2"])

    Args:
        drone_id: e.g. "1" or "D1"
        waypoints: ordered target/airport ids after the current position, e.g. ["T8","A2"]
    """
    did = _drone_id(drone_id)
    start = _current_start(did)
    rest = [str(w).strip().upper() for w in waypoints if str(w).strip()]
    if rest and rest[0] == start:
        rest = rest[1:]
    if not rest:
        return "Error: give at least the landing airport."
    return _evaluate_and_store(did, [start, *rest])


@tool
def insert_in_route(drone_id: str, target_id: str, after: Optional[str] = None) -> str:
    """Add ONE target to a drone's current route, keeping the rest of the route as
    it is. Without `after`, it goes where it adds the least distance.

    Args:
        drone_id: e.g. "1"
        target_id: e.g. "T9"
        after: optional waypoint in the route to insert right after
    """
    did = _drone_id(drone_id)
    tid = str(target_id).strip().upper()
    route = _current_route(did)
    if len(route) < 2:
        return f"Error: D{did} has no current route to insert into — use set_route."
    if tid in route:
        return f"D{did}'s route already includes {tid}; nothing changed."
    if after:
        a = str(after).strip().upper()
        if a not in route[:-1]:
            return f"Error: {a} is not in D{did}'s route {' -> '.join(route)}."
        i = route.index(a) + 1
        return _evaluate_and_store(did, route[:i] + [tid] + route[i:])
    env = _mission_context.get("environment", {})
    cfg = (_mission_context.get("drone_configs") or {}).get(did) or {}
    best = None
    for i in range(1, len(route)):
        candidate = route[:i] + [tid] + route[i:]
        r = evaluate_route(
            candidate, env, did, distance_matrix=get_current_matrix(), drone_config=cfg
        )
        if r["success"] and (best is None or r["distance"] < best[0]):
            best = (r["distance"], candidate)
    if best is None:
        return f"Error: no SAM-safe place to insert {tid} into D{did}'s route."
    return _evaluate_and_store(did, best[1])


@tool
def remove_from_route(drone_id: str, target_id: str) -> str:
    """Remove ONE target from a drone's current route, keeping the rest as it is.
    The target stays in the mission, unvisited.

    Args:
        drone_id: e.g. "2"
        target_id: e.g. "T4"
    """
    did = _drone_id(drone_id)
    tid = str(target_id).strip().upper()
    route = _current_route(did)
    if tid not in route[1:-1]:
        return f"D{did}'s route {' -> '.join(route) or '(none)'} does not visit {tid}; nothing changed."
    return _evaluate_and_store(did, [w for w in route if w != tid])


@tool
def set_drone(
    drone_id: str,
    enabled: Optional[bool] = None,
    end_airport: Optional[str] = None,
    cruise_altitude_km: Optional[float] = None,
    platform: Optional[str] = None,
    sensors: Optional[List[str]] = None,
) -> str:
    """Change a drone's settings for the NEXT solve_full_mission / commit_ordered_plan
    (the same settings the operator changes in the Asset tab). Does not plan by
    itself. E.g. "D3 is down" → set_drone("3", enabled=False), then solve_full_mission.
    "D2 fly at 6 km" → set_drone("2", cruise_altitude_km=6), then solve_full_mission:
    each drone plans around the SAMs as they are at its own altitude (a SAM is a
    sphere; higher up its circle is smaller, and above its range it is gone).

    "Make D1 an RQ-4B with SAR only" → set_drone("1", platform="RQ-4B", sensors=["SAR"]):
    the aircraft's nominal values (altitude, speed, fuel = range, runway,
    climb/descent angles) and, per target type, its best sensor's detection
    radius and loiter time are filled in; a target type none of its sensors
    can see is no longer visited. See list_platforms for the ids. Settings
    given in the same call (e.g. cruise_altitude_km) are applied after.

    Args:
        drone_id: e.g. "3"
        enabled: False to take the drone out of the mission
        end_airport: landing airport id
        cruise_altitude_km: the drone's cruise altitude, km above sea level
            (0 = ground level: SAMs at their full size)
        platform: aircraft type id from list_platforms (e.g. "MQ-9A")
        sensors: sensor ids that aircraft carries (default: all of them)
    """
    did = _drone_id(drone_id)
    configs = _mission_context.get("drone_configs") or {}
    if did not in configs:
        return f"Error: D{did} not found."
    cfg = dict(configs[did])
    if platform:
        from server.asset_catalog import (
            CatalogError,
            catalog_platform_sensors,
            drone_fields,
            load_catalog,
        )

        try:
            catalog = load_catalog()
            chosen = (
                list(sensors)
                if sensors is not None
                else catalog_platform_sensors(catalog, str(platform))
            )
            fields = drone_fields(catalog, str(platform), [str(x) for x in chosen])
        except CatalogError as e:
            return f"Error: {e}"
        cfg.update(fields)
        cfg["preset"] = dict(fields)
    if enabled is not None:
        cfg["enabled"] = bool(enabled)
    if end_airport:
        cfg["end_airport"] = str(end_airport).strip().upper()
    if cruise_altitude_km is not None:
        cfg["cruise_altitude_km"] = max(float(cruise_altitude_km), 0.0)
    configs[did] = cfg
    _mission_context["drone_configs"] = configs
    _mission_context["changed_drone_configs"] = True
    if cfg.get("enabled") is False:
        _solved_routes.pop(did, None)
    platform_note = (
        f", platform={cfg['platform_id']} sensors={cfg.get('sensor_ids')}, "
        f"fuel={cfg.get('fuel_budget')} km, speed={float(cfg.get('speed') or 1) * 60:g} km/h"
        if cfg.get("platform_id")
        else ""
    )
    return (
        f"D{did} settings: enabled={cfg.get('enabled', True)}, "
        f"end_airport={cfg.get('end_airport')}, "
        f"cruise_altitude_km={cfg.get('cruise_altitude_km', 0)}{platform_note}"
    )


@tool
def list_platforms() -> str:
    """The aircraft types a drone can be (catalog, NOMINAL public values) and the
    sensors each carries — ids for set_drone(platform=..., sensors=...)."""
    from server.asset_catalog import (
        CatalogError,
        category_ids,
        load_catalog,
        sensor_for_type,
    )

    try:
        catalog = load_catalog()
    except (OSError, ValueError, CatalogError) as e:
        return f"Error: asset catalog unavailable ({e})"
    lines = ["Platforms (nominal):"]
    for p in catalog.get("platforms") or []:
        fuel = p.get("range_km") or float(p["cruise_speed_kmh"]) * float(
            p["endurance_h"]
        )
        lines.append(
            f"- {p['id']} ({p.get('name', p['id'])}): cruise {p['cruise_altitude_km']} km, "
            f"{p['cruise_speed_kmh']} km/h, fuel {fuel:.0f} km, runway {p.get('runway', 'S')}, "
            f"sensors {', '.join(p.get('sensors') or []) or 'none'}"
        )
    cats = catalog.get("target_categories") or []
    if cats:
        lines.append(
            "Target categories (the letter on each target): "
            + ", ".join(
                f"{str(c['id']).upper()} = {c.get('name', c['id'])}" for c in cats
            )
        )
    lines.append(
        "Sensors (per target category: detection km / loiter min; '-' = can't see it):"
    )
    for sen in catalog.get("sensors") or []:
        per = []
        for t in category_ids(catalog):
            v = sensor_for_type(sen, t)
            per.append(
                f"{t.upper()} {v['detection_km']:g}/{v['loiter_min']:g}"
                if v
                else f"{t.upper()} -"
            )
        lines.append(f"- {sen['id']} ({sen.get('name', sen['id'])}): " + ", ".join(per))
    return "\n".join(lines)


@tool
def get_distance(from_point: str, to_point: str) -> str:
    """Get the SAM-aware distance between two waypoints.

    Distance is read from the pre-computed SAM-aware distance matrix.
    Distance = fuel units.

    Args:
        from_point: Airport ID or Target ID (e.g., "A1", "T5")
        to_point: Airport ID or Target ID

    Returns:
        The distance in fuel units.
    """
    matrix_data = _mission_context.get("distance_matrix")
    env = _mission_context.get("environment", {})

    # Try to get from SAM-aware matrix first
    if matrix_data:
        labels = matrix_data.get("labels", [])
        matrix = matrix_data.get("matrix", [])

        if from_point in labels and to_point in labels:
            i = labels.index(from_point)
            j = labels.index(to_point)
            dist = matrix[i][j]
            return f"Distance {from_point} → {to_point}: {dist:.2f} (SAM-aware)"

    # Fallback to haversine distance
    all_targets = {t.get("id", t.get("label")): t for t in env.get("targets", [])}
    all_airports = {a.get("id", a.get("label")): a for a in env.get("airports", [])}
    all_points = {**all_targets, **all_airports}

    if from_point not in all_points:
        return f"Error: Point '{from_point}' not found."
    if to_point not in all_points:
        return f"Error: Point '{to_point}' not found."

    p1 = all_points[from_point]
    p2 = all_points[to_point]

    import math

    # Haversine formula for lat/lng distance in km
    EARTH_RADIUS_KM = 6371.0088
    lat1, lng1 = p1["lat"], p1["lng"]
    lat2, lng2 = p2["lat"], p2["lng"]
    phi1 = lat1 * math.pi / 180
    phi2 = lat2 * math.pi / 180
    dphi = (lat2 - lat1) * math.pi / 180
    dlam = (lng2 - lng1) * math.pi / 180
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    )
    dist = 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))

    return f"Distance {from_point} → {to_point}: {dist:.2f}km (haversine fallback)"


@tool
def allocate_targets(strategy: str = "insertion") -> str:
    """Allocate targets among drones using the specified strategy.

    Args:
        strategy: Allocation strategy:
            - "insertion": Cheapest insertion - minimizes total fuel [default, recommended]
            - "greedy": Highest priority to nearest capable drone
            - "balanced": Equal target count per drone
            - "efficient": Maximize priority-per-fuel (auction-based)
            - "exclusive": Handle single-drone-only targets first

    Returns:
        Target allocation for each drone.
    """
    from server.solver.target_allocator import allocate_targets as do_allocate

    env = _mission_context.get("environment", {})
    configs = _mission_context.get("drone_configs", {})
    matrix_data = _mission_context.get("distance_matrix")

    targets = env.get("targets", [])

    try:
        # NOTE: Using old argument pattern that causes this to fail
        # This forces the agent to reason about allocation itself
        # TODO: Fix insertion algorithm to respect fuel budgets, then fix this call
        allocation = do_allocate(  # type: ignore[call-arg]
            targets=targets,
            drone_configs=configs,
            airports=env.get("airports", []),
            strategy=strategy,
            matrix_data=matrix_data,
        )

        result = [f"Allocation (strategy={strategy}):"]
        for did, tids in sorted(allocation.items()):
            if tids:
                # Get priorities for these targets
                target_map = {t["id"]: t for t in targets}
                total_priority = sum(
                    target_map.get(tid, {}).get("priority", 0) for tid in tids
                )
                result.append(f"  D{did}: {tids} (total priority: {total_priority})")
            else:
                result.append(f"  D{did}: no targets")

        return "\n".join(result)

    except Exception as e:
        return f"Error allocating targets: {str(e)}"


# _normalize_constraints moved to server/solver/constraints.py as
# validate_and_normalize_constraints, so v4's route_optimizer_node can share
# the same validated implementation instead of duplicating it. Import at top
# of file; kept as a thin local alias so existing call sites below don't
# need to change.
_normalize_constraints = validate_and_normalize_constraints


def _solve_supports_constraints() -> bool:
    """Does solve_mission_with_allocation accept a `constraints` kwarg yet?

    The solver-side `constraints` DP-pruning param is being implemented in
    parallel (see dp_plan). We introspect so this tool works TODAY (free-target
    optimization via an unconstrained solve) and lights up automatically once
    the solver param lands — no code change here required.
    """
    import inspect

    try:
        return (
            "constraints" in inspect.signature(solve_mission_with_allocation).parameters
        )
    except (TypeError, ValueError):
        return False


@tool
def commit_ordered_plan(
    drone_id: str,
    constraints: Optional[Dict[str, Any]] = None,
    refuel: Optional[Dict[str, Any]] = None,
) -> str:
    """Commit the OPTIMAL full-mission route for a drone, honoring ORDERING/REFUEL
    constraints while the solver optimizes EVERYTHING else.

    This is the order-honoring tool. You do NOT hand it a sequence or pin targets;
    you hand it a small, generic `constraints` dict (translated from the user's
    words) and, only if the user NAMED a refuel, a `refuel` plan. The solver's
    Held-Karp DP then chooses/orders every free (unconstrained) target optimally
    under the pruned search — free targets are NEVER trapped in a leg.

    Args:
        drone_id: Which drone (e.g., "1").
        constraints: The ordering rules, as a JSON dict (all target ids are strings
            like "T7"). Every field is OPTIONAL; omit what the user did not ask for:
              - "first": "T7"          → T7 must be the FIRST target (right after the
                                         start airport).
              - "last":  "T3"          → T3 must be the LAST target before returning.
              - "before": [["A","B"]]  → A must come somewhere before B (not
                                         necessarily adjacent). One pair per rule.
              - "adjacent": [["Y","X"]]→ X must come IMMEDIATELY after Y (nothing
                                         between them).
            Example: {"first":"T7"} or {"before":[["T1","T2"]],"adjacent":[["T5","T6"]]}.
            An empty/None dict means NO ordering constraints — the solver optimizes
            the whole route freely.
        refuel: Optional refuel plan — pass this ONLY when the user explicitly NAMES a
            refuel airport (or one is configured on the drone). Shape:
              {"airport":"TK1", "position":"before_landing"}  → refuel is the last
                  stop before the END airport (leg2 has no targets), OR
              {"airport":"TK1", "position":"after:T4"}         → refuel right after T4
                  (leg1 = targets up to & including T4, leg2 = the rest), OR
              {"airport":"TK1", "slot":3}                      → refuel after the 3rd
                  target.
            If `refuel` is omitted, the tool auto-decides: it adds a refuel ONLY if the
            genuine single-tank optimal route exceeds the fuel budget AND a refuel
            airport is available. Do NOT pass a refuel just because a route is long —
            let the tool decide. Never invent a refuel the user didn't ask for.
    """
    from server.solver.sam_distance_matrix import calculate_sam_aware_matrix
    from server.solver.trajectory_planner import ISRTrajectoryPlanner

    env = _mission_context.get("environment", {})
    configs = _mission_context.get("drone_configs", {})

    did = drone_id.lstrip("D") if drone_id.startswith("D") else drone_id
    drone_cfg = configs.get(did, configs.get(drone_id, {}))
    if not drone_cfg:
        return f"Error: Drone {drone_id} not found."
    if drone_cfg.get("enabled") is False:
        return (
            f"Error: Drone {drone_id} is DISABLED in the mission config and cannot "
            f"be used. Only enabled drones may be planned (see get_drones)."
        )

    fuel_budget = resolve_fuel_budget(drone_cfg, drone_id=str(did))

    airport_ids = {str(a.get("id", a.get("label"))) for a in env.get("airports", [])}
    target_ids = {str(t.get("id", t.get("label"))) for t in env.get("targets", [])}

    start_airport = str(
        drone_cfg.get("startAirport", drone_cfg.get("start_airport", "A1"))
    )
    end_airport = str(
        drone_cfg.get("endAirport", drone_cfg.get("end_airport", start_airport))
    )

    # ---- 1. Normalize + validate the constraints dict into the proven shape. ----
    try:
        norm = _normalize_constraints(constraints, target_ids)
    except ValueError as ve:
        return f"Error: invalid constraints — {ve}"

    # ---- 2. All mission targets (this tool plans the WHOLE mission for the drone,
    #         honoring type access; free targets are chosen by the DP). ----
    access = drone_cfg.get("target_access") or {}

    def _allowed(t: Dict[str, Any]) -> bool:
        if not access:
            return True
        return bool(access.get(str(t.get("type", "a")), True))

    all_targets_list = [t for t in env.get("targets", []) if _allowed(t)]
    if not all_targets_list:
        return f"Error: no targets available to D{did} (check target access)."
    solve_env = {**env, "targets": all_targets_list}
    solver_env = solve_env
    trajectory_env = solve_env
    solve_target_ids = [str(t.get("id", t.get("label"))) for t in all_targets_list]

    # ---- 3. Decide refuel per the refuel_rule (user-named OR single-tank bust). ----
    # A refuel is added iff EITHER a refuel airport is explicitly requested/configured
    # (honor its slot), OR the genuine single-tank optimal route busts the budget.
    # We first compute the SAM-aware matrix for the fuel-bust feasibility check.
    try:
        dist_data = calculate_sam_aware_matrix(solve_env)
    except Exception as e:  # pragma: no cover
        return f"Error: could not compute SAM-aware distances — {e}"
    labels = dist_data.get("labels", [])
    matrix = dist_data.get("matrix", [])
    label_index = {lab: i for i, lab in enumerate(labels)}

    def _d(a: str, b: str) -> float:
        ia, ib = label_index.get(a), label_index.get(b)
        if ia is None or ib is None:
            raise RuntimeError(f"no SAM-aware distance for {a}->{b}")
        return float(matrix[ia][ib])

    def _target_loiter_fuel(target: Dict[str, Any]) -> float:
        if not drone_cfg.get("loiter_enabled"):
            return 0.0
        speed = float(drone_cfg.get("speed", 1) or 1)
        loiter_times = drone_cfg.get("loiter_times", {}) or {}
        t_type = str(target.get("type") or "a").lower()
        return float(loiter_times.get(t_type, 0) or 0) * speed

    # Resolve any user-named / configured refuel airport.
    # A refuel happens ONLY when the user (agent) explicitly asks for one via the
    # `refuel` arg. The drone config carrying refuel_stops/refuel_airport is a
    # CAPABILITY, not a request — it must NOT trigger a refuel on its own (that was
    # the phantom-refuel bug: config capability was read as a user request, which
    # also scattered a "first"/order constraint across the resulting legs).
    named_airport = ""
    named_position = None
    if refuel and isinstance(refuel, dict):
        named_airport = str(refuel.get("airport", "")).strip()
        named_position = refuel.get("position")
        if refuel.get("slot") is not None and named_position is None:
            named_position = f"slot:{refuel.get('slot')}"
    user_named_refuel = bool(named_airport)

    # If a genuine single-tank fuel bust forces a refuel with NO airport named, fall
    # back to the drone's configured refuel airport as the PLACE to refuel (used only
    # inside the needs_refuel_for_fuel branch below, never to trigger a refuel).
    _cfg_stops = drone_cfg.get("refuel_stops")
    if isinstance(_cfg_stops, list):
        _cfg_stops = [s for s in _cfg_stops if s and s != "-"]
    else:
        _cfg_stops = []
    fallback_refuel_airport = (
        _cfg_stops[0] if _cfg_stops else (drone_cfg.get("refuel_airport") or "").strip()
    )
    if fallback_refuel_airport == "-":
        fallback_refuel_airport = ""
    if user_named_refuel and named_airport not in airport_ids:
        return (
            f"Error: refuel airport '{named_airport}' is not in the environment. "
            f"Available airports: {sorted(airport_ids)}"
        )

    # ---- 4. Convert an explicit "first target" into a frozen start-state prefix. ----
    # Semantics: "start/visit T5 first" means fly from the configured start airport
    # to T5, spend that fuel, remove T5 from the remaining target set, and solve the
    # rest from a checkpoint at T5 with reduced leg-1 fuel.
    solver_norm = dict(norm)
    forced_first_target = norm.get("first")
    forced_start_id: Optional[str] = None
    forced_prefix_fuel = 0.0
    forced_first_priority = 0
    if forced_first_target:
        from server.solver.sam_distance_matrix import clear_matrix_cache

        forced_target = next(
            (
                t
                for t in all_targets_list
                if str(t.get("id", t.get("label"))) == forced_first_target
            ),
            None,
        )
        if forced_target is None:
            return f"Error: target {forced_first_target} is not available to D{did}."

        forced_prefix_fuel = _d(
            start_airport, forced_first_target
        ) + _target_loiter_fuel(forced_target)
        if forced_prefix_fuel > fuel_budget + 1e-6:
            return (
                f"NOT committed — D{did} cannot reach {forced_first_target} first: "
                f"required fuel {forced_prefix_fuel:.1f} exceeds available "
                f"{fuel_budget:.1f}."
            )

        existing_ids = airport_ids | target_ids
        forced_start_id = f"C0-{did}"
        suffix = 1
        while forced_start_id in existing_ids:
            forced_start_id = f"C0-{did}-{suffix}"
            suffix += 1

        remaining_targets = [
            t
            for t in all_targets_list
            if str(t.get("id", t.get("label"))) != forced_first_target
        ]
        solver_env = {
            **solve_env,
            "targets": remaining_targets,
            "checkpoints": list(solve_env.get("checkpoints", []) or [])
            + [
                {
                    "id": forced_start_id,
                    "lat": forced_target["lat"],
                    "lng": forced_target["lng"],
                }
            ],
        }
        solver_norm["first"] = None
        forced_first_priority = int(forced_target.get("priority", 0) or 0)
        clear_matrix_cache()

    # ---- 5. SOLVE. Single leg by default; refuel is the exception. ----
    use_constraints = _solve_supports_constraints()
    constraints_kwarg: Dict[str, Any] = {}
    if use_constraints:
        # Solver-side DP-pruning param (dp_plan). Signature agreed as
        # constraints={did: {"first","last","before","adjacent"}}.
        constraints_kwarg = {"constraints": {str(did): solver_norm}}
    else:
        # TODO(solver-constraints): solve_mission_with_allocation does not yet
        # accept `constraints`. Until it lands, we cannot DP-prune ordering here,
        # so we fall back to an UNCONSTRAINED optimal solve (free targets are still
        # optimized globally — this already fixes the free-target-confinement bug).
        # `first`/`last`/`before`/`adjacent` are best-effort NOT enforced in this
        # fallback; remove this branch once the solver param is merged.
        if norm["first"] or norm["last"] or norm["before"] or norm["adjacent"]:
            print(
                "   ⚠️ commit_ordered_plan: solver has no `constraints` param yet — "
                "ordering constraints NOT DP-enforced (free targets still optimized). "
                "TODO(solver-constraints).",
                file=sys.stderr,
            )

    single_config = {
        str(did): {
            **dict(drone_cfg),
            "enabled": True,
            "fuel_budget": fuel_budget,
            "start_airport": forced_start_id or start_airport,
            "end_airport": end_airport,
        }
    }
    if forced_start_id:
        single_config[str(did)]["start_fuel"] = fuel_budget - forced_prefix_fuel
    # Refuel is ALWAYS opportunistic — the solver decides whether/when/where to
    # refuel to complete the job. So KEEP refuel_options/max_refuels (they enable the
    # opportunistic path, which honors the ordering atoms per-leg via constraints).
    # But STRIP the FORCED-leg config (refuel_stops / legacy refuel_airport): those
    # trigger virtual-leg expansion + a prescriptive leg split that RE-ORDERS targets
    # and breaks the ordering constraint. Forced refuel is retired; opportunistic
    # replaces it and keeps frozen atoms intact.
    for _k in ("refuel_stops", "refuel_airport"):
        single_config[str(did)].pop(_k, None)
    # Ensure the opportunistic path is reachable: if the drone lists refuel options
    # but max_refuels is missing/zero, allow a sensible default so a mission that
    # genuinely needs fuel can refuel automatically.
    _sc = single_config[str(did)]
    if _sc.get("refuel_options") and not _sc.get("max_refuels"):
        _sc["max_refuels"] = 4

    def _run_solver(leg_split: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = dict(
            env=solver_env,
            drone_configs=single_config,
            allocation_strategy="exclusive",
            use_sam_aware_distances=True,
            post_optimize=True,  # optimize free targets UNDER the same constraints
        )
        if leg_split is not None:
            kwargs["leg_split"] = leg_split
        kwargs.update(constraints_kwarg)
        return solve_mission_with_allocation(**kwargs)

    def _leg_split_for(anchor_targets_order: List[str]) -> Optional[Dict[str, Any]]:
        """Build a {did:{leg1,leg2}} split for the named refuel position.

        The DP still orders WITHIN each leg; we only decide the target SET per leg
        from the requested slot. `anchor_targets_order` is a reference ordering used
        only to resolve slot/after positions (the solver re-optimizes within legs).
        """
        pos = named_position or "before_landing"
        all_ids = list(anchor_targets_order)
        if pos == "before_landing":
            # Refuel is the LAST stop before end → leg2 has ZERO targets.
            return {str(did): {"leg1": all_ids, "leg2": []}}
        if isinstance(pos, str) and pos.startswith("after:"):
            anchor = pos.split(":", 1)[1].strip()
            if anchor in all_ids:
                i = all_ids.index(anchor)
                return {str(did): {"leg1": all_ids[: i + 1], "leg2": all_ids[i + 1 :]}}
            return {str(did): {"leg1": all_ids, "leg2": []}}
        if isinstance(pos, str) and pos.startswith("slot:"):
            try:
                k = int(pos.split(":", 1)[1])
            except ValueError:
                k = len(all_ids)
            k = max(0, min(k, len(all_ids)))
            return {str(did): {"leg1": all_ids[:k], "leg2": all_ids[k:]}}
        return {str(did): {"leg1": all_ids, "leg2": []}}

    try:
        # First: a SINGLE-LEG solve (the DEFAULT — this is what kills the phantom
        # refuel: with no named refuel and a route that fits one tank, we stop here).
        refuel_used = False
        result = _run_solver(leg_split=None)
        route_data = (result or {}).get("routes", {}).get(str(did), {})

        # A partial visit at tight fuel is NOT a refuel trigger. The orienteering DP
        # legitimately visits the max-points SUBSET the tank allows and drops the
        # rest — that partial is the correct answer, not a bust. Auto-refueling to
        # chase the dropped targets (a) re-splits the route and BREAKS the ordering
        # constraint (the refuel-leg DP put a different target first) and (b) invents
        # a refuel the user never asked for. So: NEVER auto-refuel here. A refuel
        # happens ONLY when the user explicitly named one (user_named_refuel). If the
        # user wants "visit everything, refuel as needed" that is the opportunistic
        # solver's job, requested separately.
        needs_refuel_for_fuel = False

        # Effective refuel airport: the user-named one, else (only on a genuine fuel
        # bust) the drone's configured fallback. A refuel is added iff the user named
        # one OR fuel genuinely busts AND we have a place to land.
        effective_refuel_airport = named_airport or (
            fallback_refuel_airport if needs_refuel_for_fuel else ""
        )
        # A pure fuel bust (no user-named position) must NOT force a fixed leg
        # split: a `before_landing` split parks EVERY target on leg 1, which the
        # tight tank still can't fit → all targets dropped. Instead let the bridge
        # run its exhaustive two-leg split search (leg_split=None) so targets are
        # distributed across BOTH legs to fit the tank. `first` is scoped to leg 1
        # by _subset_constraints_for_leg inside the bridge. Only honor an explicit
        # user-named split position.
        fuel_bust_auto = needs_refuel_for_fuel and not user_named_refuel

        if user_named_refuel or needs_refuel_for_fuel:
            if effective_refuel_airport:
                if fuel_bust_auto:
                    # Genuine bust, no named position → bridge distributes targets
                    # across both legs (exhaustive split), honoring `first` on leg 1.
                    leg_split = None
                else:
                    # User NAMED a refuel position → build the explicit leg split
                    # from the single-leg route order (DP re-optimizes within legs).
                    ref_order = [
                        wp
                        for wp in route_data.get("route", [])
                        if wp in solve_target_ids
                    ]
                    if not ref_order:
                        ref_order = solve_target_ids
                    leg_split = _leg_split_for(ref_order)
                # Inject the refuel airport onto the drone config so the bridge
                # inserts the physical landing between legs.
                single_config[str(did)]["refuel_airport"] = effective_refuel_airport
                result = _run_solver(leg_split=leg_split)
                route_data = (result or {}).get("routes", {}).get(str(did), {})
                refuel_used = True

        route = route_data.get("route", [])
        if not route:
            return f"Error: solver returned no route for D{did}. Result: {result}"

        if forced_start_id:
            suffix_route = list(
                route[1:] if route and route[0] == forced_start_id else route
            )
            merged_route = [start_airport, forced_first_target] + suffix_route
            route_data["route"] = merged_route
            route_data["distance"] = (
                float(route_data.get("distance", route_data.get("fuel_used", 0)) or 0)
                + forced_prefix_fuel
            )
            route_data["fuel_used"] = route_data["distance"]
            route_data["fuel_budget"] = fuel_budget
            route_data.pop("start_fuel", None)
            route_data["points"] = int(route_data.get("points", 0) or 0) + int(
                forced_first_priority
            )
            if route_data.get("leg_distances"):
                leg_distances = [float(x) for x in route_data["leg_distances"]]
                leg_distances[0] += forced_prefix_fuel
                route_data["leg_distances"] = leg_distances
            if route_data.get("refuel_indices"):
                route_data["refuel_indices"] = [
                    int(i) + 1 for i in route_data["refuel_indices"]
                ]
                route_data["refuel_stops"] = [
                    merged_route[i]
                    for i in route_data["refuel_indices"]
                    if 0 <= i < len(merged_route)
                ]
            route_data.pop("trajectory", None)
            route_data.pop("waypoint_indices", None)
            route_data.pop("anim_frame", None)
            route = merged_route

        total_distance = float(
            route_data.get("distance", route_data.get("fuel_used", 0))
        )
        points = route_data.get("points", 0)
        trajectory = route_data.get("trajectory", [])
        visited = [wp for wp in route if wp in target_ids]

        # ---- Feasibility of the ORDERING itself. A constrained target (first / last /
        # an adjacency endpoint) that the solver could NOT place — because honoring it
        # would blow the fuel budget — means the request is INFEASIBLE at this fuel.
        # Do NOT commit a route that silently drops the pinned target and claim success
        # (that produced the "route=['A1']" / "T4 before T2" false-success bugs). Tell
        # the user plainly so they can raise fuel, allow a refuel, or drop the pin.
        visited_set = set(visited)
        required_ids = set()
        if norm.get("first"):
            required_ids.add(norm["first"])
        if norm.get("last"):
            required_ids.add(norm["last"])
        for _pair in norm.get("adjacent", []):
            required_ids.update(str(p) for p in _pair)
        missing_pins = [t for t in required_ids if t not in visited_set]
        if missing_pins:
            reason = (
                "the fuel budget is too small to reach it while honoring the order"
                if not refuel_used
                else "no fuel-feasible leg split can place it in order"
            )
            return (
                f"NOT committed — cannot honor the ordering constraint for D{did}: "
                f"{sorted(missing_pins)} could not be visited in the required position "
                f"({reason}). Options: raise the fuel budget, allow a refuel at a "
                f"reachable airport, or drop the constraint. "
                f"(Best fuel-feasible route without the constraint would visit "
                f"{len(visited)} target(s).)"
            )

        # ---- Ensure a trajectory + waypoint_indices exist for the UI. The bridge
        # normally emits them; if a trajectory is missing (older path), regenerate
        # it for the committed order (no reorder). ----
        if not trajectory or route_data.get("waypoint_indices") is None:
            try:
                from server.solver.solver_bridge import (
                    _project_env_to_local_km,
                    _unproject_solution_trajectories,
                )

                projected_env, ref_lat, ref_lng = _project_env_to_local_km(
                    trajectory_env
                )
                wp_pos: Dict[str, List[float]] = {}
                for a in projected_env.get("airports", []):
                    wp_pos[str(a.get("id", ""))] = [float(a["x"]), float(a["y"])]
                for t in projected_env.get("targets", []):
                    wp_pos[str(t.get("id", ""))] = [float(t["x"]), float(t["y"])]
                from server.solver.altitude import sams_for_drone

                planner = ISRTrajectoryPlanner(
                    sams_for_drone(
                        projected_env, _mission_context.get("drone_configs") or {}, did
                    )
                )
                traj_km, wp_idx = planner.generate_trajectory(
                    route, wp_pos, drone_id=str(did), return_waypoint_indices=True
                )
                route_data["trajectory"] = traj_km
                route_data["waypoint_indices"] = wp_idx
                _unproject_solution_trajectories(
                    {"routes": {str(did): route_data}}, ref_lat, ref_lng
                )
                trajectory = route_data["trajectory"]
            except Exception as _te:
                print(
                    f"   ⚠️ trajectory regen skipped for D{did}: {_te}", file=sys.stderr
                )

        # ---- Detection radius: fuel from the flown path, as for every other solve.
        # Only (a): this tool plans one drone and can't see the others' targets. ----
        from server.solver.detection_radius import apply_detection_radius

        _, _dr_log = apply_detection_radius(
            {"routes": {str(did): route_data}},
            _mission_context.get("environment", {}),
            {str(did): drone_cfg},
            insert_targets=False,
        )
        for _line in _dr_log:
            print(f"🎯 {_line}", file=sys.stderr)
        if _dr_log:
            total_distance = float(route_data.get("distance") or total_distance)

        # ---- Commit exactly as the other solve paths do. ----
        route_data.setdefault("fuel_budget", fuel_budget)
        route_data.setdefault("fuel_used", total_distance)
        route_data.setdefault("distance", total_distance)
        route_data.setdefault("start", start_airport)
        route_data.setdefault("end", route[-1] if route else end_airport)
        route_data.setdefault("targets_visited", visited)
        store_solved_route(str(did), route_data)

        # ---- Human-readable summary: route, per-leg fuel, points, honored rules. ----
        honored: List[str] = []
        if norm["first"]:
            honored.append(f"first={norm['first']}")
        if norm["last"]:
            honored.append(f"last={norm['last']}")
        for a, b in norm["before"]:
            honored.append(f"{a} before {b}")
        for y, x in norm["adjacent"]:
            honored.append(f"{x} right after {y}")
        if refuel_used:
            honored.append(f"refuel at {named_airport}")
        if not use_constraints and (
            norm["first"] or norm["last"] or norm["before"] or norm["adjacent"]
        ):
            honored_note = (
                " [NOTE: solver constraint-pruning not yet available — ordering was "
                "requested but is NOT hard-enforced in this build]"
            )
        else:
            honored_note = ""

        lines = [
            f"COMMITTED plan for D{did}:",
            f"Route: {' -> '.join(route)}",
        ]
        fuel_status = _route_fuel_status(route_data)
        refuel_stops = _route_refuel_stops(route, route_data)
        if refuel_used or fuel_status["legs"] or refuel_stops:
            # Per-leg fuel: prefer the multi-stop `leg_distances` list the bridge
            # emits; fall back to the legacy leg1/leg2 keys.
            leg_ds = fuel_status["legs"] or route_data.get("leg_distances")
            if not leg_ds:
                l1, l2 = route_data.get("leg1_distance"), route_data.get(
                    "leg2_distance"
                )
                leg_ds = [l1, l2] if l1 is not None and l2 is not None else []
            if named_airport:
                lines.append(f"Refuel at: {named_airport}")
            elif refuel_stops:
                lines.append(f"Refuel stops: {refuel_stops}")
            for i, ld in enumerate(leg_ds, start=1):
                if ld is not None:
                    lines.append(f"Leg {i} fuel: {float(ld):.1f} / {fuel_budget}")
        else:
            lines.append(f"Fuel: {total_distance:.1f} / {fuel_budget}")
        lines.append(f"Total fuel: {total_distance:.1f}")
        lines.append(f"Points: {points}")
        lines.append(f"Targets visited: {len(visited)}")
        lines.append(
            f"Constraints honored: {', '.join(honored) if honored else 'none'}"
            + honored_note
        )
        lines.append(
            f"Feasible: {'YES' if fuel_status['feasible'] else 'NO - leg exceeds budget'}"
        )
        return "\n".join(lines)

    except Exception as e:
        import traceback

        return f"Error: {str(e)}\n{traceback.format_exc()}"


@tool
def solve_full_mission(
    strategy: str = "geographic",
    start_with: Optional[List[str]] = None,
    only_targets: Optional[List[str]] = None,
) -> str:
    """Solve the complete mission for all drones using the full pipeline.

    This is the SAME algorithm as 'Run Planner' button. Handles SAM avoidance,
    flexible end airports, optimal sequencing, and trajectory computation.

    Args:
        strategy: Allocation strategy - "geographic" (default), "greedy", "balanced", "efficient", "exclusive"
        start_with: Optional list of target IDs to visit FIRST before other targets.
                    Example: ["T9"] to visit T9 first, then the rest optimally.
        only_targets: Optional list of target IDs to include. ALL other targets are excluded.
                      Use this when the command says "only visit" a specific set of targets.
                      Example: ["T1", "T3", "T5"] to solve with ONLY those targets.

    Returns:
        Complete solution with routes for all drones.
    """
    env = _mission_context.get("environment", {})
    configs = _mission_context.get("drone_configs", {})

    # Filter environment to only_targets if specified
    if only_targets:
        only_set = set(only_targets)
        filtered = [
            t
            for t in env.get("targets", [])
            if str(t.get("id", t.get("label"))) in only_set
        ]
        env = {**env, "targets": filtered}
        print(
            f"🔍 [solve_full_mission] Filtered to {len(filtered)} targets: {only_targets}",
            file=sys.stderr,
        )

    if start_with:
        enabled_drones = [
            str(did)
            for did, cfg in sorted(configs.items())
            if cfg and cfg.get("enabled", True)
        ]
        if len(start_with) == 1 and len(enabled_drones) == 1:
            return commit_ordered_plan.invoke(
                {
                    "drone_id": enabled_drones[0],
                    "constraints": {"first": str(start_with[0])},
                }
            )
        return (
            "Error: start_with is only supported for a single first target on a "
            "single enabled drone. For multi-drone starts, use commit_ordered_plan "
            "with explicit per-drone constraints."
        )

    try:
        from server.api.models import SolveWithAllocationRequest
        from server.api.solve_routes import run_solve_with_allocation

        response = run_solve_with_allocation(
            SolveWithAllocationRequest(
                env=env,
                drone_configs=configs,
                allocation_strategy=strategy,
                use_sam_aware_distances=True,
                post_optimize=True,
                mission_id=_mission_context.get("mission_id"),
                time_sync=_mission_context.get("time_sync"),
                **(_mission_context.get("checkpoint_replan") or {}),
            )
        )
        routes = response.routes or {}
        _remember_wrapped_polygons(response.wrapped_polygons)
        if not response.success:
            errors = response.errors or ["unknown planner error"]
            return "Planner reported an error: " + "; ".join(str(e) for e in errors)

        for drone_id, route_data in routes.items():
            store_solved_route(drone_id, route_data)

        output = [f"Mission solved (strategy={strategy}):"]
        if response.allocations:
            output.append("\nAllocations:")
            for did, tids in sorted(response.allocations.items()):
                if tids:
                    output.append(f"  D{did}: {tids}")

        total_fuel = 0.0
        total_points = 0
        output.append("\nRoutes:")
        for did, route_data in sorted(routes.items()):
            route = route_data.get("route", [])
            fuel = float(route_data.get("distance", 0.0) or 0.0)
            budget = route_data.get("fuel_budget", 0)
            points = int(route_data.get("points", 0) or 0)
            total_fuel += fuel
            total_points += points

            if route:
                route_str = " -> ".join(str(w) for w in route)
                targets_count = len(_route_targets(route))
                fuel_status = _route_fuel_status(route_data)
                feasible = "YES" if fuel_status["feasible"] else "NO"
                output.append(f"  D{did}: {route_str}")
                if fuel_status["legs"]:
                    output.append(
                        f"       Total fuel: {fuel:.1f}; Legs: {fuel_status['detail']}; "
                        f"Points: {points}, Targets: {targets_count}, Feasible: {feasible}"
                    )
                else:
                    output.append(
                        f"       Fuel: {fuel:.1f}/{budget}, Points: {points}, "
                        f"Targets: {targets_count}, Feasible: {feasible}"
                    )

        if response.pipeline_summary:
            s = response.pipeline_summary
            output.append("\nPost-optimization (Run Planner shared pipeline):")
            output.append(
                f"  insertions={s.get('insertions')}, "
                f"swaps={s.get('swaps')} ({s.get('swap_iterations')} iters), "
                f"or_opt={s.get('or_opt_moves')}, "
                f"crossings_fixed={s.get('crossings_fixed')}, "
                f"targets {s.get('targets_before')}→{s.get('targets_after')}, "
                f"fuel {s.get('fuel_before')}→{s.get('fuel_after')}"
            )

        output.append(f"\nTotal: {total_points} points, {total_fuel:.1f} fuel")
        return "\n".join(output)

    except Exception as e:
        import traceback

        return f"Error solving mission: {str(e)}\n{traceback.format_exc()}"


# ============================================================================
# SYSTEM PROMPT - Explains the agent's purpose and capabilities
# ============================================================================

REASONING_AGENT_PROMPT = """You are an ISR mission planner and optimizer. Be decisive: solve ONCE with the best strategy, present the result, and stop. Do NOT sweep every strategy or re-solve a plan you already have.  # noqa: E501

## Your Objective
Find target sequences for each drone that:
1. Maximize total points collected
2. Minimize total fuel used
3. Satisfy the user's command

## The Problem

**Targets**: ID (T1, T2...), priority (points), position (x,y), type (a letter = its target category; list_platforms gives the names).
**Drones**: Fuel budget (tank capacity = max distance per leg), start/end airports, target type restrictions.
**Airports**: Where drones start, refuel, and must return.
**SAM Zones**: No-fly zones drones navigate around.
**Distance = Fuel**: SAM-aware distances from pre-computed matrix.

## Tools

### Information
- get_targets(): All targets with ID, priority, position, type
- get_airports(): All airports with ID, position
- get_drones(): Enabled drones with fuel budgets, airports, target access
- get_sams(): SAM zones with position and range
- get_distance(from, to): SAM-aware distance between two waypoints
- get_current_mission_status(): Current committed agent routes, target counts,
  first targets, refuel stops, fuel, points, and unvisited targets from mission
  session memory. Also includes the previous completed command, whether that
  completed turn changed the route/canvas solution, and route deltas.

### Solving — auto-commit (preferred, single call)
- **solve_full_mission(strategy, start_with, only_targets)**: Solve entire mission in ONE call.
  - strategy: "geographic" (default), "greedy", "balanced", "efficient", "exclusive"
  - start_with: Visit these targets FIRST, then the rest
  - only_targets: ONLY visit these targets, exclude all others
  - Result is automatically committed as the final plan.

- **commit_ordered_plan(drone_id, constraints, refuel)**: THE tool for any request
  with an ORDER ("first"/"last"/"before"/"right after") or a NAMED REFUEL. It honors
  the constraints exactly and optimizes every free target around them, then commits a
  real SAM-aware route. See "Honoring ordering / refuel constraints" below for how to
  build the `constraints` dict. This is the ONLY tool that guarantees the committed
  route matches the stated order — do NOT fall back to any other tool for a refuel or
  an ordering constraint.

### Route edits — change a drone's route exactly as ordered (auto-commit)
These are the operator's sequence-bar operation. They change ONLY the drone's
remaining route from where it is now; the environment is untouched (targets left
out stay unvisited in the mission). The drone's current position is added for you.
- **set_route(drone_id, waypoints)**: replace the remaining route with exactly
  `waypoints` (targets/airports after the current position, ending at the landing
  airport). E.g. set_route("1", ["T8","A2"]).
- **insert_in_route(drone_id, target_id, after=None)**: add one target, keep the rest.
- **remove_from_route(drone_id, target_id)**: drop one target, keep the rest.

### Drone settings (for the next replan)
- **set_drone(drone_id, enabled=None, end_airport=None)**: e.g. a drone is down, or
  must land somewhere else. Then replan with solve_full_mission.

## Route edit or replan?
- The order DICTATES the route ("go directly to A1", "visit T8 then land at A2",
  "add T9 to D1", "D2 skip T4") → a ROUTE EDIT. Do not replan.
  "Abort, all drones go to A1" → set_route(d, ["A1"]) for EVERY enabled drone.
- The order asks you to CHOOSE ("solve", "continue", "re-optimize", "T9 first",
  "D3 is down, continue") → a REPLAN: solve_full_mission, or commit_ordered_plan for
  ordering/refuel rules, after any set_drone changes.

## Workflow

**Default (simple, one request):**
1. Call get_targets() and get_drones() to understand the mission.
2. Call solve_full_mission() — it auto-commits.
3. Present the solution.

## Language Discipline: Avoid False Proof Claims
Do NOT call a mission result "optimal", "globally optimal", "best possible", or
say "no further improvements possible" unless a tool explicitly returned a proof
or certificate of global optimality. The planner pipeline is strong, but most
mission solves are heuristic across allocation, refuel placement, and
post-optimization. Use careful wording:
- "best route found by the current planner pipeline"
- "the current planner pipeline did not find further improvements"
- "feasible route found"
When the user asks "is this optimal?" or "is there a better solution?", answer
with this distinction: "I cannot certify global optimality from the available
tools. This is the best route found by the current planner pipeline, and that
pipeline did not find further improvements." Then explain any known caveat
briefly.
For exact subproblems such as Held-Karp inside one bounded leg, you may say that
specific leg/order was solved exactly, but do not generalize that to the whole
mission unless the whole mission was actually certified.

## CRITICAL: OBEY THE USER'S CONSTRAINTS — THEY ARE HARD REQUIREMENTS
When the user gives an explicit instruction, it is a HARD CONSTRAINT, not a
suggestion. You MUST satisfy it, even when it makes the route longer or is not
needed for feasibility. NEVER decide a user constraint is "unnecessary" and drop
it. If you cannot satisfy it, say so explicitly and explain why — do not silently
optimize it away.

**THE COMMITTED ROUTE IS THE ONLY THING THAT MATTERS. Describing a route in text
does NOT commit it — the UI shows only what a solve/commit TOOL returned.** So:
- After you decide the correct route, you MUST call the tool that COMMITS it
  (a route edit when the order dictates the route, commit_ordered_plan for an
  order/refuel constraint, solve_full_mission for a plain solve).
  A plan you only wrote in prose is NOT applied and the user sees the OLD route.
- If the currently-committed route VIOLATES any user constraint (wrong order,
  missing refuel, wrong targets), you MUST re-solve and commit the correct one.
  This is REQUIRED, not "excessive solving" — the "be decisive / don't re-solve"
  guidance applies ONLY when the committed route already satisfies every
  constraint. A constraint violation always overrides it.
- Before you finish, self-check: does the COMMITTED route (the one the tool just
  returned) satisfy every clause of the request? If not, you are not done — commit
  the correct route.

### HOW to honor ordering / refuel constraints: TRANSLATE the words into a constraints dict
You are an LLM — you already understand "first", "last", "before X", "right after Y",
"then", "refuel before landing". Do NOT look for a recipe per phrase and do NOT build
a sequence or pin targets by hand. Your ONLY job is to translate the user's words into
a small generic `constraints` dict, then call ONE tool. The solver optimizes
everything else (all free targets) optimally under those constraints.

1. Build the `constraints` dict, including ONLY the rules the user actually stated:
   - "visit T7 first" / "start at T7"      → {"first":"T7"}
   - "T3 last" / "end at T3 before return"  → {"last":"T3"}
   - "A before B" (not necessarily next)    → {"before":[["A","B"]]}
   - "X right after Y" (immediately)        → {"adjacent":[["Y","X"]]}
   Combine as needed, e.g. {"first":"T7","before":[["T1","T2"]]}. Omit any field the
   user did not constrain — those targets are FREE and the solver places them
   optimally. If the user stated NO ordering at all, pass constraints=None (or {}).

2. Decide refuel — and DEFAULT TO NONE. Add a `refuel` arg ONLY when the user
   explicitly NAMES a refuel airport (or the drone is configured with one):
   - "refuel at TK1 before landing" → refuel={"airport":"TK1","position":"before_landing"}
   - "refuel at TK1 after T4"        → refuel={"airport":"TK1","position":"after:T4"}
   Do NOT add a refuel just because a route looks long — the tool itself checks the
   fuel budget and will add a refuel automatically ONLY if the single-tank optimal
   route genuinely exceeds the budget. Never invent a refuel the user didn't request.

3. Call **commit_ordered_plan(drone_id, constraints=<dict or None>, refuel=<dict or None>)**.
   It solves the WHOLE mission for that drone with Held-Karp under your constraints,
   optimizes all free targets, and COMMITS a real SAM-aware route (with refuel
   metadata only if a refuel exists).

Use **commit_ordered_plan** whenever the user fixes an order OR names a refuel — it is
also fine for a plain "solve the mission" (pass constraints=None). Use
solve_full_mission for simple multi-drone unconstrained requests. Either way, the
COMMITTED route (what the tool returned) must satisfy every clause — verify before
presenting.

## CRITICAL: "optimize this solution" / "can you do better"
solve_full_mission runs the current optimizer pipeline (Insert → Swap → Or-Opt →
No-Cross) to its configured stopping point. So when the user asks you to optimize
or improve an EXISTING solution from earlier in this conversation:
- Do NOT re-solve from scratch. The solution you already produced is in the
  conversation above.
- A plain re-solve is expected to give the same result unless inputs changed.
- Say that the current planner pipeline already ran and did not find further
  improvements. Do NOT call solve tools again just to confirm.

## CRITICAL: Follow-up questions about the current mission
For questions like "how many targets did you visit?", "what did D1 visit?", "why
didn't you visit T2 first?", "which targets are missing?", "what route did you
just create?", "what changed?", "what command did I give before this?", or "did
the last answer change the route?", call get_current_mission_status() before
answering. Never say you cannot track visited targets; the current committed
agent routes and last completed turn facts are available in mission session
memory. If there is no committed route, say that explicitly and answer that zero
targets are currently committed for the agent result.

## Key Rules
- **Drones MUST land before running out of fuel.** Fuel budget is a HARD constraint.
- **Points matter more than fuel.** Prefer the highest-point feasible plan; only use fuel as a tiebreaker.
- Never recommend a 0-point "do nothing" plan unless literally no target is reachable.
- The solver already optimizes refueling leg splits — trust its result.
- **Solve ONCE.** Call solve_full_mission a SINGLE time per request. Do NOT sweep
  strategies and do NOT re-commit a plan you already have — you have the answer
  after one call. Present it and stop.
- Report routes EXACTLY as the tools returned them. The reply automatically ends
  with the committed plan generated from the routes themselves; never describe a
  route that differs from it.

## Continuing a mission from a cut (agent-driven segments)
When the turn is marked [CONTINUATION], the operator paused mid-flight and wants the
mission to continue. The environment you were given is ALREADY CUT: each drone's start is
its current position, the target list is only the remaining (unvisited) targets, and the
fuel already burned is carried forward. So you simply SOLVE this environment normally:
- Plain "continue" / "keep going" / "solve from here" → solve_full_mission.
- An ORDERING order → commit_ordered_plan with the constraint. Map the sentence to args:
  "visit T3 then T7 then T9" → before=[["T3","T7"],["T7","T9"]]; "T9 first" → first="T9";
  "T9 last" → last="T9"; "keep T4 and T5 together" → adjacent=[["T4","T5"]].
- A route order → a ROUTE EDIT from the current position: "D1 go directly to A5" →
  set_route("1", ["A5"]); "abort, all land at A5" → set_route(d, ["A5"]) for EVERY
  enabled drone; "D1 add T9" → insert_in_route("1", "T9").
- "D3 is down" → set_drone("3", enabled=False), then solve_full_mission.
Because the start is the cut, ANY solve continues from there — you cannot lose the cut.
Present the continued route(s). Respect-or-report: if an order is infeasible, relay the
best the solver returned and what it had to drop.
"""


# ============================================================================
# AGENT EXECUTION
# ============================================================================


def create_reasoning_agent(llm_config: Optional[Dict[str, Any]] = None):
    """Create the reasoning agent with tools.

    Request settings select the provider/model, while credentials always come
    from the server. Without settings, use LLM_PROVIDER / LLM_MODEL, falling
    back to OpenAI. Never mutate environment variables for a request.
    """
    import os

    from server.agents.llm_factory import get_llm

    config = llm_config or {}
    provider = config.get("provider") or os.getenv("LLM_PROVIDER", "openai")
    if not isinstance(provider, str):
        raise ValueError("LLM provider must be a string")
    # An explicit provider must not inherit another provider's server model.
    model = config.get("model")
    if not model and not config.get("provider"):
        model = os.getenv("LLM_MODEL")
    if not model:
        defaults = {
            "openai": "gpt-4o",
            "zai": "glm-4.6",
            "google": "gemini-1.5-pro",
            "groq": "llama-3.3-70b-versatile",
        }
        model = defaults.get(provider, "gpt-4o")

    # Hosted-provider keys and endpoints are never supplied by the client.
    # Ollama already supports an operator-selected host in AI Settings.
    host_options = {"base_url": config.get("base_url")} if provider == "ollama" else {}
    llm = get_llm(
        provider=provider,
        model=model,
        temperature=0,
        max_tokens=8192,
        **host_options,
    )

    # Bind tools to the LLM
    tools = [
        get_targets,
        get_airports,
        get_drones,
        get_sams,  # Information
        get_current_mission_status,
        solve_full_mission,  # Replan — same as Run Planner (auto-commits)
        commit_ordered_plan,  # Replan one drone under ordering/refuel constraints
        set_route,  # Route edit: replace a drone's remaining route (sequence bar)
        insert_in_route,  # Route edit: add one target
        remove_from_route,  # Route edit: drop one target
        set_drone,  # Drone settings (enabled / end airport / altitude / platform) for the next replan
        list_platforms,  # Aircraft types + sensors a drone can be (catalog)
        get_distance,
    ]
    llm_with_tools = llm.bind_tools(tools)

    return llm_with_tools, tools


# ---------------------------------------------------------------------------
# Mission inputs the last plan was made from. A continuing conversation must not
# report its remembered plan after the operator changed the mission (e.g. end
# airports) — the old plan is out of date and the model can't see that itself.
# ---------------------------------------------------------------------------

_DRONE_PLAN_FIELDS = (
    "enabled",
    "start_airport",
    "end_airport",
    "fuel_budget",
    "refuel_airport",
    "refuel_options",
    "max_refuels",
    "target_access",
)


def _mission_inputs(
    environment: Dict[str, Any], drone_configs: Dict[str, Any]
) -> Dict[str, Any]:
    """The parts of the mission a plan depends on, in a comparable form."""
    env = environment or {}

    def _pos(obj: Dict[str, Any]) -> List[Any]:
        return [
            round(float(obj.get("lat", obj.get("x", 0)) or 0), 5),
            round(float(obj.get("lng", obj.get("y", 0)) or 0), 5),
        ]

    drones = {}
    for did, cfg in (drone_configs or {}).items():
        cfg = cfg or {}
        entry = {k: cfg.get(k) for k in _DRONE_PLAN_FIELDS}
        entry["enabled"] = cfg.get("enabled") is not False
        entry["refuel_options"] = sorted(
            str(x) for x in (cfg.get("refuel_options") or [])
        )
        drones[str(did)] = entry
    return {
        "drones": drones,
        "targets": {
            str(t.get("id", t.get("label"))): _pos(t)
            + [t.get("priority"), t.get("type")]
            for t in env.get("targets", []) or []
        },
        "sams": sorted(
            _pos(sm.get("center") or sm) + [sm.get("range_km", sm.get("range"))]
            for sm in env.get("sams", []) or []
        ),
        "airports": sorted(str(a.get("id")) for a in env.get("airports", []) or []),
        "cruise_altitude_km": env.get("cruise_altitude_km"),
    }


def _describe_input_changes(
    old: Dict[str, Any], new: Dict[str, Any], limit: int = 12
) -> List[str]:
    """Human-readable differences between two _mission_inputs results."""
    changes: List[str] = []
    shown = {"end_airport": "end airport", "start_airport": "start airport"}

    def _fmt(v: Any) -> str:
        return "ANY" if v in (None, "", "-") else str(v)

    for did in sorted(set(old.get("drones", {})) | set(new.get("drones", {}))):
        o = old.get("drones", {}).get(did)
        n = new.get("drones", {}).get(did)
        if o is None or n is None:
            changes.append(f"D{did} {'added' if o is None else 'removed'}")
            continue
        for field in _DRONE_PLAN_FIELDS:
            if o.get(field) != n.get(field):
                if field == "enabled":
                    changes.append(f"D{did} {'enabled' if n[field] else 'disabled'}")
                else:
                    label = shown.get(field, field.replace("_", " "))
                    changes.append(
                        f"D{did} {label} {_fmt(o.get(field))} → {_fmt(n.get(field))}"
                    )
    ot, nt = old.get("targets", {}), new.get("targets", {})
    added = sorted(set(nt) - set(ot))
    removed = sorted(set(ot) - set(nt))
    moved = sorted(t for t in set(ot) & set(nt) if ot[t] != nt[t])
    if added:
        changes.append(f"targets added: {', '.join(added)}")
    if removed:
        changes.append(f"targets removed: {', '.join(removed)}")
    if moved:
        changes.append(f"targets moved or re-prioritized: {', '.join(moved)}")
    if old.get("sams") != new.get("sams"):
        changes.append("SAM zones changed")
    if old.get("airports") != new.get("airports"):
        changes.append("airports changed")
    if old.get("cruise_altitude_km") != new.get("cruise_altitude_km"):
        changes.append(
            f"cruise altitude {_fmt(old.get('cruise_altitude_km'))} → "
            f"{_fmt(new.get('cruise_altitude_km'))} km"
        )
    if len(changes) > limit:
        changes = changes[:limit] + [f"… and {len(changes) - limit} more"]
    return changes


def _plan_not_on_canvas(routes: Dict[str, Dict[str, Any]]) -> bool:
    """True if the agent's plan differs from what the operator's canvas shows
    (sent as current_routes) — e.g. the operator discarded it or Run Planner
    replaced it. Only drones the plan actually routes are compared."""
    on_canvas = _mission_context.get("current_routes")
    if on_canvas is None:
        return False  # canvas unknown (caller didn't send it) — don't guess
    for did, rd in (routes or {}).items():
        route = [str(w) for w in _route_list(rd)]
        if route and route != [str(w) for w in on_canvas.get(str(did), [])]:
            return True
    return False


def _routes_changed_this_turn(
    routes: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Only the drones a tool changed this turn. The UI keeps every other drone's
    plan as the operator has it (routes carried from earlier turns may be stale)."""
    changed = _mission_context.get("routes_changed_this_turn", set())
    return {did: rd for did, rd in (routes or {}).items() if str(did) in changed}


def _committed_plan_text(routes: Dict[str, Dict[str, Any]]) -> str:
    """The committed routes, generated from the routes themselves (not from the
    model's prose), so the reply can never contradict what the map shows."""
    if not routes:
        return ""
    lines = ["", "", "**Committed plan (what the map shows):**"]
    for did, rd in sorted(routes.items(), key=lambda kv: str(kv[0])):
        route = [re.sub(r"^(C\d+)-\d+$", r"\1", str(w)) for w in _route_list(rd)]
        fuel = float(rd.get("distance", rd.get("fuel_used", 0.0)) or 0.0)
        lines.append(
            f"- D{did}: {' → '.join(route)} ({fuel:.1f} fuel, {rd.get('points', 0)} pts)"
        )
    unchanged = sorted(
        set(
            str(d)
            for d, c in (_mission_context.get("drone_configs") or {}).items()
            if (c or {}).get("enabled") is not False
        )
        - set(str(d) for d in routes)
    )
    if unchanged:
        lines.append(f"- Unchanged: {', '.join('D' + d for d in unchanged)}")
    return "\n".join(lines)


def _turn_extras() -> Dict[str, Any]:
    extras: Dict[str, Any] = {
        "wrapped_polygons": _mission_context.get("wrapped_polygons")
    }
    if _mission_context.get("changed_drone_configs"):
        extras["drone_configs"] = _mission_context.get("drone_configs")
    return extras


def run_reasoning_agent(
    user_request: str,
    environment: Dict[str, Any],
    drone_configs: Dict[str, Any],
    distance_matrix: Optional[Dict] = None,
    time_sync: Optional[Dict[str, Any]] = None,
    max_iterations: int = 50,  # Increased for complex phased missions
    mission_id: Optional[str] = None,
    is_continue_from_cut: bool = False,
    visited_targets: Optional[List[str]] = None,
    repair_routes: Optional[Dict[str, Any]] = None,
    current_routes: Optional[Dict[str, List[str]]] = None,
    llm_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run the reasoning agent on a user request.

    Args:
        user_request: The user's command in natural language
        environment: Mission environment (targets, airports, SAMs)
        drone_configs: Drone configurations
        distance_matrix: Optional pre-computed distance matrix
        max_iterations: Maximum tool-calling iterations
        mission_id: When given, the agent CONTINUES that mission's thread —
            prior turns + the last solution are loaded so followup questions
            ("why did D2 skip T14?") work. The thread persists across edits/cuts
            until explicit End Mission (agent_session.end_session). Omit / None
            for a one-off stateless turn (legacy behavior).

    Returns:
        Dict with 'response' (final answer) and 'reasoning' (step-by-step thoughts)
    """
    from server.agents.agent_session import get_or_create_session, save_session

    # Set up mission context for tools (env/cfg can change across turns — a cut
    # or edit — so always refresh it from THIS turn's request).
    checkpoint_replan = (
        {
            "is_checkpoint_replan": True,
            "visited_targets": list(visited_targets or []),
            "repair_routes": repair_routes,
        }
        if is_continue_from_cut
        else None
    )
    set_mission_context(
        environment,
        drone_configs,
        distance_matrix,
        mission_id,
        time_sync,
        checkpoint_replan,
        current_routes,
    )

    # Store the mission command in memory (persists across stateless calls)
    _store_mission_command(user_request)

    # Create agent
    llm_with_tools, tools = (
        create_reasoning_agent(llm_config) if llm_config else create_reasoning_agent()
    )
    tool_map = {t.name: t for t in tools}

    # --- Session: continue the mission thread, or start fresh -----------------
    session = get_or_create_session(mission_id)
    previous_routes_for_turn: Dict[str, Dict[str, Any]] = {}
    # What this turn's mission looks like, and what the last PLAN was made from.
    current_inputs = _mission_inputs(environment, drone_configs)
    planned_inputs_before = (
        (session.last_turn or {}).get("planned_inputs")
        if session is not None and not session.is_fresh
        else None
    )
    target_priority_for_turn = {
        str(t.get("id", t.get("label"))): int(t.get("priority", 0) or 0)
        for t in (environment or {}).get("targets", [])
        if t.get("id", t.get("label")) is not None
    }
    if session is not None and not session.is_fresh:
        # CONTINUE: reload the running thread + last solution. The new user
        # message is appended to the existing conversation, so the agent sees
        # every prior turn (initial solve, edits, cuts, followups).
        messages = list(session.messages)
        messages.append(HumanMessage(content=user_request))

        # RECONCILE carried routes against the CURRENT config. The env/configs
        # can change between turns (a drone disabled, removed). A carried route
        # for a drone that is now disabled / gone must be DROPPED — else it
        # lingers in the solution and that drone keeps flying. (This was the
        # "disabled drones still fly" bug: session carried the route, nothing
        # re-checked it against the live config.)
        _enabled_ids = {
            str(d)
            for d, c in (drone_configs or {}).items()
            if (c or {}).get("enabled") is not False
        }
        carried = {
            did: rd
            for did, rd in (session.solved_routes or {}).items()
            if str(did) in _enabled_ids
        }
        previous_routes_for_turn = dict(carried)
        _mission_context["previous_turn"] = dict(session.last_turn or {})
        _dropped = set(session.solved_routes or {}) - set(carried)
        if is_continue_from_cut:
            # A mid-mission turn: keep only routes that start where each drone starts
            # NOW. A route from an older origin (airport / earlier cut) is stale and
            # must not be returned for a drone the agent didn't re-solve; a route from
            # the current cut is the agent's own plan and stays, so it can answer
            # questions about it.
            carried = _routes_from_current_starts(carried, drone_configs)
        elif planned_inputs_before and planned_inputs_before != current_inputs:
            # The operator changed the mission since the last plan: that plan is
            # out of date. Drop it and tell the model what changed, so "solve"
            # plans again instead of reporting the old routes.
            changes = _describe_input_changes(planned_inputs_before, current_inputs)
            carried = {}
            messages[-1] = HumanMessage(
                content=(
                    "[MISSION CHANGED SINCE YOUR LAST PLAN] "
                    + "; ".join(changes)
                    + ". Your previous plan is out of date — do not report it. Use the "
                    "tools to plan with the current settings.\n\nOperator request: "
                    + str(user_request)
                )
            )
            print(
                f"🔄 Mission changed since last plan: {'; '.join(changes)}",
                file=sys.stderr,
            )
        set_solved_routes(carried)
        print(
            f"🧵 Continuing mission {mission_id}: turn {session.turns + 1}, "
            f"{len(session.messages)} prior messages, "
            f"{len(carried)} carried routes"
            + (f", dropped {sorted(_dropped)} (disabled/removed)" if _dropped else ""),
            file=sys.stderr,
        )
    else:
        # FRESH: build the system prompt (with agent memory) + first message.
        clear_solved_routes()
        _mission_context["previous_turn"] = {}
        memory_context = get_memory_for_prompt()
        system_prompt = REASONING_AGENT_PROMPT
        if memory_context:
            system_prompt += "\n" + memory_context
            print(
                f"📝 Agent memory loaded into context ({len(memory_context)} chars)",
                file=sys.stderr,
            )
        similar_context = _similar_missions_context(
            user_request, environment, drone_configs
        )
        if similar_context:
            system_prompt += "\n\n" + similar_context
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_request),
        ]

    # AGENT-DRIVEN CONTINUE: the env we were given is already CUT — each drone's start is a
    # D{did}_START node at its current position, targets are filtered to the unvisited set,
    # and start_fuel carries the burn forward. So the agent just SOLVES this env normally
    # (solve_full_mission / commit_ordered_plan) and it automatically continues from the
    # cut — it cannot re-solve from an airport, because there is no airport start in the
    # env. This directive only frames the turn as a continuation and maps the operator's
    # order to solver constraints. Folded into the last user message so the
    # prompt shape stays compatible with providers that require one leading
    # system message.
    if is_continue_from_cut and messages and isinstance(messages[-1], HumanMessage):
        _cont = (
            "[CONTINUATION] The mission is mid-flight and already cut: each drone starts "
            "at its current position and the target list is the remaining (unvisited) "
            "targets. Solve this environment to continue the mission from here — call "
            "solve_full_mission (or commit_ordered_plan if the operator gave an ordering "
            "constraint). Translate any order into constraints: 'visit T3 then T7 then T9' "
            "-> before=[[T3,T7],[T7,T9]]; 'T9 first' -> first=T9; 'T9 last' -> last=T9; "
            "'keep T4 and T5 together' -> adjacent=[[T4,T5]]; 'D1 to A5' -> D1 end_airport "
            "A5; 'abort, all land at A5' -> every drone ends at A5 with no targets. Then "
            "present the continued route(s).\n\nOperator request: "
        )
        messages[-1] = HumanMessage(content=_cont + str(messages[-1].content))

    reasoning_steps = []
    # Phase-1b learning capture: remember the ordering/refuel constraints the agent
    # actually committed with (the args it passed to commit_ordered_plan). Threaded
    # into the return so the mission ledger records real constraints, not {}.
    committed_constraints: Dict[str, Any] = {}
    committed_refuel: Any = None

    # No-progress guard: the agent's "never satisfied" prompt makes it churn —
    # sweeping every allocation strategy, re-running solve_full_mission, retrying
    # leg-splits — long after it has a feasible plan. Each solve is ~3.4s, so the
    # UI looks hung for minutes. Count TOTAL solve-type tool calls (any of them,
    # committing or not); once a feasible solution exists and the agent keeps
    # solving past a budget, nudge it to commit-or-finish.
    SOLVE_TOOLS = {
        "solve_full_mission",
        "commit_ordered_plan",
        "set_route",
        "insert_in_route",
        "remove_from_route",
    }
    SOLVE_BUDGET = 6  # solve calls allowed before the nudge (a strategy sweep = 5)
    solve_calls = 0
    nudged = False

    def _save_turn(
        messages_to_save: List[Any],
        routes_to_save: Dict[str, Dict[str, Any]],
        response_text: str,
        routes_changed_for_ui: bool,
    ) -> None:
        turn_memory = _build_turn_memory(
            user_request=user_request,
            response=response_text,
            previous_routes=previous_routes_for_turn,
            current_routes=routes_to_save,
            routes_changed_for_ui=routes_changed_for_ui,
            target_priority=target_priority_for_turn,
        )
        # The inputs the latest PLAN was made from: this turn's if it planned,
        # else unchanged (a question after an edit must not hide the edit).
        turn_memory["planned_inputs"] = (
            current_inputs if routes_changed_for_ui else planned_inputs_before
        )
        save_session(mission_id, messages_to_save, routes_to_save, turn_memory)

    print(f"\n{'='*60}", file=sys.stderr)
    print("🧠 REASONING AGENT", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"User request: {user_request}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    # Agent loop
    for iteration in range(max_iterations):
        print(f"\n--- Iteration {iteration + 1} ---", file=sys.stderr)

        # Get LLM response
        response = llm_with_tools.invoke(messages)
        messages.append(response)

        # Check if there are tool calls
        if response.tool_calls:
            iter_tool_names = [tc["name"] for tc in response.tool_calls]
            print(
                f"🔧 Tool calls: {iter_tool_names}",
                file=sys.stderr,
            )

            # Count solve-type calls this iteration (toward the no-progress budget).
            solve_calls += sum(1 for n in iter_tool_names if n in SOLVE_TOOLS)

            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]

                print(f"   Calling {tool_name}({tool_args})", file=sys.stderr)
                reasoning_steps.append(f"Called {tool_name} with {tool_args}")

                # Capture the constraints/refuel the agent commits with (for learning).
                # The LAST committing call wins (it's what ends up on the canvas).
                if tool_name == "commit_ordered_plan":
                    _c = tool_args.get("constraints")
                    if _c:
                        committed_constraints = _c
                    _r = tool_args.get("refuel")
                    if _r:
                        committed_refuel = _r

                # Execute tool
                if tool_name in tool_map:
                    try:
                        result = tool_map[tool_name].invoke(tool_args)
                        print(
                            (
                                f"   Result: {result[:200]}..."
                                if len(str(result)) > 200
                                else f"   Result: {result}"
                            ),
                            file=sys.stderr,
                        )
                    except Exception as e:
                        result = f"Error: {str(e)}"
                        print(f"   Error: {e}", file=sys.stderr)
                else:
                    result = f"Unknown tool: {tool_name}"

                # Add tool result to messages
                messages.append(
                    ToolMessage(content=str(result), tool_call_id=tool_call["id"])
                )
                reasoning_steps.append(f"Result: {result}")

            # No-progress nudge: once the agent has run more than SOLVE_BUDGET
            # solve calls (a full strategy sweep is 5) AND has a feasible solution,
            # tell it to stop re-solving and commit-or-finish. Catches the
            # strategy-sweep / leg-split-retry churn — each solve is ~3.4s, so
            # this is what kept the UI hung for minutes. Fires ONCE.
            if solve_calls > SOLVE_BUDGET and not nudged and get_solved_routes():
                nudged = True
                _solve_calls_at_nudge = solve_calls
                nudge = (
                    f"You have run {solve_calls} solves and already have a feasible "
                    "solution. STOP re-solving and trying more strategies. Give "
                    "your final answer now. Do not call any more solve tools."
                )
                messages.append(HumanMessage(content=nudge))
                reasoning_steps.append(f"[guard] {nudge}")
                print(
                    f"⚠️  No-progress nudge at iteration {iteration + 1} ({solve_calls} solves)",
                    file=sys.stderr,
                )

            # HARD STOP: if the agent IGNORED the nudge and kept solving (the
            # solve churn that hung the demo for minutes),
            # force-finish now — return the best solution we already have, with a
            # short summary, instead of letting it re-solve to the iteration cap.
            elif (
                nudged
                and solve_calls > _solve_calls_at_nudge + 1
                and get_solved_routes()
            ):
                routes = get_solved_routes()
                print(
                    f"🛑 Hard stop: agent kept solving after the nudge "
                    f"({solve_calls} solves). Returning best solution.",
                    file=sys.stderr,
                )
                total_pts = sum(rd.get("points", 0) for rd in routes.values())
                summary_lines = [
                    f"Solution ready ({len(routes)} drone(s), {total_pts} total points)."
                ]
                for did, rd in sorted(routes.items()):
                    r = rd.get("route", [])
                    summary_lines.append(
                        f"- D{did}: {' → '.join(str(w) for w in r)} "
                        f"({rd.get('points', 0)} pts)"
                    )
                response_text = "\n".join(summary_lines)
                _save_turn(
                    messages,
                    routes,
                    response_text,
                    routes_changed_for_ui=solve_calls > 0,
                )
                routes = _routes_changed_this_turn(routes)
                response_text += _committed_plan_text(routes)
                return {
                    "response": response_text,
                    "reasoning": reasoning_steps,
                    "iterations": iteration + 1,
                    "routes": routes,
                    "routes_changed": solve_calls > 0,
                    "constraints": committed_constraints,
                    "refuel": committed_refuel,
                    **_turn_extras(),
                    "success": True,
                }
        else:
            # No tool calls - agent is done reasoning
            final_response = _soften_certainty_language(response.content)
            print("\n✅ Agent finished reasoning", file=sys.stderr)
            (
                print(f"Final response:\n{final_response[:500]}...", file=sys.stderr)
                if len(final_response) > 500
                else print(f"Final response:\n{final_response}", file=sys.stderr)
            )

            # Get all routes that were solved during reasoning
            routes = get_solved_routes()

            # The agent kept its remembered plan (no tool call) but that plan is
            # not what the canvas shows (discarded / replaced): send it back as a
            # solution so what the reply describes is what the map draws.
            redisplay = (
                solve_calls == 0 and bool(routes) and _plan_not_on_canvas(routes)
            )

            # Persist the thread + solution so the next turn can continue it.
            _save_turn(
                messages,
                routes,
                final_response,
                routes_changed_for_ui=solve_calls > 0 or redisplay,
            )
            if solve_calls > 0:
                routes = _routes_changed_this_turn(routes)
                final_response += _committed_plan_text(routes)
            elif redisplay:
                final_response += _committed_plan_text(routes).replace(
                    "(what the map shows)",
                    "(re-displayed on the map — it was not on the canvas)",
                )
                print(
                    "🗺️ Re-displaying the remembered plan (not on the canvas)",
                    file=sys.stderr,
                )
            print(f"📦 Returning {len(routes)} solved routes to UI", file=sys.stderr)

            return {
                "response": final_response,
                "reasoning": reasoning_steps,
                "iterations": iteration + 1,
                "routes": routes,  # Include routes for UI rendering
                # Did THIS turn change the solution? A pure followup question
                # (no solve/commit tool) returns the unchanged routes for context
                # but the UI must not treat it as a new solution to apply —
                # unless the plan it refers to is not on the canvas (redisplay).
                "routes_changed": solve_calls > 0 or redisplay,
                "constraints": committed_constraints,
                "refuel": committed_refuel,
                **_turn_extras(),
                "success": True,
            }

    # Max iterations reached — still persist the partial thread so a followup
    # ("keep going") continues from where it stalled.
    routes = get_solved_routes()
    response_text = "Maximum iterations reached. Partial reasoning available."
    _save_turn(
        messages,
        routes,
        response_text,
        routes_changed_for_ui=solve_calls > 0,
    )
    if solve_calls > 0:
        routes = _routes_changed_this_turn(routes)
        response_text += _committed_plan_text(routes)
    return {
        "response": response_text,
        "reasoning": reasoning_steps,
        "iterations": max_iterations,
        "routes": routes,  # Include any routes solved so far
        "routes_changed": solve_calls > 0,
        "constraints": committed_constraints,
        "refuel": committed_refuel,
        **_turn_extras(),
        "success": False,
    }


# ============================================================================
# API ENDPOINT HELPER
# ============================================================================


def _env_shape_features(environment: Dict[str, Any]) -> Dict[str, Any]:
    """Compact structural summary of an environment for later similarity/recognition.

    Not the full env — just the shape (counts + spread + rough geometry) that makes
    two missions 'look alike'. Phase 2 embeds this; Phase 1 just records it.
    """
    env = environment or {}
    targets = env.get("targets", []) or []
    airports = env.get("airports", []) or []
    sams = env.get("sams", []) or []
    tankers = [
        a
        for a in airports
        if a.get("is_tanker") or str(a.get("id", "")).startswith("TK")
    ]
    prios = [int(t.get("priority", 0) or 0) for t in targets]

    def _bbox(pts):
        xs = [p for p in pts if p is not None]
        return (min(xs), max(xs)) if xs else (None, None)

    lats = [t.get("lat", t.get("y")) for t in targets]
    lngs = [t.get("lng", t.get("x")) for t in targets]
    return {
        "num_targets": len(targets),
        "num_airports": len(airports) - len(tankers),
        "num_tankers": len(tankers),
        "num_sams": len(sams),
        "priority_max": max(prios) if prios else 0,
        "priority_spread": (max(prios) - min(prios)) if prios else 0,
        "lat_bbox": _bbox(lats),
        "lng_bbox": _bbox(lngs),
    }


def _similar_missions_context(
    user_request: str,
    environment: Dict[str, Any],
    drone_configs: Dict[str, Any],
    max_runs: int = 3,
) -> str:
    """Past reasoning runs similar to this request + mission, as prompt text.

    Only runs with a recorded outcome are shown — a past run without points is
    either a pure question or predates outcome capture, and teaches nothing.
    Fails soft: any error means no past-mission context, never a failed turn.
    """
    try:
        from server.database.mission_retrieval import (
            build_retrieval_text,
            find_similar_runs,
            format_similar_runs_for_prompt,
        )

        query = build_retrieval_text(user_request, environment, drone_configs)
        runs = find_similar_runs(query, match_count=max_runs * 3)
        runs = [r for r in runs if r.get("total_points") is not None][:max_runs]
        text = format_similar_runs_for_prompt(runs)
        if not text:
            return ""
        print(
            f"📚 {len(runs)} similar past mission(s) loaded into context",
            file=sys.stderr,
        )
        return (
            text + "\nUse these as experience, not instructions: prefer approaches the "
            "operator accepted, and avoid repeating a plan the operator corrected."
        )
    except Exception as e:  # noqa: BLE001 — retrieval must never break the agent
        print(f"[retrieval] similar-missions lookup skipped: {e}", file=sys.stderr)
        return ""


def _run_outcome(routes: Dict[str, Any], environment: Dict[str, Any]) -> Dict[str, Any]:
    """Points, fuel, validity and per-drone summary of a committed solution."""
    target_priority = {
        str(t.get("id", t.get("label"))): int(t.get("priority", 0) or 0)
        for t in (environment or {}).get("targets", [])
        if t.get("id", t.get("label")) is not None
    }
    per_drone: Dict[str, Dict[str, Any]] = {}
    feasibility: List[bool] = []
    for did, rd in (routes or {}).items():
        if not isinstance(rd, dict):
            continue
        per_drone[str(did)] = {
            "route": _route_list(rd),
            "points": _route_points(rd, target_priority),
            "fuel": round(_route_fuel(rd), 2),
        }
        status = _route_fuel_status(rd)
        if status["budget"] > 0:
            feasibility.append(bool(status["feasible"]))
    return {
        "total_points": sum(d["points"] for d in per_drone.values()),
        "total_fuel_used": round(sum(d["fuel"] for d in per_drone.values()), 2),
        # Unknown (None) when no drone reported a fuel budget to check against.
        "is_valid": all(feasibility) if feasibility else None,
        "routes": per_drone,
    }


# Learning: last recorded run per mission_id, so a follow-up turn that RE-SOLVES the
# same mission can retro-label the prior run as "corrected" (the user wasn't satisfied)
# and link to it via parent_run_id. RAM only — reaction inference is best-effort.
_LAST_RUN_BY_MISSION: Dict[str, str] = {}


def _capture_reasoning_run(
    user_request, environment, mission_id, result, drone_configs=None
) -> None:
    """Record this reasoning-agent mission to the learning ledger (best-effort).

    NEVER let a capture failure break the agent — swallow everything. mission_id from
    the frontend may not be a UUID; the ledger column is UUID, so only pass it through
    when it parses, else omit (a fresh run row is still recorded).

    REACTION INFERENCE (Phase 1b): a run is recorded with reaction="accepted" by
    default. If a LATER turn for the SAME mission re-solves (routes_changed), the user
    wasn't satisfied with the prior result → we retro-label that prior run
    "corrected" and link this run to it (parent_run_id). Explicit accept/reject UI
    buttons can refine this later.
    """
    try:
        import uuid as _uuid

        from server.database.mission_ledger import (
            create_agent_run,
            create_agent_trace,
            update_agent_run,
        )
        from server.database.mission_retrieval import build_retrieval_text

        mid = None
        if mission_id:
            try:
                mid = str(_uuid.UUID(str(mission_id)))
            except (ValueError, AttributeError, TypeError):
                mid = None  # non-UUID handle → record run without it

        # Reaction inference: this turn re-solved a mission that had a prior run →
        # that prior run was "corrected". Only meaningful when we can key by mission.
        _key = str(mission_id) if mission_id else None
        prior_run_id = _LAST_RUN_BY_MISSION.get(_key) if _key else None
        this_resolved = bool(result.get("routes_changed"))
        if prior_run_id and this_resolved:
            try:
                update_agent_run(prior_run_id, summary={"reaction": "corrected"})
            except Exception:
                pass

        routes = result.get("routes") or {}
        # committed sequences per drone (what actually landed on the canvas)
        committed = {
            did: (rd.get("route") or rd.get("sequence"))
            for did, rd in routes.items()
            if isinstance(rd, dict)
        }
        # Phase 1b: record the ordering/refuel constraints the agent actually
        # committed with (surfaced from the commit_ordered_plan tool args).
        hard = result.get("constraints") or {}
        refuel = result.get("refuel")
        soft = {"refuel": refuel} if refuel else {}
        run_id = create_agent_run(
            agent_version="reasoning",
            mode="reason",
            request_text=str(user_request)[:2000],
            objective=None,
            hard_constraints=hard,
            soft_preferences=soft,
            mission_id=mid,
            solver_type="opportunistic",
            parent_run_id=prior_run_id if this_resolved else None,
            embedding_text=build_retrieval_text(
                str(user_request)[:2000], environment, drone_configs
            ),
        )
        if run_id:
            # Default reaction = accepted; a later re-solve for this mission flips it
            # to "corrected" (handled above for the PRIOR run). Outcome fields are
            # recorded only when this turn produced a solution, so a pure question
            # never looks like a 0-point mission to later retrieval.
            try:
                outcome = (
                    _run_outcome(routes, environment)
                    if this_resolved and routes
                    else {}
                )
                update_agent_run(run_id, summary={"reaction": "accepted"}, **outcome)
            except Exception:
                pass
            create_agent_trace(
                run_id,
                {
                    "user_request": str(user_request),
                    "env_shape": _env_shape_features(environment),
                    "committed_routes": committed,
                    "routes_changed": bool(result.get("routes_changed")),
                    "iterations": result.get("iterations"),
                    "reasoning": result.get("reasoning", []),
                    "response": str(result.get("response", ""))[:4000],
                },
            )
            # Remember this as the mission's latest run (only when it actually solved,
            # so a pure Q&A follow-up doesn't reset the correction chain).
            if _key and this_resolved:
                _LAST_RUN_BY_MISSION[_key] = run_id
    except Exception as e:  # noqa: BLE001 — capture must never break the agent
        print(f"[capture] reasoning-run capture skipped: {e}", file=sys.stderr)


def handle_reasoning_request(request_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle an API request to the reasoning agent.

    Expected request_data:
        - user_request: str
        - environment: dict
        - drone_configs: dict
        - distance_matrix: dict (optional)
        - mission_id: str (optional) — continue this mission's agent thread
    """
    user_request = request_data.get("user_request", "")
    environment = request_data.get("environment", {})
    drone_configs = request_data.get("drone_configs", {})
    distance_matrix = request_data.get("distance_matrix")
    time_sync = request_data.get("time_sync")
    mission_id = request_data.get("mission_id")
    is_continue_from_cut = bool(request_data.get("is_continue_from_cut"))
    visited_targets = request_data.get("visited_targets") or []
    repair_routes = request_data.get("repair_routes")
    current_routes = request_data.get("current_routes") or {}

    result = run_reasoning_agent(
        user_request=user_request,
        environment=environment,
        drone_configs=drone_configs,
        distance_matrix=distance_matrix,
        time_sync=time_sync,
        mission_id=mission_id,
        is_continue_from_cut=is_continue_from_cut,
        visited_targets=visited_targets,
        repair_routes=repair_routes,
        current_routes=current_routes,
        llm_config=request_data.get("llm_config"),
    )

    # Learning capture (best-effort, never blocks the response).
    _capture_reasoning_run(user_request, environment, mission_id, result, drone_configs)

    return result


# ============================================================================
# TEST
# ============================================================================

if __name__ == "__main__":
    # Quick test
    test_env = {
        "airports": [
            {"id": "A1", "x": 40, "y": 13},
            {"id": "A2", "x": 78, "y": 64},
        ],
        "targets": [
            {"id": "T1", "x": 71, "y": 47, "priority": 5},
            {"id": "T2", "x": 49, "y": 20, "priority": 5},
            {"id": "T5", "x": 22, "y": 26, "priority": 10},
            {"id": "T6", "x": 88, "y": 29, "priority": 10},
            {"id": "T7", "x": 10, "y": 54, "priority": 10},
        ],
        "sams": [],
    }

    test_configs = {
        "1": {
            "enabled": True,
            "fuel_budget": 200,
            "start_airport": "A1",
            "end_airport": "A1",
        },
        "2": {
            "enabled": True,
            "fuel_budget": 200,
            "start_airport": "A2",
            "end_airport": "A2",
        },
    }

    result = run_reasoning_agent(
        user_request="Visit all targets with priority greater than 6 first, then visit the rest.",
        environment=test_env,
        drone_configs=test_configs,
    )

    print("\n" + "=" * 60)
    print("FINAL RESPONSE:")
    print("=" * 60)
    print(result["response"])
