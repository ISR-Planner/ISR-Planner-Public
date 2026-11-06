from server.api import solve_routes


def test_solve_result_builds_routing_context():
    env = {"targets": [{"id": "T1"}]}
    drone_configs = {"1": {"enabled": True}}
    matrix = {
        "labels": ["A1", "T1"],
        "env_hash": "env123",
        "routing_model_hash": "routing123",
    }

    context = solve_routes._routing_context_from_solver_result(
        env=env,
        drone_configs=drone_configs,
        result={"distance_matrix": matrix},
    )

    assert context.env is env
    assert context.drone_configs is drone_configs
    assert context.distance_matrix is matrix
    assert context.labels == ["A1", "T1"]
    assert context.env_hash == "env123"
    assert context.routing_model_hash == "routing123"
