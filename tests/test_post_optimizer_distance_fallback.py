from server.solver.post_optimizer import PostOptimizer


def test_post_optimizer_sam_fallback_uses_wrapped_polygons():
    optimizer = PostOptimizer()
    optimizer.set_environment(
        {
            "airports": [
                {"id": "A1", "x": -20.0, "y": 0.0},
                {"id": "A2", "x": 20.0, "y": 0.0},
            ],
            "targets": [],
            "sams": [{"id": "S1", "pos": [0.0, 0.0], "range": 10.0}],
        }
    )

    distance = optimizer._get_distance("A1", "A2")

    assert distance > 40.0
    assert distance < float("inf")
