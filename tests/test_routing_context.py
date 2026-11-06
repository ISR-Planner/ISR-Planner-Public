from server.solver.routing_context import RoutingContext, matrix_from_solution_or_cache


def test_routing_context_extracts_matrix_metadata():
    matrix = {
        "labels": ["A1", "T1"],
        "env_hash": "env123",
        "routing_model_hash": "routing123",
    }

    context = RoutingContext.from_inputs(
        env={"targets": []},
        drone_configs=None,
        distance_matrix=matrix,
    )

    assert context.env == {"targets": []}
    assert context.drone_configs == {}
    assert context.distance_matrix is matrix
    assert context.has_distance_matrix is True
    assert context.labels == ["A1", "T1"]
    assert context.env_hash == "env123"
    assert context.routing_model_hash == "routing123"


def test_routing_context_explicit_metadata_overrides_matrix_metadata():
    context = RoutingContext.from_inputs(
        env={},
        drone_configs={"1": {"enabled": True}},
        distance_matrix={
            "env_hash": "matrix-env",
            "routing_model_hash": "matrix-routing",
        },
        env_hash="explicit-env",
        routing_model_hash="explicit-routing",
    )

    assert context.env_hash == "explicit-env"
    assert context.routing_model_hash == "explicit-routing"


def test_routing_context_with_projection_preserves_routing_metadata():
    context = RoutingContext.from_inputs(
        env={"lat": True},
        drone_configs={"1": {}},
        distance_matrix={"labels": ["A1"]},
        env_hash="env123",
        routing_model_hash="routing123",
    )

    projected = context.with_projection(
        env={"x": True},
        ref_lat=42.0,
        ref_lng=-71.0,
    )

    assert projected.env == {"x": True}
    assert projected.drone_configs == context.drone_configs
    assert projected.distance_matrix == context.distance_matrix
    assert projected.ref_lat == 42.0
    assert projected.ref_lng == -71.0
    assert projected.env_hash == "env123"
    assert projected.routing_model_hash == "routing123"


def test_matrix_from_solution_or_cache_prefers_explicit_solution_matrix():
    explicit = {"labels": ["solution"]}
    cached = {"labels": ["cached"]}
    calls = {"cache": 0}

    def get_cached():
        calls["cache"] += 1
        return cached

    result = matrix_from_solution_or_cache(
        {"distance_matrix": explicit},
        get_cached,
    )

    assert result is explicit
    assert calls["cache"] == 0


def test_matrix_from_solution_or_cache_falls_back_to_cache():
    cached = {"labels": ["cached"]}
    calls = {"cache": 0}

    def get_cached():
        calls["cache"] += 1
        return cached

    result = matrix_from_solution_or_cache({}, get_cached)

    assert result is cached
    assert calls["cache"] == 1
