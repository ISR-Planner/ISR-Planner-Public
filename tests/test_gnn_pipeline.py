import pytest

from server.agents.agent_metrics import run_gnn_with_metrics
from server.gnn import build_dataset_record, build_graph_sample
from server.gnn.graph_schema import NODE_FEATURE_NAMES
from server.gnn.model import run_gnn_planner
from server.gnn.torch_gnn import save_checkpoint, train_assignment_model


def _env():
    return {
        "airports": [{"id": "A1", "x": 0, "y": 0}],
        "targets": [
            {"id": "T1", "x": 10, "y": 0, "priority": 10, "type": "a"},
            {"id": "T2", "x": 40, "y": 0, "priority": 5, "type": "b"},
        ],
        "sams": [],
    }


def _drone_configs():
    return {
        "1": {
            "enabled": True,
            "fuel_budget": 100,
            "speed_kmh": 120,
            "start_airport": "A1",
            "end_airport": "A1",
            "target_access": {"a": True, "b": True, "c": False, "d": False, "e": False},
        }
    }


def test_graph_sample_contains_mission_features():
    graph = build_graph_sample(_env(), drone_configs=_drone_configs()).to_dict()

    assert graph["n_nodes"] == 3
    assert graph["n_drones"] == 1
    assert "target_type_a" in graph["node_feature_names"]
    assert "target_access_ratio" in graph["node_feature_names"]
    assert "feasible_drone_ratio" in graph["edge_feature_names"]
    assert "fuel_budget_norm" in graph["drone_feature_names"]
    assert len(graph["edge_drone_features"]) == graph["n_edges"]


def _latlng_env_with_sam():
    """A lat/lng env with a 30km SAM ring centred at 37.760.

    Both targets sit *outside* the ring so neither is excluded by the
    distance-matrix builder: T1 hugs it (~35km out, 5km of clearance) while T2
    is far clear (~150km out). A1 sits 40km south, so the A1->T1 leg has to
    route around the ring.
    """
    return {
        "airports": [{"id": "A1", "lat": 37.400, "lng": -122.400}],
        "targets": [
            {"id": "T1", "lat": 38.075, "lng": -122.400, "priority": 10, "type": "a"},
            {"id": "T2", "lat": 39.110, "lng": -122.400, "priority": 5, "type": "b"},
        ],
        "sams": [
            {
                "id": "S1",
                "center": {"lat": 37.760, "lng": -122.400},
                "range_km": 30.0,
            }
        ],
    }


def test_sam_clearance_discriminates_between_near_and_far_targets():
    """SAM proximity must produce signal for targets that survive exclusion.

    The old `sam_risk` feature was non-zero only strictly *inside* a ring, but
    the distance-matrix builder drops any target inside a ring before the graph
    is built, so it was identically 0.0 for every surviving target.
    `sam_clearance_norm` instead varies continuously outside the rings.
    """
    graph = build_graph_sample(
        _latlng_env_with_sam(), drone_configs=_drone_configs()
    ).to_dict()

    clearance_idx = NODE_FEATURE_NAMES.index("sam_clearance_norm")
    clearance = {
        nid: graph["node_features"][i][clearance_idx]
        for i, nid in enumerate(graph["node_ids"])
    }

    # Both targets survive exclusion (neither is inside the ring)...
    assert {"T1", "T2"} <= set(clearance), clearance
    # ...and the one hugging the ring is measurably closer to the threat.
    assert clearance["T1"] < clearance["T2"], clearance
    assert 0.0 <= clearance["T1"] < 1.0, clearance


def test_dataset_record_captures_route_labels():
    teacher = {
        "routes": {
            "1": {
                "route": ["A1", "T1", "A1"],
                "distance": 20.0,
                "trajectory": [[0, 0], [10, 0], [0, 0]],
            }
        },
        "sequences": {"1": "A1,T1,A1"},
    }

    record = build_dataset_record(_env(), _drone_configs(), teacher, sample_id="sample")

    assert record["schema_version"] == "gnn_dataset_v1"
    labels = record["graph"]["labels"]
    assert labels["target_assignment"] == {"T1": "1"}
    assert labels["route_sequences"] == {"1": ["A1", "T1", "A1"]}


