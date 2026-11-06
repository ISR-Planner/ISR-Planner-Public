"""Replan start-budget regression: leg 1 uses reduced fuel, post-refuel legs the tank.

Guards the dynamic-cut bug where, after a deep cut, the drone arrived at the cut
node with little fuel (start_budget) and the opportunistic solver capped EVERY leg
at that reduced fuel — so it fit only one nearby target and parked there instead of
refueling and finishing. `start_budget` must apply to leg 1 only; every leg after a
refuel gets the full `budget`.
"""

from server.solver.opportunistic_refuel import solve_opportunistic
from server.solver.refuel_solver import _select_targets_for_refuel_leg


def _line_leg_solver(coords):
    """A 1-D leg solver on a number line. Distance = sum of gaps along start→…→end,
    picking up every available target that lies within budget of the running path.
    Greedy but deterministic — enough to exercise the refuel structure.
    """

    def solver(start, end, avail, budget, flexible):
        # Order available targets by position; walk outward from start collecting
        # those reachable, then go to end (or nearest airport if flexible).
        pos = coords
        cur = pos[start]
        visited = []
        dist = 0.0
        # collect targets in nearest-first order within budget of the leg so far
        remaining = sorted(avail, key=lambda t: abs(pos[t] - cur))
        route = [start]
        for t in remaining:
            step = abs(pos[t] - cur)
            if dist + step <= budget + 1e-9:
                dist += step
                cur = pos[t]
                route.append(t)
                visited.append(t)
        # go to end
        if flexible or end is None:
            end_id = "A0"  # the home airport in our fixture
        else:
            end_id = end
        step = abs(pos[end_id] - cur)
        if dist + step > budget + 1e-9:
            return ([], [], 0.0)  # can't reach the endpoint
        dist += step
        route.append(end_id)
        return (route, visited, dist)

    return solver


def test_replan_refuels_and_finishes_not_parks():
    # Number line:  A0=0   TK=3   T1=2   T2=6   T3=9
    # Drone starts at the CUT node C=1 with only 4 km of fuel (deep-cut replan).
    # Full tank = 12. Leg 1 (4 km) can reach TK (via T1) to refuel; the full tank
    # then reaches T2, T3 and home A0.
    coords = {"A0": 0.0, "C": 1.0, "T1": 2.0, "TK": 3.0, "T2": 6.0, "T3": 9.0}
    leg_solver = _line_leg_solver(coords)

    res = solve_opportunistic(
        start="C",
        refuel_options=["TK"],
        tanker_ids={"TK"},
        target_ids=["T1", "T2", "T3"],
        target_priority={"T1": 5, "T2": 5, "T3": 5},
        budget=12.0,  # full tank (after refuel)
        start_budget=4.0,  # reduced leg-1 fuel at the cut node
        max_refuels=2,
        leg_solver=leg_solver,
        valid_end_airports=["A0"],
    )

    visited = set(res["visited_targets"])
    # The bug parked at a single nearby target. With the fix the drone refuels and
    # collects the far targets too.
    assert "T2" in visited and "T3" in visited, res
    assert res["refuel_indices"], "expected at least one refuel on the replan"
    assert res["route"][-1] == "A0", res


def test_no_refuel_when_start_budget_covers_everything():
    # Same line but leg-1 fuel is generous — no refuel needed; result identical to a
    # fresh full-tank solve (start_budget defaulting to budget must be a no-op).
    coords = {"A0": 0.0, "C": 1.0, "T1": 2.0, "TK": 3.0, "T2": 6.0, "T3": 9.0}
    leg_solver = _line_leg_solver(coords)

    with_default = solve_opportunistic(
        start="A0",
        refuel_options=["TK"],
        tanker_ids={"TK"},
        target_ids=["T1", "T2", "T3"],
        target_priority={"T1": 5, "T2": 5, "T3": 5},
        budget=30.0,
        max_refuels=2,
        leg_solver=leg_solver,
        valid_end_airports=["A0"],
    )
    assert set(with_default["visited_targets"]) == {"T1", "T2", "T3"}, with_default


