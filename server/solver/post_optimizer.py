"""
Post-Optimizer for Multi-Drone Mission Planning

After initial route optimization, this module:
1. Identifies unvisited targets
2. Attempts to assign them to drones with remaining fuel capacity
3. Re-optimizes affected drone routes

This is a LangGraph-compatible tool.
"""

from server.solver.optimizers.crossing_removal import (
    CrossingRemovalOptimizer,
    crossing_removal_optimize,
)
from server.solver.optimizers.insert_missed import insert_missed_optimize
from server.solver.optimizers.or_opt import or_opt_optimize
from server.solver.optimizers.pipeline import apply_optimizer_pipeline
from server.solver.optimizers.post_insert import (
    COVERAGE_STATS_TOOL,
    POST_OPTIMIZER_TOOL,
    PostOptimizer,
    get_coverage_stats,
    get_unvisited_targets,
    post_optimize_solution,
    set_optimizer_matrix,
)
from server.solver.optimizers.refuel_legs import (
    merge_virtual_legs,
    split_refueling_drones,
)
from server.solver.optimizers.regenerate import regenerate_solution_trajectories
from server.solver.optimizers.trajectory_swap import (
    TRAJECTORY_SWAP_TOOL,
    TrajectorySwapOptimizer,
    set_trajectory_optimizer_matrix,
    trajectory_swap_optimize,
)
from server.solver.optimizers.unified import UnifiedOptimizer, unified_optimize

__all__ = [
    "COVERAGE_STATS_TOOL",
    "POST_OPTIMIZER_TOOL",
    "TRAJECTORY_SWAP_TOOL",
    "CrossingRemovalOptimizer",
    "PostOptimizer",
    "TrajectorySwapOptimizer",
    "UnifiedOptimizer",
    "apply_optimizer_pipeline",
    "crossing_removal_optimize",
    "get_coverage_stats",
    "get_unvisited_targets",
    "insert_missed_optimize",
    "merge_virtual_legs",
    "or_opt_optimize",
    "post_optimize_solution",
    "regenerate_solution_trajectories",
    "set_optimizer_matrix",
    "set_trajectory_optimizer_matrix",
    "split_refueling_drones",
    "trajectory_swap_optimize",
    "unified_optimize",
]
