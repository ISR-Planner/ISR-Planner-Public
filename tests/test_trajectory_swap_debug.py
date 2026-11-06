from server.solver.post_optimizer import trajectory_swap_optimize


def build_test_env():
    """Minimal self-contained env for trajectory swap diagnostics test."""
    return {
        "airports": [
            {"id": "A1", "x": 0, "y": 0},
            {"id": "A2", "x": 100, "y": 0},
        ],
        "targets": [
            {"id": "T1", "x": 20, "y": 20, "priority": 5, "type": "a"},
            {"id": "T2", "x": 80, "y": 20, "priority": 6, "type": "a"},
            {"id": "T3", "x": 50, "y": 60, "priority": 7, "type": "b"},
        ],
        "sams": [],
        "drone_configs": {
            "1": {"enabled": True, "fuel_budget": 200},
            "2": {"enabled": True, "fuel_budget": 200},
        },
    }


def make_solution_from_env(env):
    # Build a trivial solution with two drones and simple routes
    waypoint_positions = {}
    for a in env.get("airports", []):
        waypoint_positions[str(a["id"])] = [float(a["x"]), float(a["y"])]
    for t in env.get("targets", []):
        waypoint_positions[str(t["id"])] = [float(t["x"]), float(t["y"])]

    # Pick two drones and two targets if available
    drone_cfgs = env.get("drone_configs", {})
    drone_ids = list(drone_cfgs.keys())[:2]
    targets = env.get("targets", [])

    routes = {}
    for i, did in enumerate(drone_ids):
        # pick airport for start and end from env airports
        airports = env.get("airports", [])
        if not airports:
            continue
        start = airports[i % len(airports)]["id"]
        end = airports[(i + 1) % len(airports)]["id"]
        # pick a target
        target = targets[i % len(targets)]["id"]
        route = [start, target, end]
        traj = [waypoint_positions[str(wp)] for wp in route]
        routes[did] = {
            "route": route,
            "trajectory": traj,
            "distance": 0.0,
            "points": 0,
            "fuel_budget": drone_cfgs.get(did, {}).get("fuel_budget", 100),
        }

    solution = {
        "routes": routes,
        "sequences": {did: ",".join(routes[did]["route"]) for did in routes},
    }
    return solution


def test_trajectory_swap_debug():
    env = build_test_env()
    solution = make_solution_from_env(env)
    drone_configs = env.get("drone_configs", {})

    res = trajectory_swap_optimize(
        solution=solution,
        env=env,
        drone_configs=drone_configs,
        distance_matrix=None,
        auto_iterate=False,
        debug=True,
    )

    assert isinstance(res, dict)
    # Expect target_diagnostics present and to be a dict
    assert "target_diagnostics" in res
    td = res["target_diagnostics"]
    assert isinstance(td, dict)
    # Each diagnostic entry should have remove_delta and candidates list
    for k, v in td.items():
        assert "remove_delta" in v
        assert "candidates" in v
        assert isinstance(v["candidates"], list)
