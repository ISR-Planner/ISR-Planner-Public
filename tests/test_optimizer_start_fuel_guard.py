from server.solver.optimizers.insert_missed import insert_missed_optimize


def test_insert_missed_uses_checkpoint_start_fuel_for_normal_route():
    env = {
        "airports": [{"id": "C1-2"}, {"id": "A1"}],
        "targets": [{"id": "T1", "priority": 5}, {"id": "T2", "priority": 5}],
    }
    labels = ["C1-2", "A1", "T1", "T2"]
    # Existing route C1-2->T1->A1 is 4. Inserting T2 between T1 and A1
    # costs +2, so it fits a full 10 tank but not the 5 fuel remaining at cut.
    matrix = [
        [0, 4, 2, 4],
        [4, 0, 2, 3],
        [2, 2, 0, 1],
        [4, 3, 1, 0],
    ]
    solution = {
        "routes": {
            "2": {
                "route": ["C1-2", "T1", "A1"],
                "sequence": "C1-2,T1,A1",
                "distance": 4.0,
                "fuel_budget": 10.0,
            }
        }
    }
    configs = {
        "2": {
            "enabled": True,
            "fuel_budget": 10.0,
            "start_fuel": 5.0,
            "target_access": {"a": True},
        }
    }

    result = insert_missed_optimize(
        solution,
        env,
        configs,
        {"labels": labels, "matrix": matrix},
    )

    assert result["success"]
    assert result["insertions"] == []
    assert result["routes"]["2"]["route"] == ["C1-2", "T1", "A1"]