def test_gnn_metrics_adapter_returns_comparison_shape():
    metrics = run_gnn_with_metrics(_env(), _drone_configs())
    data = metrics.to_dict()

    assert data["agent_type"] == "gnn"
    assert data["llm_calls"] == 0
    assert data["routes"] is not None
    assert "gnn_heuristic_v0" in data["response_text"]


def _route_teacher():
    return {
        "routes": {
            "1": {
                "route": ["A1", "T1", "T2", "A1"],
                "distance": 80.0,
                "trajectory": [[0, 0], [10, 0], [40, 0], [0, 0]],
            }
        },
        "sequences": {"1": "A1,T1,T2,A1"},
    }


def _train_route_checkpoint(tmp_path, drone_configs=None):
    from server.gnn import save_checkpoint, train_route_edge_model

    configs = drone_configs or _drone_configs()
    records = [
        build_dataset_record(_env(), configs, _route_teacher(), sample_id=f"s-{i}")
        for i in range(4)
    ]
    checkpoint = train_route_edge_model(records, epochs=20, seed=3)
    path = tmp_path / "route.pt"
    save_checkpoint(checkpoint, path)
    return path


def test_route_decoder_produces_routes_without_the_exact_solver(tmp_path):
    """The learned decoder must route end-to-end with no Held-Karp involvement.

    This is the configuration that can scale: the exact solver enumerates every
    subset of targets and runs Held-Karp on each, so it is ~O(3^N) per drone.
    """
    path = _train_route_checkpoint(tmp_path)

    result = run_gnn_planner(
        _env(), _drone_configs(), route_model_path=path, use_solver=False
    )

    assert "torch_gnn_route_edge_v1" in result.model_name
    route = result.solution["routes"]["1"]["route"]
    # Starts and ends at the drone's airport, and visits both reachable targets.
    assert route[0] == "A1" and route[-1] == "A1"
    assert set(route[1:-1]) == {"T1", "T2"}
    assert result.error is None


def test_route_decoder_respects_fuel_budget(tmp_path):
    """Feasibility is enforced by the decoder, not trusted to the model.

    T2 is a 80km round trip; with a 30km budget the decoder must drop it rather
    than emit a route the drone cannot fly.
    """
    broke = {
        "1": {
            **_drone_configs()["1"],
            "fuel_budget": 30,
        }
    }
    path = _train_route_checkpoint(tmp_path)

    result = run_gnn_planner(_env(), broke, route_model_path=path, use_solver=False)

    route = result.solution["routes"]["1"]["route"]
    assert "T2" not in route, route
    assert result.solution["routes"]["1"]["distance"] <= 30.0, route


def test_stale_checkpoint_reports_actionable_error():
    """A checkpoint from an older feature schema must say so, not raise a shape error."""
    from server.gnn.torch_gnn import assert_checkpoint_matches_schema

    with pytest.raises(ValueError, match="different feature schema"):
        assert_checkpoint_matches_schema(
            {"node_dim": 15, "edge_dim": 6, "drone_dim": 10}
        )


def test_trainable_gnn_checkpoint_drives_inference(tmp_path):
    teacher = {
        "routes": {
            "1": {
                "route": ["A1", "T1", "A1"],
                "distance": 20.0,
                "trajectory": [[0, 0], [10, 0], [0, 0]],
            }
        },
        "sequences": {"1": "A1,T1,A1"},
    }
    records = [
        build_dataset_record(_env(), _drone_configs(), teacher, sample_id=f"sample-{i}")
        for i in range(4)
    ]
    checkpoint = train_assignment_model(records, epochs=30, learning_rate=0.01, seed=1)
    path = tmp_path / "gnn.pt"
    save_checkpoint(checkpoint, path)

    result = run_gnn_planner(_env(), _drone_configs(), model_path=path)

    assert result.model_name == "torch_gnn_v1"
    assert result.assignments
    assert result.solution["routes"]
