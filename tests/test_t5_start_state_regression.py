import contextlib
import io
import json
from pathlib import Path

import pytest

from server.agents import reasoning_agent
from server.solver.sam_distance_matrix import calculate_sam_aware_matrix
from server.solver.solve_mission import solve_mission_with_allocation

FIXTURE = Path(__file__).parent / "fixtures" / "isr_env_20260721-1253_N1_1.json"
KNOWN_T5_ROUTE = [
    "A1",
    "T5",
    "T6",
    "T10",
    "TK1",
    "T2",
    "T9",
    "T1",
    "T8",
    "T7",
    "T4",
    "A1",
    "T3",
    "A1",
]


def _load_fixture():
    data = json.loads(FIXTURE.read_text())
    return data["env"], data["drone_configs"]


def _matrix(env):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        return calculate_sam_aware_matrix(env)


def _loiter_by_target(env, cfg):
    if not cfg.get("loiter_enabled"):
        return {}
    times = cfg.get("loiter_times") or {}
    out = {}
    for target in env.get("targets", []):
        target_id = str(target["id"])
        target_type = str(target.get("type", "a")).lower()
        out[target_id] = float(times.get(target_type, 0) or 0)
    return out


def _evaluate_route(env, cfg, route, *, start_fuel=None):
    dm = _matrix(env)
    labels = [str(label) for label in dm["labels"]]
    idx = {label: i for i, label in enumerate(labels)}
    matrix = dm["matrix"]
    loiter = _loiter_by_target(env, cfg)
    target_priority = {
        str(t["id"]): int(t.get("priority", 0) or 0) for t in env.get("targets", [])
    }
    refuel_nodes = {
        str(node)
        for node in (cfg.get("refuel_options") or [])
        if node and str(node) not in ("-", "None")
    }
    full_tank = float(cfg["fuel_budget"])
    first_leg_cap = full_tank if start_fuel is None else float(start_fuel)

    total = 0.0
    leg = 0.0
    legs = []
    refuel_stops = []
    visited = []

    for i, (a, b) in enumerate(zip(route, route[1:]), start=1):
        step = float(matrix[idx[str(a)]][idx[str(b)]]) + loiter.get(str(b), 0.0)
        total += step
        leg += step
        if str(b) in target_priority and str(b) not in visited:
            visited.append(str(b))
        if i < len(route) - 1 and str(b) in refuel_nodes:
            legs.append(leg)
            refuel_stops.append(str(b))
            leg = 0.0
    legs.append(leg)

    caps = [first_leg_cap] + [full_tank] * (len(legs) - 1)
    return {
        "total": total,
        "legs": legs,
        "caps": caps,
        "valid": all(distance <= cap + 1e-6 for distance, cap in zip(legs, caps)),
        "visited": visited,
        "points": sum(target_priority[tid] for tid in set(visited)),
        "refuel_stops": refuel_stops,
    }


def _t5_checkpoint_state(env, drone_configs):
    t5 = next(t for t in env["targets"] if t["id"] == "T5")
    cfg = json.loads(json.dumps(drone_configs["1"]))

    checkpoint_env = json.loads(json.dumps(env))
    checkpoint_env["targets"] = [
        t for t in checkpoint_env["targets"] if t["id"] != "T5"
    ]
    checkpoint_env["checkpoints"] = [{"id": "C1-1", "lat": t5["lat"], "lng": t5["lng"]}]

    prefix_eval = _evaluate_route(env, cfg, ["A1", "T5"])
    start_fuel = float(cfg["fuel_budget"]) - prefix_eval["total"]

    checkpoint_configs = json.loads(json.dumps(drone_configs))
    checkpoint_configs["1"]["start_airport"] = "C1-1"
    checkpoint_configs["1"]["end_airport"] = "A1"
    checkpoint_configs["1"]["start_fuel"] = start_fuel
    return checkpoint_env, checkpoint_configs, prefix_eval["total"], start_fuel


def test_known_t5_first_tk1_route_is_feasible_and_lower_fuel():
    env, drone_configs = _load_fixture()
    cfg = drone_configs["1"]

    result = _evaluate_route(env, cfg, KNOWN_T5_ROUTE)

    assert result["valid"]
    assert result["points"] == 47
    assert set(result["visited"]) == {f"T{i}" for i in range(1, 11)}
    assert result["refuel_stops"] == ["TK1", "A1"]
    # Great-circle distances (the old flat map read ~0.1% long: 117.99/160.36/34.57).
    assert result["legs"] == pytest.approx([117.83, 160.24, 34.59], abs=0.05)
    assert result["total"] == pytest.approx(312.66, abs=0.05)


def test_checkpoint_at_t5_selects_low_fuel_tk1_suffix():
    env, drone_configs = _load_fixture()
    checkpoint_env, checkpoint_configs, prefix_fuel, _start_fuel = _t5_checkpoint_state(
        env, drone_configs
    )

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        result = solve_mission_with_allocation(
            env=checkpoint_env,
            drone_configs=checkpoint_configs,
            allocation_strategy="geographic",
            use_sam_aware_distances=True,
            post_optimize=False,
        )

    route_data = result["routes"]["1"]
    route = route_data["route"]
    mission_fuel = prefix_fuel + float(route_data["distance"])

    assert "TK1" in route
    assert mission_fuel == pytest.approx(312.94, abs=1.0)


def test_agent_first_target_uses_checkpoint_start_pipeline():
    env, drone_configs = _load_fixture()

    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        reasoning_agent.set_mission_context(env, drone_configs)
        summary = reasoning_agent.commit_ordered_plan.invoke(
            {"drone_id": "1", "constraints": {"first": "T5"}}
        )

    route_data = reasoning_agent._solved_routes["1"]

    assert route_data["route"] == KNOWN_T5_ROUTE
    assert route_data["distance"] == pytest.approx(312.94, abs=1.0)
    assert route_data["leg_distances"] == pytest.approx(
        [117.83, 160.24, 34.59], abs=0.1
    )
    assert route_data["refuel_stops"] == ["TK1", "A1"]
    assert "Feasible: YES" in summary


def test_agent_start_with_delegates_to_checkpoint_start_pipeline():
    env, drone_configs = _load_fixture()

    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        reasoning_agent.set_mission_context(env, drone_configs)
        summary = reasoning_agent.solve_full_mission.invoke({"start_with": ["T5"]})

    route_data = reasoning_agent._solved_routes["1"]

    assert route_data["route"] == KNOWN_T5_ROUTE
    assert route_data["distance"] == pytest.approx(312.94, abs=1.0)
    assert route_data["leg_distances"] == pytest.approx(
        [117.83, 160.24, 34.59], abs=0.1
    )
    assert "Feasible: YES" in summary
