"""
Tests for ISRTrajectoryPlanner (polygon-based)

Tests the trajectory planner that generates drone paths avoiding SAM polygon zones.

NOTE: The ISRTrajectoryPlanner.__init__ accepts polygons but internally passes them
to boundary_plan_path which expects SAM dictionaries. This is a design issue.
These tests focus on the no-obstacle cases which work correctly.

Tests with obstacles are marked to show the API mismatch.
"""

import math
import os
import sys

import pytest

from path_planning_core.latlng_utils import plane_dist

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from server.solver.trajectory_planner_poly import ISRTrajectoryPlanner


class TestISRTrajectoryPlannerInit:
    """Tests for planner initialization."""

    def test_init_with_no_polygons(self):
        """Planner should initialize with empty polygon list."""
        planner = ISRTrajectoryPlanner([])
        assert planner.polygons == []

    def test_init_with_none(self):
        """Planner should handle None input."""
        planner = ISRTrajectoryPlanner(None)
        assert planner.polygons == []

    def test_init_with_single_polygon(self):
        """Planner should store a single polygon correctly."""
        polygon = [[0, 0], [10, 0], [10, 10], [0, 10]]
        planner = ISRTrajectoryPlanner([polygon])
        assert len(planner.polygons) == 1
        assert planner.polygons[0] == [
            (0.0, 0.0),
            (10.0, 0.0),
            (10.0, 10.0),
            (0.0, 10.0),
        ]

    def test_init_with_multiple_polygons(self):
        """Planner should store multiple polygons."""
        poly1 = [[0, 0], [5, 0], [5, 5], [0, 5]]
        poly2 = [[20, 20], [30, 20], [30, 30], [20, 30]]
        planner = ISRTrajectoryPlanner([poly1, poly2])
        assert len(planner.polygons) == 2

    def test_init_skips_empty_polygons(self):
        """Planner should skip empty polygon lists."""
        poly1 = [[0, 0], [5, 0], [5, 5]]
        planner = ISRTrajectoryPlanner([poly1, [], poly1])
        assert len(planner.polygons) == 2


class TestPlanSegmentNoObstacles:
    """Tests for single segment planning without obstacles."""

    def test_direct_path_no_obstacles(self):
        """Direct path when no polygons exist."""
        planner = ISRTrajectoryPlanner([])
        path, distance = planner.plan_segment((0, 0), (10, 0), debug=False)

        assert len(path) == 2
        assert path[0] == [0, 0]
        assert path[1] == [10, 0]
        assert abs(distance - 10.0) < 0.001

    def test_direct_path_diagonal(self):
        """Direct diagonal path with no obstacles."""
        planner = ISRTrajectoryPlanner([])
        path, distance = planner.plan_segment((0, 0), (3, 4), debug=False)

        assert len(path) == 2
        assert abs(distance - 5.0) < 0.001  # 3-4-5 triangle

    def test_same_start_end(self):
        """Path from point to itself should have zero distance."""
        planner = ISRTrajectoryPlanner([])
        path, distance = planner.plan_segment((5, 5), (5, 5), debug=False)

        assert distance == 0.0

    def test_vertical_path(self):
        """Vertical path should work."""
        planner = ISRTrajectoryPlanner([])
        path, distance = planner.plan_segment((5, 0), (5, 10), debug=False)

        assert len(path) == 2
        assert abs(distance - 10.0) < 0.001

    def test_negative_to_positive(self):
        """Path from negative to positive coordinates."""
        planner = ISRTrajectoryPlanner([])
        path, distance = planner.plan_segment((-5, -5), (5, 5), debug=False)

        assert len(path) == 2
        expected = math.hypot(10, 10)
        assert abs(distance - expected) < 0.001