def test_replan_never_ends_at_cut_node():
    """The start on a replan is a mid-flight cut node — the drone cannot refuel or
    land there. The route must END at a real airport, never loop back to the cut
    node. Guards the bug where the cut node was treated as a refuel base, so the DFS
    'finished' by returning to it (route ended at C5-1) or parked the drone there.
    """
    # A0=0 (home), CUT=1 (cut node, NOT refuelable), TK=4 (tanker), targets spread out.
    coords = {"A0": 0.0, "CUT": 1.0, "T1": 2.0, "TK": 4.0, "T2": 7.0, "T3": 5.0}
    leg_solver = _line_leg_solver(coords)

    res = solve_opportunistic(
        start="CUT",
        refuel_options=["TK"],  # cut node is NOT a refuel option
        tanker_ids={"TK"},
        target_ids=["T1", "T2", "T3"],
        target_priority={"T1": 5, "T2": 5, "T3": 5},
        budget=12.0,
        start_budget=6.0,
        max_refuels=3,
        leg_solver=leg_solver,
        valid_end_airports=["A0"],
    )
    assert res["route"][-1] == "A0", res
    assert "CUT" not in res["route"][1:], f"cut node reappeared as a base/end: {res}"


def test_stranded_no_targets_flies_home_not_parks():
    """No target fits the remaining fuel, but the drone CAN reach an airport. It must
    FLY HOME (start → nearest real airport), not park at the cut node. Parking left an
    empty [CUT] route that froze the accept ("no valid routes to animate").
    """
    # CUT=0, A0=2 (home, close), targets far (need >>fuel). start_budget just reaches home.
    coords = {"A0": 2.0, "CUT": 0.0, "T1": 40.0, "T2": 50.0}
    leg_solver = _line_leg_solver(coords)

    res = solve_opportunistic(
        start="CUT",
        refuel_options=["A0"],
        tanker_ids=set(),
        target_ids=["T1", "T2"],
        target_priority={"T1": 5, "T2": 5},
        budget=5.0,
        start_budget=5.0,  # 5 km: reaches A0 (2 km) but no target (40+ km)
        max_refuels=0,  # cap spent — no refuels allowed
        leg_solver=leg_solver,
        valid_end_airports=["A0"],
    )
    assert res["route"][-1] == "A0", res
    assert res["route"] != ["CUT"], f"drone parked instead of flying home: {res}"
    assert not res["visited_targets"], res


def test_home_refuel_can_finish_at_same_base_after_target_leg():
    """A home-refuel mission may finish at the same base after collecting targets.

    The bug: after A1->T1->A1, the solver tried to append a flexible final leg.
    If that final leg preferred a non-final airport, the target-earning route was
    rejected and the only surviving candidate was ["A1"].
    """

    def leg_solver(start, end, avail, budget, flexible):
        if flexible:
            # Simulate the one-leg solver preferring another airport for the final
            # flexible leg. The opportunistic layer must still accept the already
            # complete A1->T1->A1 route.
            return ["A2"], [], 0.0
        if start == "A1" and end == "A1" and "T1" in avail:
            return ["A1", "T1", "A1"], ["T1"], 2.0
        return [start] if start == end else [start, end], [], 0.0

    res = solve_opportunistic(
        start="A1",
        refuel_options=["A1"],
        tanker_ids=set(),
        target_ids=["T1"],
        target_priority={"T1": 5},
        budget=15.0,
        max_refuels=5,
        leg_solver=leg_solver,
        valid_end_airports=["A1"],
    )

    assert res["route"] == ["A1", "T1", "A1"], res
    assert res["visited_targets"] == ["T1"], res
    assert res["refuel_indices"] == [], res
    assert res["leg_distances"] == [2.0], res


def test_home_refuel_chains_multiple_same_base_fuel_legs():
    """Repeated home fuel legs count only intermediate refuels, not final landing."""
    calls = {"return_legs": 0}

    def leg_solver(start, end, avail, budget, flexible):
        if flexible:
            return ["A2"], [], 0.0
        if start == "A1" and end == "A1":
            for tid in ("T1", "T2"):
                if tid in avail:
                    calls["return_legs"] += 1
                    return ["A1", tid, "A1"], [tid], 2.0
        return [start] if start == end else [start, end], [], 0.0

    res = solve_opportunistic(
        start="A1",
        refuel_options=["A1"],
        tanker_ids=set(),
        target_ids=["T1", "T2"],
        target_priority={"T1": 5, "T2": 5},
        budget=15.0,
        max_refuels=1,
        leg_solver=leg_solver,
        valid_end_airports=["A1"],
    )

    assert res["route"] == ["A1", "T1", "A1", "T2", "A1"], res
    assert res["visited_targets"] == ["T1", "T2"], res
    assert res["refuel_indices"] == [2], res
    assert res["leg_distances"] == [2.0, 2.0], res


