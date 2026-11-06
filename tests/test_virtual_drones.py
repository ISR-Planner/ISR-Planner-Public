"""Tests for virtual-drone expansion/merge (multi-stop refuel)."""

from server.solver.virtual_drones import (
    expand_to_virtual_drones,
    is_virtual,
    merge_virtual_to_physical,
    parent_of,
)


def test_no_refuel_passthrough():
    cfgs = {"1": {"start_airport": "A1", "end_airport": "A1", "fuel_budget": 100}}
    v = expand_to_virtual_drones(cfgs)
    assert set(v.keys()) == {"1"}
    assert v["1"]["total_legs"] == 1
    assert v["1"]["is_virtual"] is False


def test_one_stop_two_legs():
    cfgs = {
        "1": {
            "start_airport": "A1",
            "end_airport": "A3",
            "refuel_stops": ["A2"],
            "fuel_budget": 100,
        }
    }
    v = expand_to_virtual_drones(cfgs)
    assert set(v.keys()) == {"1_L1", "1_L2"}
    assert (v["1_L1"]["start_airport"], v["1_L1"]["end_airport"]) == ("A1", "A2")
    assert (v["1_L2"]["start_airport"], v["1_L2"]["end_airport"]) == ("A2", "A3")
    assert v["1_L1"]["total_legs"] == 2 and v["1_L2"]["total_legs"] == 2
    # Every leg gets the full tank.
    assert v["1_L1"]["fuel_budget"] == 100 and v["1_L2"]["fuel_budget"] == 100


def test_four_stops_five_legs():
    cfgs = {
        "1": {
            "start_airport": "A1",
            "end_airport": "A6",
            "refuel_stops": ["A2", "A3", "A4", "A5"],
            "fuel_budget": 50,
        }
    }
    v = expand_to_virtual_drones(cfgs)
    assert [f"1_L{i}" for i in range(1, 6)] == sorted(v.keys())
    chain = [
        (v[f"1_L{i}"]["start_airport"], v[f"1_L{i}"]["end_airport"])
        for i in range(1, 6)
    ]
    assert chain == [
        ("A1", "A2"),
        ("A2", "A3"),
        ("A3", "A4"),
        ("A4", "A5"),
        ("A5", "A6"),
    ]


def test_start_fuel_only_on_first_leg():
    cfgs = {
        "1": {
            "start_airport": "A1",
            "end_airport": "A3",
            "refuel_stops": ["A2"],
            "fuel_budget": 100,
            "start_fuel": 30,  # reduced leg-1 fuel from a dynamic replan
        }
    }
    v = expand_to_virtual_drones(cfgs)
    assert v["1_L1"].get("start_fuel") == 30  # leg 1 inherits the reduced start fuel
    assert "start_fuel" not in v["1_L2"]  # leg 2 (post-refuel) = full tank only


def test_zero_start_fuel_is_preserved_on_first_leg():
    cfgs = {
        "1": {
            "start_airport": "A1",
            "end_airport": "A3",
            "refuel_stops": ["A2"],
            "fuel_budget": 100,
            "start_fuel": 0,
        }
    }
    v = expand_to_virtual_drones(cfgs)
    assert v["1_L1"].get("start_fuel") == 0
    assert "start_fuel" not in v["1_L2"]


def test_legacy_refuel_airport_single_value():
    cfgs = {
        "1": {
            "start_airport": "A1",
            "end_airport": "A1",
            "refuel_airport": "A2",
            "fuel_budget": 100,
        }
    }
    v = expand_to_virtual_drones(cfgs)
    assert set(v.keys()) == {"1_L1", "1_L2"}


def test_legacy_any_dash_is_no_refuel():
    # "-" was the retired ANY option → migrates to no refuel.
    cfgs = {
        "1": {
            "start_airport": "A1",
            "end_airport": "A1",
            "refuel_airport": "-",
            "fuel_budget": 100,
        }
    }
    v = expand_to_virtual_drones(cfgs)
    assert set(v.keys()) == {"1"}


def test_mixed_real_and_virtual():
    cfgs = {
        "1": {
            "start_airport": "A1",
            "end_airport": "A3",
            "refuel_stops": ["A2"],
            "fuel_budget": 100,
        },
        "2": {"start_airport": "A1", "end_airport": "A1", "fuel_budget": 100},
    }
    v = expand_to_virtual_drones(cfgs)
    assert sorted(v.keys()) == ["1_L1", "1_L2", "2"]


def test_disabled_drone_not_expanded():
    cfgs = {
        "1": {
            "enabled": False,
            "refuel_stops": ["A2"],
            "start_airport": "A1",
            "end_airport": "A3",
        }
    }
    v = expand_to_virtual_drones(cfgs)
    assert set(v.keys()) == {"1"}


def test_merge_two_legs_drops_join_and_emits_metadata():
    cfgs = {
        "1": {
            "start_airport": "A1",
            "end_airport": "A3",
            "refuel_stops": ["A2"],
            "fuel_budget": 100,
        }
    }
    vroutes = {
        "1_L1": {
            "route": ["A1", "T1", "A2"],
            "distance": 5.0,
            "visited_targets": ["T1"],
        },
        "1_L2": {
            "route": ["A2", "T2", "A3"],
            "distance": 7.0,
            "visited_targets": ["T2"],
        },
    }
    merged = merge_virtual_to_physical(vroutes, cfgs)["1"]
    # Join A2 appears once (not duplicated).
    assert merged["route"] == ["A1", "T1", "A2", "T2", "A3"]
    assert merged["distance"] == 12.0
    assert merged["visited_targets"] == ["T1", "T2"]
    assert merged["leg_distances"] == [5.0, 7.0]
    # Refuel index points at A2 in the merged route (index 2).
    assert merged["refuel_indices"] == [2]
    assert merged["refuel_stops"] == ["A2"]


