"""
Invariant: a refuel solve NEVER hard-fails / never returns an infeasible or
fabricated route. When no feasible two-leg refuel split exists (e.g. a dynamic
replan from a near-empty diamond, or added targets that don't fit), the solver
must DEGRADE to the single-leg "fly-to-end" floor — dropping the refuel and
taking whatever targets fit (down to zero) — rather than returning a fabricated
distance:0 refuel route that the validator then rejects.

See docs/ISR_PLANNER_DESCRIPTION.md §5 ("a dynamic replan can NEVER be worse ...
and NEVER hard-fails") and §8.2/§8.3/§8.5. This locks the fix so a future change
cannot silently reintroduce the all-or-nothing hard-fail.
"""

from server.solver.solver_bridge import _solve_two_leg_refuel


def _tiny_env():
    # Nodes: start diamond C, refuel airport R, end airport E, target T.
    # SAM-aware distances (symmetric); C->R is 2.76 (the failing-case distance).
    labels = ["C", "R", "E", "T"]
    #          C     R     E     T
    matrix = [
        [0.0, 2.76, 8.0, 9.0],  # C
        [2.76, 0.0, 6.0, 1.0],  # R
        [8.0, 6.0, 0.0, 5.0],  # E
        [9.0, 1.0, 5.0, 0.0],  # T
    ]
    return {
        "matrix_labels": labels,
        "matrix": matrix,
        "distance_matrix": matrix,
        # C = diamond (checkpoint start), R/E = real airports, T = target.
        "airports": [
            {"id": "C", "x": 0.0, "y": 0.0, "is_checkpoint": True, "runway": "L"},
            {"id": "R", "x": 0.0, "y": 0.0, "runway": "L"},
            {"id": "E", "x": 0.0, "y": 0.0, "runway": "L"},
        ],
        "targets": [{"id": "T", "x": 0.0, "y": 0.0, "priority": 5}],
    }


def test_no_feasible_split_degrades_to_single_leg_floor():
    """Near-zero fuel: cannot reach the refuel airport. Must NOT fabricate a
    refuel route; must return a plain single-leg route with NO refuel_airport."""
    env = _tiny_env()
    targets = [{"id": "T", "x": 0.0, "y": 0.0, "priority": 5}]

    sol = _solve_two_leg_refuel(
        env_for_solver=env,
        fuel_budget=0.0,  # near-empty diamond — nothing fits
        start_id="C",
        end_id="E",
        refuel_airport_id="R",
        candidate_targets=targets,
        flexible_endpoint=False,
        did="1",
        drone_cfg={"fuel_capacity": 0.0},
    )

    # Degraded floor route: single-leg, no refuel, NOT a fabricated 2-leg route.
    assert sol is not None
    assert "refuel_airport" not in sol, (
        "floor route must NOT carry refuel_airport (would be validated as an "
        f"infeasible 2-leg route): {sol}"
    )
    assert (
        "leg1_distance" not in sol and "leg2_distance" not in sol
    ), f"floor route must not carry fabricated leg distances: {sol}"
    # It still returns a usable route to the end (fly-to-end floor).
    assert isinstance(sol.get("route"), list) and len(sol["route"]) >= 1
    # No targets are forced in when they don't fit (opportunity, not obligation).
    assert isinstance(sol.get("visited_targets"), list)


def test_feasible_split_still_refuels():
    """Plenty of fuel: a real two-leg refuel route is still produced (no
    regression — the floor only triggers when nothing fits)."""
    env = _tiny_env()
    targets = [{"id": "T", "x": 0.0, "y": 0.0, "priority": 5}]

    sol = _solve_two_leg_refuel(
        env_for_solver=env,
        fuel_budget=100.0,  # ample
        start_id="C",
        end_id="E",
        refuel_airport_id="R",
        candidate_targets=targets,
        flexible_endpoint=False,
        did="1",
        drone_cfg={"fuel_budget": 100.0},  # §11.10: one fuel value = the tank
    )
    assert sol is not None
    assert isinstance(sol.get("route"), list) and len(sol["route"]) >= 2


def test_start_fuel_contract_leg1_reduced_leg2_full_tank():
    """§11.10: ONE fuel value (fuel_budget = the tank). On a dynamic replan, leg 1
    flies on the REDUCED start fuel (passed as the fuel_budget arg here) while leg 2
    starts AFTER refueling on a FULL TANK (leg2_budget). A route whose leg 2 only
    fits on a full tank must still be accepted — the reduced leg-1 fuel must NOT
    constrain leg 2. This locks the start_fuel/full-tank split the unification rests
    on (fuel_capacity retired)."""
    # C->R = 2.76 (reachable on a small start fuel); R->E = 6.0 (needs > leg1 fuel).
    env = _tiny_env()
    sol = _solve_two_leg_refuel(
        env_for_solver=env,
        fuel_budget=3.0,  # reduced leg-1 start fuel: enough for C->R (2.76)
        start_id="C",
        end_id="E",
        refuel_airport_id="R",
        candidate_targets=[],  # no targets — isolate the leg-fit logic
        flexible_endpoint=False,
        did="1",
        drone_cfg={"fuel_budget": 3.0},
        leg2_budget=10.0,  # full tank after refuel: covers R->E (6.0)
    )
    # Leg 2 (6.0) exceeds the leg-1 start fuel (3.0) but fits the full tank (10.0),
    # so a real 2-leg refuel route must be produced — NOT degraded to the floor.
    assert sol is not None
    assert (
        sol.get("refuel_airport") == "R"
    ), f"leg 2 must use the full tank (leg2_budget), not the reduced leg-1 fuel: {sol}"
    assert sol.get("leg2_distance", 0) <= 10.0 + 1e-6
