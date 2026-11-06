from server.solver.refuel_expansion import (
    apply_extra_sortie,
    apply_leg_insertion,
    best_extra_sortie,
    best_leg_insertion,
    expand_route_with_refuel_sorties,
    fuel_legs,
    path_distance,
    split_route_into_legs,
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


def test_split_route_into_fresh_fuel_legs():
    route = ["A2", "T9", "A2", "T10", "A2"]

    assert split_route_into_legs(route, [2]) == [
        ["A2", "T9", "A2"],
        ["A2", "T10", "A2"],
    ]


def test_best_leg_insertion_uses_per_leg_fuel():
    labels, matrix = _matrix({"A": 0, "T1": 2, "T2": 3, "B": 10, "T3": 18})
    route = ["A", "T1", "B"]

    insertion = best_leg_insertion(
        route=route,
        refuel_indices=[],
        target_id="T2",
        labels=labels,
        matrix=matrix,
        fuel_budget=11,
    )

    assert insertion is not None
    assert insertion.target_id == "T2"
    assert insertion.new_leg_distance == 10
    assert apply_leg_insertion(route, insertion) == ["A", "T1", "T2", "B"]

    assert best_leg_insertion(route, [], "T3", labels, matrix, fuel_budget=11) is None


def test_fuel_legs_report_remaining_fresh_tank_per_leg():
    labels, matrix = _matrix({"A": 0, "T1": 2, "T2": 5})
    legs = fuel_legs(["A", "T1", "A", "T2", "A"], [2], labels, matrix, 12)

    assert [leg.route for leg in legs] == [["A", "T1", "A"], ["A", "T2", "A"]]
    assert [leg.distance for leg in legs] == [4, 10]
    assert [leg.remaining_fuel for leg in legs] == [8, 2]


def test_extra_sortie_uses_remaining_refuel_capacity():
    labels, matrix = _matrix({"A": 0, "T1": 2, "T2": 4})
    route = ["A", "T1", "A"]

    sortie = best_extra_sortie(
        route=route,
        refuel_indices=[],
        target_id="T2",
        refuel_bases=["A"],
        labels=labels,
        matrix=matrix,
        fuel_budget=10,
        max_refuels=1,
    )

    assert sortie is not None
    applied = apply_extra_sortie(route, [], sortie)
    assert applied["route"] == ["A", "T1", "A", "T2", "A"]
    assert applied["refuel_indices"] == [2]

    assert (
        best_extra_sortie(route, [], "T2", ["A"], labels, matrix, 7, max_refuels=1)
        is None
    )
    assert (
        best_extra_sortie(route, [2], "T2", ["A"], labels, matrix, 10, max_refuels=1)
        is None
    )


def test_refuel_expansion_adds_intermediate_base_sorties():
    labels, matrix = _matrix(
        {"A1": 0, "T0": 5, "A2": 10, "T18": 14, "T19": 6, "A3": 20}
    )
    route_data = {
        "route": ["A1", "T0", "A2", "A3"],
        "refuel_indices": [2],
        "fuel_budget": 15,
    }

    result = expand_route_with_refuel_sorties(
        route_data=route_data,
        target_ids=["T18", "T19"],
        refuel_bases=["A2"],
        labels=labels,
        matrix=matrix,
        fuel_budget=15,
        max_refuels=3,
        target_priority={"T18": 5, "T19": 5, "T0": 5},
    )

    expanded = result["route_data"]
    assert len(result["insertions"]) == 2
    assert set(expanded["visited_targets"]) == {"T0", "T18", "T19"}

    legs = split_route_into_legs(expanded["route"], expanded["refuel_indices"])
    assert len(legs) == 4
    assert all(path_distance(leg, labels, matrix) <= 15 for leg in legs)
    assert expanded["refuel_stops"] == ["A2", "A2", "A2"]


def test_refuel_expansion_respects_max_refuels():
    labels, matrix = _matrix(
        {"A1": 0, "T0": 5, "A2": 10, "T18": 14, "T19": 6, "A3": 20}
    )
    route_data = {
        "route": ["A1", "T0", "A2", "A3"],
        "refuel_indices": [2],
        "fuel_budget": 15,
    }

    result = expand_route_with_refuel_sorties(
        route_data=route_data,
        target_ids=["T18", "T19"],
        refuel_bases=["A2"],
        labels=labels,
        matrix=matrix,
        fuel_budget=15,
        max_refuels=2,
        target_priority={"T18": 5, "T19": 5, "T0": 5},
    )

    expanded = result["route_data"]
    assert len(result["insertions"]) == 1
    assert len(expanded["refuel_indices"]) == 2
    assert len(set(expanded["visited_targets"]) & {"T18", "T19"}) == 1


def test_solution_expansion_skips_targets_another_drone_already_visits():
    # D1 has spare refuels and could reach T18 and T19 on sorties from A2, but
    # D2 already visits T18: a sortie to it would visit T18 twice (the bug seen
    # as T-T after Or-opt). Only T19 is truly missed.
    from server.solver.refuel_expansion import expand_solution_with_refuel_sorties

    labels, matrix = _matrix(
        {"A1": 0, "T0": 5, "A2": 10, "T18": 14, "T19": 6, "A3": 20}
    )
    solution = {
        "routes": {
            "1": {
                "route": ["A1", "T0", "A2", "A3"],
                "refuel_indices": [2],
                "fuel_budget": 15,
            },
            "2": {"route": ["A3", "T18", "A3"], "fuel_budget": 15},
        }
    }
    env = {"targets": [{"id": t, "priority": 5} for t in ("T0", "T18", "T19")]}
    configs = {
        "1": {
            "enabled": True,
            "fuel_budget": 15,
            "refuel_options": ["A2"],
            "max_refuels": 3,
        },
        "2": {"enabled": True, "fuel_budget": 15},
    }
    out = expand_solution_with_refuel_sorties(
        solution, env, configs, {"labels": labels, "matrix": matrix}
    )
    added = [ins["target"] for ins in out["insertions"]]
    assert added == ["T19"]
    visits = [
        w for rd in out["routes"].values() for w in rd["route"] if w.startswith("T")
    ]
    assert sorted(visits) == ["T0", "T18", "T19"]  # each target once


def test_split_leg_fuel_counts_loiter_so_insert_cannot_overpack():
    # A drone that MAY refuel but whose route has no refuel stop yet is one leg.
    # Its "fuel already used" must include its loiter: flying only (52) made the
    # optimizer see 8 km free on a 60 km tank and insert a target -> 62.58 > 60.
    from server.solver.virtual_drones import split_solution_to_virtual_legs

    labels, matrix = _matrix({"A1": 0, "T1": 10, "T2": 26, "A3": 40})
    solution = {
        "routes": {
            "1": {
                "route": ["A1", "T1", "T2", "A1"],
                "distance": 56.0,
                "fuel_budget": 60,
            }
        },
        "distance_matrix": {"labels": labels, "matrix": matrix},
    }
    cfg = {
        "1": {
            "enabled": True,
            "fuel_budget": 60,
            "refuel_stops": ["A3"],
            "refuel_options": ["A3"],
            "loiter_enabled": True,
            "loiter_times": {"a": 2},
            "speed": 1,
        }
    }
    env = {"targets": [{"id": "T1", "type": "a"}, {"id": "T2", "type": "a"}]}
    split, _, _ = split_solution_to_virtual_legs(solution, cfg, env)
    (leg,) = [v for v in split["routes"].values()]
    assert leg["distance"] == 56.0  # flying 52 + loiter 4 = the route's own total

    # Without the route total, flying + loiter is rebuilt from the matrix.
    solution["routes"]["1"].pop("distance")
    split, _, _ = split_solution_to_virtual_legs(solution, cfg, env)
    (leg,) = [v for v in split["routes"].values()]
    assert leg["distance"] == 52.0 + 4.0