class TestPlanSegmentWithObstacles:
    """Tests for segment planning with obstacles."""

    def test_path_around_obstacle(self):
        """Path should go around a polygon obstacle."""
        obstacle = [[4, -2], [6, -2], [6, 2], [4, 2]]
        planner = ISRTrajectoryPlanner([obstacle])
        path, distance = planner.plan_segment((0, 0), (10, 0), debug=False)
        assert len(path) >= 2
        assert distance > 10.0

    def test_path_not_blocked(self):
        """Path that doesn't intersect obstacle should be direct."""
        obstacle = [[50, 50], [60, 50], [60, 60], [50, 60]]
        planner = ISRTrajectoryPlanner([obstacle])
        path, distance = planner.plan_segment((0, 0), (10, 0), debug=False)
        assert len(path) == 2
        assert abs(distance - 10.0) < 0.001


class TestGenerateTrajectory:
    """Tests for full trajectory generation."""

    def test_empty_route(self):
        """Empty route should return empty trajectory."""
        planner = ISRTrajectoryPlanner([])
        waypoints = {"A1": [0, 0], "A2": [10, 10]}

        trajectory = planner.generate_trajectory([], waypoints, debug=False)
        assert trajectory == []

    def test_single_waypoint_route(self):
        """Route with single waypoint should return empty trajectory."""
        planner = ISRTrajectoryPlanner([])
        waypoints = {"A1": [0, 0]}

        trajectory = planner.generate_trajectory(["A1"], waypoints, debug=False)
        assert trajectory == []

    def test_simple_two_point_route(self):
        """Simple two-point route should work."""
        planner = ISRTrajectoryPlanner([])
        waypoints = {"A1": [0, 0], "T1": [10, 0]}

        trajectory = planner.generate_trajectory(["A1", "T1"], waypoints, debug=False)

        assert len(trajectory) == 2
        assert trajectory[0] == [0.0, 0.0]
        assert trajectory[1] == [10.0, 0.0]

    def test_multi_point_route(self):
        """Multi-point route should concatenate segments."""
        planner = ISRTrajectoryPlanner([])
        waypoints = {
            "A1": [0, 0],
            "T1": [10, 0],
            "T2": [10, 10],
        }

        trajectory = planner.generate_trajectory(
            ["A1", "T1", "T2", "A1"], waypoints, debug=False
        )

        # Should have 4 points (no duplicates at junctions)
        assert len(trajectory) == 4
        assert trajectory[0] == [0.0, 0.0]
        assert trajectory[1] == [10.0, 0.0]
        assert trajectory[2] == [10.0, 10.0]
        assert trajectory[3] == [0.0, 0.0]

    def test_missing_waypoint_position(self):
        """Route with missing waypoint position should return empty."""
        planner = ISRTrajectoryPlanner([])
        waypoints = {"A1": [0, 0]}  # T1 is missing

        trajectory = planner.generate_trajectory(["A1", "T1"], waypoints, debug=False)
        assert trajectory == []

    def test_trajectory_avoids_obstacle(self):
        """Trajectory should avoid polygon obstacles."""
        obstacle = [[4, 4], [6, 4], [6, 6], [4, 6]]
        planner = ISRTrajectoryPlanner([obstacle])
        waypoints = {"A1": [0, 0], "T1": [2, 5], "T2": [8, 5], "A2": [10, 0]}
        trajectory = planner.generate_trajectory(
            ["A1", "T1", "T2", "A2"], waypoints, debug=False
        )
        assert len(trajectory) > 0

    def test_drone_id_in_debug(self, capsys):
        """Drone ID should appear in debug output."""
        planner = ISRTrajectoryPlanner([])
        waypoints = {"A1": [0, 0], "T1": [10, 0]}

        planner.generate_trajectory(["A1", "T1"], waypoints, drone_id="1", debug=True)

        captured = capsys.readouterr()
        assert "[D1]" in captured.out

    def test_round_trip_route(self):
        """Route that returns to start."""
        planner = ISRTrajectoryPlanner([])
        waypoints = {
            "A1": [0, 0],
            "T1": [10, 0],
            "T2": [10, 10],
        }

        trajectory = planner.generate_trajectory(
            ["A1", "T1", "T2", "A1"], waypoints, debug=False
        )

        assert len(trajectory) == 4
        # First and last points should be the same
        assert trajectory[0] == trajectory[-1]


