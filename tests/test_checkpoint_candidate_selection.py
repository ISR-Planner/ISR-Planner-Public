from server.api.solve_routes import _repair_is_better, _select_best_checkpoint_candidate


def _env():
    return {
        "targets": [
            {"id": "T1", "priority": 5},
            {"id": "T2", "priority": 5},
            {"id": "T3", "priority": 10},
        ]
    }


def test_checkpoint_candidate_same_points_lower_fuel_wins():
    repair = {"1": {"route": ["C1-1", "T1", "T3", "A1"], "distance": 106.0}}
    full = {"1": {"route": ["C1-1", "T2", "T3", "A1"], "distance": 127.0}}

    assert _repair_is_better(repair, full, _env())


def test_checkpoint_candidate_higher_points_wins_even_with_more_fuel():
    repair = {"1": {"route": ["C1-1", "T1", "A1"], "distance": 106.0}}
    full = {"1": {"route": ["C1-1", "T3", "A1"], "distance": 127.0}}

    assert not _repair_is_better(repair, full, _env())


def test_checkpoint_candidate_target_count_does_not_beat_priority():
    env = {
        "targets": [
            {"id": "T1", "priority": 1},
            {"id": "T2", "priority": 1},
            {"id": "T3", "priority": 10},
        ]
    }
    repair = {"1": {"route": ["C1-1", "T1", "T2", "A1"], "distance": 50.0}}
    full = {"1": {"route": ["C1-1", "T3", "A1"], "distance": 60.0}}

    assert not _repair_is_better(repair, full, env)


def test_checkpoint_selector_preserves_same_points_lower_fuel_candidate():
    candidates = [
        {
            "name": "full_replan",
            "success": True,
            "errors": [],
            "routes": {"1": {"route": ["C1-1", "T2", "T3", "A1"], "distance": 133.0}},
        },
        {
            "name": "preserve_baseline",
            "success": True,
            "errors": [],
            "routes": {"1": {"route": ["C1-1", "T1", "T3", "A1"], "distance": 106.0}},
        },
    ]

    selected = _select_best_checkpoint_candidate(candidates, _env())
    assert selected["name"] == "preserve_baseline"


def test_checkpoint_selector_ignores_infeasible_lower_fuel_candidate():
    candidates = [
        {
            "name": "full_replan",
            "success": True,
            "errors": [],
            "routes": {"1": {"route": ["C1-1", "T2", "T3", "A1"], "distance": 133.0}},
        },
        {
            "name": "preserve_baseline",
            "success": True,
            "errors": ["route[1] exceeds fuel budget"],
            "routes": {"1": {"route": ["C1-1", "T1", "T3", "A1"], "distance": 106.0}},
        },
    ]

    selected = _select_best_checkpoint_candidate(candidates, _env())
    assert selected["name"] == "full_replan"