def test_home_airport_not_refuelable_unless_checked():
    """Start/end airport may launch/land but cannot reset fuel unless allowed."""
    calls = {"return_legs": 0}

    def leg_solver(start, end, avail, budget, flexible):
        if flexible:
            return ["A1"], [], 0.0
        if start == "A1" and end == "A1":
            for tid in ("T1", "T2"):
                if tid in avail:
                    calls["return_legs"] += 1
                    return ["A1", tid, "A1"], [tid], 2.0
        if start == "TK1" and end == "TK1":
            return ["TK1"], [], 0.0
        return [start, end] if start != end else [start], [], 0.0

    res = solve_opportunistic(
        start="A1",
        refuel_options=["TK1"],
        tanker_ids={"TK1"},
        target_ids=["T1", "T2"],
        target_priority={"T1": 5, "T2": 5},
        budget=15.0,
        max_refuels=5,
        leg_solver=leg_solver,
        valid_end_airports=["A1"],
        base_progress={"A1": 0.0, "TK1": 0.5},
    )

    assert res["route"] == ["A1", "T1", "A1"], res
    assert res["visited_targets"] == ["T1"], res
    assert res["refuel_indices"] == [], res
    assert calls["return_legs"] == 1


def test_closed_loop_corridor_considers_tanker_bases():
    """A start=end mission must still evaluate allowed tanker refuel bases.

    A1->A1 has no useful linear corridor, so the corridor planner used to generate
    only repeated A1 loops. That skipped TK1 even when TK1 was legal and produced
    higher-priority routes.
    """

    def leg_solver(start, end, avail, budget, flexible):
        if flexible:
            return ["A1"], [], 0.0
        if start == "A1" and end == "A1" and "HOME" in avail:
            return ["A1", "HOME", "A1"], ["HOME"], 2.0
        if start == "A1" and end == "TK1" and "OUT" in avail:
            return ["A1", "OUT", "TK1"], ["OUT"], 5.0
        if start == "TK1" and end == "A1" and "BACK" in avail:
            return ["TK1", "BACK", "A1"], ["BACK"], 5.0
        if start == end:
            return [start], [], 0.0
        return [start, end], [], 1.0

    res = solve_opportunistic(
        start="A1",
        refuel_options=["A1", "TK1"],
        tanker_ids={"TK1"},
        target_ids=["HOME", "OUT", "BACK"],
        target_priority={"HOME": 5, "OUT": 10, "BACK": 10},
        budget=15.0,
        max_refuels=1,
        leg_solver=leg_solver,
        valid_end_airports=["A1"],
        base_progress={"A1": 0.0, "TK1": 1.0},
    )

    assert res["route"] == ["A1", "OUT", "TK1", "BACK", "A1"], res
    assert set(res["visited_targets"]) == {"OUT", "BACK"}, res
    assert res["refuel_indices"] == [2], res


def test_refuel_leg_target_cap_uses_leg_relevance_not_global_order():
    """Later equal-priority targets must remain visible to their nearby fuel leg."""
    coords = {"A4": 0.0}
    for i in range(1, 13):
        coords[f"T{i}"] = 100.0 + i
    coords.update({"T13": 1.0, "T14": 3.0, "T15": 2.0})

    labels = list(coords)
    matrix = [[abs(coords[a] - coords[b]) for b in labels] for a in labels]
    env = {"labels": labels, "matrix": matrix}
    targets = {tid: {"id": tid, "priority": 5} for tid in labels if tid.startswith("T")}

    selected = _select_targets_for_refuel_leg(
        env,
        targets,
        "A4",
        "A4",
        [f"T{i}" for i in range(1, 16)],
        False,
        12,
        ["A4"],
    )
    selected_ids = {str(t["id"]) for t in selected}

    assert {"T13", "T14", "T15"} <= selected_ids
    assert len(selected_ids) == 12
    assert "T12" not in selected_ids


