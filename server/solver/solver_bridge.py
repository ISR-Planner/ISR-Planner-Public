"""Compatibility facade for solver orchestration.

The implementation lives in focused modules under `server.solver`. This module
keeps the historical import path stable for API routes, agents, tests, and
scripts while Phase 5 completes.
"""

from .airport_rules import (
    REFUEL_STOP_COST_KM,
    _airport_accepts_drone,
    is_real_airport,
)
from .constraints import (
    _constraints_for_drone,
    _normalize_constraints,
    _ordering_legal,
    _subset_constraints_for_leg,
)
from .matrix_cache import (
    _build_distance_matrix,
    _compute_env_hash,
    clear_cached_matrix,
    get_current_matrix,
    prepare_distance_matrix,
)
from .mission_parser import _parse_env_for_solver
from .pipeline_runner import run_pipeline_on_solution
from .projection import (
    _apply_cruise_altitude_to_sams,
    _project_env_to_local_km,
    _project_solution_trajectories,
    _unproject_solution_trajectories,
)
from .refuel_solver import (
    _refuel_options,
    _relax_positions_to_detection_radii,
    _relocate_refuel_stops,
)
from .sam_distance_matrix import (
    calculate_sam_aware_matrix,
    clear_matrix_cache,
    get_cached_matrix,
)
from .solve_config import _normalize_fuel_budget_keys
from .solve_mission import (
    _solve_one_leg,
    _solve_opportunistic_drone,
    _solve_two_leg_refuel,
    solve_mission,
    solve_mission_with_allocation,
)

__all__ = [
    "REFUEL_STOP_COST_KM",
    "_airport_accepts_drone",
    "is_real_airport",
    "_constraints_for_drone",
    "_normalize_constraints",
    "_ordering_legal",
    "_subset_constraints_for_leg",
    "_build_distance_matrix",
    "_compute_env_hash",
    "clear_cached_matrix",
    "get_current_matrix",
    "prepare_distance_matrix",
    "_parse_env_for_solver",
    "run_pipeline_on_solution",
    "_apply_cruise_altitude_to_sams",
    "_project_env_to_local_km",
    "_project_solution_trajectories",
    "_unproject_solution_trajectories",
    "_refuel_options",
    "_relax_positions_to_detection_radii",
    "_relocate_refuel_stops",
    "calculate_sam_aware_matrix",
    "clear_matrix_cache",
    "get_cached_matrix",
    "_normalize_fuel_budget_keys",
    "_solve_one_leg",
    "_solve_opportunistic_drone",
    "_solve_two_leg_refuel",
    "solve_mission",
    "solve_mission_with_allocation",
]