def test_merge_no_refuel_passthrough():
    cfgs = {"2": {"start_airport": "A1", "end_airport": "A1", "fuel_budget": 100}}
    vroutes = {
        "2": {"route": ["A1", "T5", "A1"], "distance": 3.0, "visited_targets": ["T5"]}
    }
    merged = merge_virtual_to_physical(vroutes, cfgs)["2"]
    assert merged["route"] == ["A1", "T5", "A1"]


def test_parent_and_is_virtual_helpers():
    assert parent_of("1_L2") == "1" and parent_of("3") == "3"
    assert is_virtual("1_L1") is True and is_virtual("2") is False


# --- Optimizer-path N-leg split/merge (cross-leg moves) ---
from server.solver.virtual_drones import (
    _route_refuel_positions,
    merge_virtual_legs_n,
    split_solution_to_virtual_legs,
)


def test_route_refuel_positions_in_order():
    route = ["A1", "T1", "A2", "T2", "A4", "T3", "A3"]
    assert _route_refuel_positions(route, ["A2", "A4"]) == [2, 4]


def test_split_three_legs_then_merge_roundtrip():
    cfgs = {
        "1": {
            "enabled": True,
            "start_airport": "A1",
            "end_airport": "A3",
            "refuel_stops": ["A2", "A4"],
            "fuel_budget": 50,
        }
    }
    sol = {
        "routes": {
            "1": {
                "route": ["A1", "T1", "A2", "T10", "A4", "T2", "A3"],
                "distance": 30.0,
                "fuel_budget": 50,
                "trajectory": [],
            }
        }
    }
    split, scfgs, lm = split_solution_to_virtual_legs(sol, cfgs)
    # 3 virtual legs, each a plain drone with fixed endpoints, no refuel.
    assert sorted(split["routes"].keys()) == ["1_L1", "1_L2", "1_L3"]
    assert split["routes"]["1_L1"]["route"] == ["A1", "T1", "A2"]
    assert split["routes"]["1_L2"]["route"] == ["A2", "T10", "A4"]
    assert split["routes"]["1_L3"]["route"] == ["A4", "T2", "A3"]
    assert (
        scfgs["1_L2"]["start_airport"] == "A2" and scfgs["1_L2"]["end_airport"] == "A4"
    )
    assert scfgs["1_L2"]["refuel_airport"] == ""
    assert lm == {"1_L1": "1", "1_L2": "1", "1_L3": "1"}

    # Merge back (simulate optimizer moved T10 from L2 to L1).
    split["routes"]["1_L1"]["route"] = ["A1", "T1", "T10", "A2"]
    split["routes"]["1_L2"]["route"] = ["A2", "A4"]
    merged = merge_virtual_legs_n(split, cfgs, lm)["routes"]["1"]
    assert merged["route"] == ["A1", "T1", "T10", "A2", "A4", "T2", "A3"]
    assert merged["refuel_indices"] == [3, 4]  # A2 then A4 joins
    assert len(merged["leg_distances"]) == 3


def test_non_refuel_drone_passthrough_in_optimizer_split():
    cfgs = {
        "2": {
            "enabled": True,
            "start_airport": "A1",
            "end_airport": "A1",
            "fuel_budget": 100,
        }
    }
    sol = {"routes": {"2": {"route": ["A1", "T5", "A1"], "distance": 3.0}}}
    split, scfgs, lm = split_solution_to_virtual_legs(sol, cfgs)
    assert set(split["routes"].keys()) == {"2"}
    assert lm == {"2": "2"}


def test_merge_drops_an_empty_first_leg():
    # An optimizer move took every target of the FIRST sortie (A4 -> A4), so
    # leg 1 is a bare [A4, A4]. It is neither a flight nor a refuel: the merged
    # drone starts at A4 and flies leg 2 only (was: L1 A4->A4 0, 2 tanks).
    sol = {
        "routes": {
            "4_L1": {
                "route": ["A4", "A4"],
                "distance": 0.0,
                "points": 0,
                "_virtual_leg": True,
                "_parent_drone": "4",
                "_leg_number": 1,
            },
            "4_L2": {
                "route": ["A4", "T7", "T8", "A1"],
                "distance": 279.0,
                "points": 10,
                "_virtual_leg": True,
                "_parent_drone": "4",
                "_leg_number": 2,
            },
        }
    }
    merged = merge_virtual_legs_n(sol, {}, {"4_L1": "4", "4_L2": "4"})["routes"]["4"]
    assert merged["route"] == ["A4", "T7", "T8", "A1"]
    assert merged["leg_distances"] == [279.0]
    assert merged["refuel_indices"] == [] and merged["refuel_stops"] == []


def test_merge_keeps_one_leg_when_every_leg_is_empty():
    sol = {
        "routes": {
            "4_L1": {
                "route": ["A4", "A4"],
                "distance": 0.0,
                "_virtual_leg": True,
                "_parent_drone": "4",
                "_leg_number": 1,
            },
            "4_L2": {
                "route": ["A4", "A4"],
                "distance": 0.0,
                "_virtual_leg": True,
                "_parent_drone": "4",
                "_leg_number": 2,
            },
        }
    }
    merged = merge_virtual_legs_n(sol, {}, {"4_L1": "4", "4_L2": "4"})["routes"]["4"]
    assert merged["route"] == ["A4", "A4"]
    assert merged["leg_distances"] == [0.0] and merged["refuel_indices"] == []