def test_refuel_corridor_planner_does_not_bounce_backward_between_bases():
    coords = {
        "A1": 0.0,
        "A2": 10.0,
        "A3": 20.0,
        "A4": 30.0,
        "T1": 2.0,
        "T2": 4.0,
        "T3": 12.0,
        "T4": 14.0,
        "T13": 26.0,
        "T15": 28.0,
    }

    def leg_solver(start, end, avail, budget, flexible):
        if flexible:
            end = "A4"
        if end is None:
            end = "A4"
        s_pos = coords[start]
        e_pos = coords[end]
        direction = 1 if e_pos >= s_pos else -1
        candidates = [
            t for t in avail if min(s_pos, e_pos) <= coords[t] <= max(s_pos, e_pos)
        ]
        candidates.sort(key=lambda t: direction * coords[t])
        route = [start] + candidates + [end]
        dist = sum(abs(coords[a] - coords[b]) for a, b in zip(route, route[1:]))
        if dist > budget + 1e-9:
            return [start, end], [], abs(e_pos - s_pos)
        return route, candidates, dist

    res = solve_opportunistic(
        start="A1",
        refuel_options=["A1", "A2", "A3", "A4"],
        tanker_ids=set(),
        target_ids=["T1", "T2", "T3", "T4", "T13", "T15"],
        target_priority={tid: 5 for tid in ["T1", "T2", "T3", "T4", "T13", "T15"]},
        budget=15.0,
        max_refuels=4,
        leg_solver=leg_solver,
        valid_end_airports=["A4"],
        base_progress={"A1": 0.0, "A2": 0.33, "A3": 0.66, "A4": 1.0},
    )

    bases_in_route = [w for w in res["route"] if w.startswith("A")]
    assert bases_in_route == sorted(
        bases_in_route,
        key={"A1": 0, "A2": 1, "A3": 2, "A4": 3}.get,
    )
    assert {"T13", "T15"} <= set(res["visited_targets"])


def test_refuel_corridor_adds_repeated_intermediate_base_sorties():
    def leg_solver(start, end, avail, budget, flexible):
        if start == end == "A2":
            for tid in ("T18", "T19"):
                if tid in avail:
                    return ["A2", tid, "A2"], [tid], 8.0
        if end is None:
            end = "A3"
        if start != end:
            return [start, end], [], 10.0
        return [start], [], 0.0

    res = solve_opportunistic(
        start="A1",
        refuel_options=["A1", "A2", "A3"],
        tanker_ids=set(),
        target_ids=["T18", "T19"],
        target_priority={"T18": 5, "T19": 5},
        budget=15.0,
        max_refuels=3,
        leg_solver=leg_solver,
        valid_end_airports=["A3"],
        base_progress={"A1": 0.0, "A2": 0.5, "A3": 1.0},
    )

    assert res["route"] == [
        "A1",
        "A2",
        "T18",
        "A2",
        "T19",
        "A2",
        "A3",
    ]
    assert set(res["visited_targets"]) == {"T18", "T19"}
    assert len(res["refuel_indices"]) == 3


def test_refuel_corridor_sorties_reserve_future_refuels():
    def leg_solver(start, end, avail, budget, flexible):
        if start == end == "A2":
            for tid in ("T18", "T19"):
                if tid in avail:
                    return ["A2", tid, "A2"], [tid], 8.0
        if end is None:
            end = "A4"
        if start != end:
            return [start, end], [], 10.0
        return [start], [], 0.0

    res = solve_opportunistic(
        start="A1",
        refuel_options=["A1", "A2", "A3", "A4"],
        tanker_ids=set(),
        target_ids=["T18", "T19"],
        target_priority={"T18": 5, "T19": 5},
        budget=15.0,
        max_refuels=3,
        leg_solver=leg_solver,
        valid_end_airports=["A4"],
        base_progress={"A1": 0.0, "A2": 0.33, "A3": 0.66, "A4": 1.0},
    )

    assert res["route"][-1] == "A4", res
    assert len(res["refuel_indices"]) <= 3, res
    assert res["refuel_indices"] == sorted(res["refuel_indices"]), res