class TestTrajectoryDistance:
    """Tests for trajectory distance calculations."""

    def test_trajectory_distance_direct(self):
        """Calculate total trajectory distance for direct path."""
        planner = ISRTrajectoryPlanner([])
        waypoints = {
            "A1": [0, 0],
            "T1": [3, 4],  # Distance 5 from A1
            "A2": [3, 0],  # Distance 4 from T1
        }

        trajectory = planner.generate_trajectory(
            ["A1", "T1", "A2"], waypoints, debug=False
        )

        # Calculate total distance
        total_dist = 0
        for i in range(len(trajectory) - 1):
            dx = trajectory[i + 1][0] - trajectory[i][0]
            dy = trajectory[i + 1][1] - trajectory[i][1]
            total_dist += math.hypot(dx, dy)

        # Should be 5 + 4 = 9
        assert abs(total_dist - 9.0) < 0.001

    def test_trajectory_distance_square(self):
        """Distance around a square."""
        planner = ISRTrajectoryPlanner([])
        waypoints = {
            "A": [0, 0],
            "B": [10, 0],
            "C": [10, 10],
            "D": [0, 10],
        }

        trajectory = planner.generate_trajectory(
            ["A", "B", "C", "D", "A"], waypoints, debug=False
        )

        # Calculate total distance
        total_dist = 0
        for i in range(len(trajectory) - 1):
            dx = trajectory[i + 1][0] - trajectory[i][0]
            dy = trajectory[i + 1][1] - trajectory[i][1]
            total_dist += math.hypot(dx, dy)

        # Should be 4 * 10 = 40
        assert abs(total_dist - 40.0) < 0.001


class TestEdgeCases:
    """Edge case tests."""

    def test_very_close_points(self):
        """Handle very close points."""
        planner = ISRTrajectoryPlanner([])
        path, distance = planner.plan_segment((0, 0), (0.001, 0.001), debug=False)

        assert len(path) == 2
        assert distance < 0.01

    def test_large_coordinates(self):
        """Handle large coordinate values."""
        planner = ISRTrajectoryPlanner([])
        path, distance = planner.plan_segment((0, 0), (10000, 10000), debug=False)

        assert len(path) == 2
        # Far from the plane's centre, plane units are not km: the length is
        # the great-circle distance between the two plane points.
        expected_dist = plane_dist((0, 0), (10000, 10000))
        assert expected_dist < math.hypot(10000, 10000)
        assert abs(distance - expected_dist) < 0.001

    def test_negative_coordinates(self):
        """Handle negative coordinates."""
        planner = ISRTrajectoryPlanner([])
        waypoints = {"A1": [-10, -10], "T1": [10, 10]}

        trajectory = planner.generate_trajectory(["A1", "T1"], waypoints, debug=False)

        assert len(trajectory) == 2
        assert trajectory[0] == [-10.0, -10.0]
        assert trajectory[1] == [10.0, 10.0]

    def test_float_coordinates(self):
        """Handle float coordinates."""
        planner = ISRTrajectoryPlanner([])
        waypoints = {"A1": [1.5, 2.7], "T1": [8.3, 4.1]}

        trajectory = planner.generate_trajectory(["A1", "T1"], waypoints, debug=False)

        assert len(trajectory) == 2
        assert abs(trajectory[0][0] - 1.5) < 0.001
        assert abs(trajectory[0][1] - 2.7) < 0.001

    def test_many_waypoints(self):
        """Handle route with many waypoints."""
        planner = ISRTrajectoryPlanner([])
        waypoints = {f"T{i}": [i * 10, 0] for i in range(10)}

        route = [f"T{i}" for i in range(10)]
        trajectory = planner.generate_trajectory(route, waypoints, debug=False)

        assert len(trajectory) == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
