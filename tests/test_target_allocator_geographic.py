from server.solver.target_allocator import AllocationStrategy, TargetAllocator


def _matrix(labels, distances):
    index = {label: i for i, label in enumerate(labels)}
    matrix = [[0.0 for _ in labels] for _ in labels]
    for (a, b), distance in distances.items():
        matrix[index[a]][index[b]] = float(distance)
        matrix[index[b]][index[a]] = float(distance)
    return {"labels": labels, "matrix": matrix}


def test_geographic_tie_splits_same_start_drones():
    allocator = TargetAllocator()
    allocator.set_distance_matrix(
        _matrix(
            ["A1", "T1", "T2", "T3", "T4"],
            {
                ("A1", "T1"): 1,
                ("A1", "T2"): 2,
                ("A1", "T3"): 3,
                ("A1", "T4"): 4,
            },
        )
    )

    targets = [{"id": f"T{i}", "type": "a", "priority": 5} for i in range(1, 5)]
    drones = {
        "1": {"enabled": True, "start_airport": "A1"},
        "2": {"enabled": True, "start_airport": "A1"},
    }

    result = allocator.allocate(
        targets,
        drones,
        [{"id": "A1"}],
        AllocationStrategy.GEOGRAPHIC,
    )

    assert len(result["1"]) == 2
    assert len(result["2"]) == 2
    assert set(result["1"] + result["2"]) == {"T1", "T2", "T3", "T4"}


def test_geographic_still_prefers_closer_start_airport():
    allocator = TargetAllocator()
    allocator.set_distance_matrix(
        _matrix(
            ["A1", "A2", "T1", "T2"],
            {
                ("A1", "T1"): 1,
                ("A2", "T1"): 10,
                ("A1", "T2"): 10,
                ("A2", "T2"): 1,
            },
        )
    )

    targets = [
        {"id": "T1", "type": "a", "priority": 5},
        {"id": "T2", "type": "a", "priority": 5},
    ]
    drones = {
        "1": {"enabled": True, "start_airport": "A1"},
        "2": {"enabled": True, "start_airport": "A2"},
    }

    result = allocator.allocate(
        targets,
        drones,
        [{"id": "A1"}, {"id": "A2"}],
        AllocationStrategy.GEOGRAPHIC,
    )

    assert result == {"1": ["T1"], "2": ["T2"]}
