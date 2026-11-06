from server.solver.virtual_sorties import (
    build_sortie_slots,
    merge_sorties_to_physical_route,
    split_targets_same_base,
)


def _matrix(coords):
    labels = list(coords)
    matrix = []
    for a in labels:
        row = []
        for b in labels:
            row.append(abs(float(coords[a]) - float(coords[b])))
        matrix.append(row)
    return labels, matrix


def test_build_sortie_slots_fixed_final_airport():
    slots = build_sortie_slots(
        "1",
        {
            "start_airport": "A1",
            "end_airport": "A2",
            "fuel_budget": 15,
            "refuel_options": ["A1"],
            "max_refuels": 2,
        },
    )

    assert [slot.id for slot in slots] == ["1_S1", "1_S2", "1_S3"]
    assert [(slot.start_base, slot.end_base, slot.is_final) for slot in slots] == [
        ("A1", "A1", False),
        ("A1", "A1", False),
        ("A1", "A2", True),
    ]
    assert [slot.refuel_after for slot in slots] == [True, True, False]


def test_build_sortie_slots_any_final_airport():
    slots = build_sortie_slots(
        "2",
        {
            "start_airport": "A2",
            "end_airport": "-",
            "fuel_budget": 16,
            "refuel_options": ["A1", "A2"],
            "max_refuels": 1,
        },
    )

    assert [(slot.start_base, slot.end_base, slot.is_final) for slot in slots] == [
        ("A2", "A2", False),
        ("A2", None, True),
    ]


def test_same_base_split_can_assign_more_than_twelve_targets():
    coords = {"A": 0}
    targets = []
    for idx in range(1, 14):
        tid = f"T{idx}"
        coords[tid] = idx
        targets.append({"id": tid, "priority": 5})
    labels, matrix = _matrix(coords)
    slots = build_sortie_slots(
        "1",
        {
            "start_airport": "A",
            "end_airport": "A",
            "fuel_budget": 100,
            "refuel_options": ["A"],
            "max_refuels": 1,
        },
    )

    allocation = split_targets_same_base(
        targets,
        slots,
        labels,
        matrix,
        max_targets_per_sortie=12,
    )

    assigned = {tid for cluster in allocation.clusters.values() for tid in cluster}
    assert "T13" in assigned
    assert len(assigned) == 13
    assert all(len(cluster) <= 12 for cluster in allocation.clusters.values())
    assert allocation.unassigned == []


def test_same_base_split_leaves_individually_unreachable_targets_unassigned():
    labels, matrix = _matrix({"A": 0, "T1": 2, "T2": 20})
    slots = build_sortie_slots(
        "1",
        {
            "start_airport": "A",
            "end_airport": "A",
            "fuel_budget": 10,
            "refuel_options": ["A"],
            "max_refuels": 1,
        },
    )

    allocation = split_targets_same_base(
        [{"id": "T1", "priority": 5}, {"id": "T2", "priority": 5}],
        slots,
        labels,
        matrix,
    )

    assigned = {tid for cluster in allocation.clusters.values() for tid in cluster}
    assert assigned == {"T1"}
    assert allocation.unassigned == ["T2"]


def test_merge_sorties_to_physical_route_metadata():
    slots = build_sortie_slots(
        "1",
        {
            "start_airport": "A1",
            "end_airport": "A2",
            "fuel_budget": 15,
            "refuel_options": ["A1"],
            "max_refuels": 2,
        },
    )
    merged = merge_sorties_to_physical_route(
        "1",
        slots,
        {
            "1_S1": {
                "route": ["A1", "T1", "A1"],
                "distance": 4,
                "points": 5,
                "visited_targets": ["T1"],
            },
            "1_S2": {
                "route": ["A1", "T2", "A1"],
                "distance": 6,
                "points": 5,
                "visited_targets": ["T2"],
            },
            "1_S3": {
                "route": ["A1", "T3", "A2"],
                "distance": 8,
                "points": 5,
                "visited_targets": ["T3"],
            },
        },
    )

    assert merged["route"] == ["A1", "T1", "A1", "T2", "A1", "T3", "A2"]
    assert merged["refuel_indices"] == [2, 4]
    assert merged["refuel_stops"] == ["A1", "A1"]
    assert merged["leg_distances"] == [4, 6, 8]
    assert merged["distance"] == 18
    assert merged["visited_targets"] == ["T1", "T2", "T3"]
