"""End-to-end test: route_optimizer_node honors hard ordering constraints.

Confirms the fix for the real bug found while porting commit_ordered_plan's
DP-exact constraint solving into v4: route_optimizer_node previously solved
UNCONSTRAINED then post-hoc reordered to satisfy a soft "start_with" hint
(a heuristic that could produce a worse route than solving with the
constraint honored from the start). It now threads constraints= directly
into solver.solve()/the refuel helpers via state["constraint_program"]
["ordering_constraints"], the same validated shape reasoning_agent.py's
commit_ordered_plan tool produces.
"""

from langchain_core.messages import HumanMessage

from server.agents.v4.route_optimizer import route_optimizer_node
from server.solver.sam_distance_matrix import calculate_sam_aware_matrix


def _build_env():
    return {
        "airports": [{"id": "A1", "x": 0.0, "y": 0.0, "runway": "L"}],
        "targets": [
            {"id": "T1", "x": 10.0, "y": 0.0, "priority": 5, "type": "a"},
            {"id": "T2", "x": 5.0, "y": 5.0, "priority": 5, "type": "a"},
            {"id": "T3", "x": 0.0, "y": 10.0, "priority": 5, "type": "a"},
        ],
        "sams": [],
    }


def _dist_matrix_dict(env):
    dist_data = calculate_sam_aware_matrix(env)
    labels = dist_data.get("labels", [])
    matrix_data = dist_data.get("matrix", [])
    dist_matrix = {}
    for i, from_id in enumerate(labels):
        dist_matrix[from_id] = {}
        for j, to_id in enumerate(labels):
            dist_matrix[from_id][to_id] = float(matrix_data[i][j])
    return dist_matrix


def _build_state(ordering_constraints=None):
    env = _build_env()
    drone_configs = {
        "1": {
            "enabled": True,
            "fuel_budget": 200,
            "start_airport": "A1",
            "end_airport": "A1",
            "runway": "S",
        }
    }
    constraint_program = (
        {"ordering_constraints": ordering_constraints} if ordering_constraints else None
    )
    return {
        "messages": [HumanMessage(content="test")],
        "environment": env,
        "drone_configs": drone_configs,
        "distance_matrix": _dist_matrix_dict(env),
        "excluded_targets": [],
        "user_request": "test",
        "request_type": "optimize",
        "commands": None,
        "intent": "plan",
        "policy": {},
        "guardrails": None,
        "drone_contracts": None,
        "allocation_strategy": "efficient",
        "constraint_program": constraint_program,
        "sequencing_hints": None,
        "allocation": {"1": ["T1", "T2", "T3"]},
    }


def _route_for_drone_1(result):
    routes = result.get("routes", {})
    assert "1" in routes, f"No route computed for D1: {result}"
    return routes["1"]["route"]


def test_no_constraints_solves_optimally_by_distance():
    # Baseline: with no ordering constraint, the DP picks the shortest route
    # regardless of target id order.
    state = _build_state()
    result = route_optimizer_node(state)
    route = _route_for_drone_1(result)
    assert route[0] == "A1"
    assert route[-1] == "A1"
    assert set(route[1:-1]) == {"T1", "T2", "T3"}


def test_first_constraint_is_honored_exactly():
    # T3 is farthest from A1 (0,10 vs T1 at 10,0) — an unconstrained solve
    # would almost certainly NOT visit T3 first. Force it and confirm the
    # DP actually honors it, not just "closer to first" like the old
    # post-hoc heuristic would produce.
    state = _build_state(
        ordering_constraints={
            "first": "T3",
            "last": None,
            "before": [],
            "adjacent": [],
        }
    )
    result = route_optimizer_node(state)
    route = _route_for_drone_1(result)
    # First waypoint after the start airport must be T3.
    first_target_idx = next(i for i, wp in enumerate(route) if wp.startswith("T"))
    assert route[first_target_idx] == "T3"


def test_last_constraint_is_honored_exactly():
    state = _build_state(
        ordering_constraints={
            "first": None,
            "last": "T3",
            "before": [],
            "adjacent": [],
        }
    )
    result = route_optimizer_node(state)
    route = _route_for_drone_1(result)
    # Last waypoint before returning to the airport must be T3.
    last_target_idx = max(i for i, wp in enumerate(route) if wp.startswith("T"))
    assert route[last_target_idx] == "T3"


def test_adjacent_constraint_is_honored_exactly():
    state = _build_state(
        ordering_constraints={
            "first": None,
            "last": None,
            "before": [],
            "adjacent": [["T1", "T2"]],  # T2 must immediately follow T1
        }
    )
    result = route_optimizer_node(state)
    route = _route_for_drone_1(result)
    t1_idx = route.index("T1")
    t2_idx = route.index("T2")
    assert t2_idx == t1_idx + 1
