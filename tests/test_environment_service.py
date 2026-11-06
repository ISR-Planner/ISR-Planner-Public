from server.api.environment_service import EnvironmentService


def test_environment_service_uses_injected_paths_and_matrix_callbacks(tmp_path):
    cached_matrix = {
        "labels": ["A1", "T1"],
        "paths": {"A1->T1": [[0, 0], [1, 1]]},
        "excluded_targets": ["T2"],
        "wrapped_polygons": [[[0, 0], [1, 0], [0, 1]]],
    }
    calls = {"cleared": 0}

    service = EnvironmentService(
        tmp_path,
        get_current_matrix=lambda: cached_matrix,
        prepare_distance_matrix=lambda env, buffer: {},
        clear_cached_matrix=lambda: calls.__setitem__("cleared", calls["cleared"] + 1),
    )

    env = {
        "airports": [{"id": "A1", "x": 0, "y": 0}],
        "targets": [{"id": "T1", "x": 1, "y": 1}],
        "sams": [],
    }
    service.save_environment(env)

    assert service.env_path == tmp_path / "environment.json"
    assert service.load_environment() == env
    assert service.read_environment_text() is not None

    response = service.environment_response()
    assert response["environment"] == env
    assert response["matrix_cached"] is True
    assert response["matrix_info"] == {
        "num_waypoints": 2,
        "num_sam_avoiding_paths": 1,
        "excluded_targets": ["T2"],
        "wrapped_polygons": [[[0, 0], [1, 0], [0, 1]]],
    }

    service.refresh_distance_matrix(env)
    assert calls["cleared"] == 1


def test_environment_service_prepares_matrix_when_sams_exist(tmp_path):
    calls = {"prepared": []}

    def prepare(env, buffer):
        calls["prepared"].append((env, buffer))
        return {"labels": ["A1"], "paths": {}}

    service = EnvironmentService(
        tmp_path,
        get_current_matrix=lambda: None,
        prepare_distance_matrix=prepare,
        clear_cached_matrix=lambda: None,
    )

    env = {"airports": [], "targets": [], "sams": [{"id": "S1"}]}
    service.refresh_distance_matrix(env)

    assert calls["prepared"] == [(env, 0.0)]


def test_environment_service_export_uses_injected_matrix(tmp_path):
    cached_matrix = {
        "labels": ["A1", "T1"],
        "paths": {"A1->T1": [[0, 0], [1, 1]]},
        "env_hash": "env123",
        "routing_model": {"buffer": 0.0},
        "routing_model_hash": "routing123",
        "distance_matrix_id": "matrix123",
        "excluded_targets": [],
        "wrapped_polygons": [],
        "cluster_info": {"clusters": 0},
        "buffer": 0.0,
        "cache_hit": True,
    }
    service = EnvironmentService(
        tmp_path,
        get_current_matrix=lambda: cached_matrix,
        prepare_distance_matrix=lambda env, buffer: {},
        clear_cached_matrix=lambda: None,
    )

    env = {"airports": [], "targets": [], "sams": []}
    service.save_environment(env)

    first_filename, first_export = service.build_export()
    second_filename, second_export = service.build_export()

    assert first_filename.startswith("ise-env-")
    assert first_filename.endswith("-1.json")
    assert second_filename.endswith("-2.json")
    assert first_export["environment"] == env
    assert first_export["matrix_cached"] is True
    assert first_export["env_hash"] == "env123"
    assert first_export["routing_model_hash"] == "routing123"
    assert first_export["distance_matrix_id"] == "matrix123"
    assert first_export["matrix_info"]["num_waypoints"] == 2
    assert second_export["matrix_info"]["cache_hit"] is True
