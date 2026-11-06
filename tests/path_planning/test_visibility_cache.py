"""Multi-SAM path planning reuses the polygon visibility graph across queries
(path_planning_core/boundary_navigation.py) — same paths, far less work."""

import pytest

import path_planning_core.boundary_navigation as bn

# Three separate SAMs between the ends (the case that took minutes per matrix).
SAMS = [
    {"pos": [20, 0], "range": 5},
    {"pos": [40, 2], "range": 5},
    {"pos": [60, -2], "range": 5},
]


def _plan(start, goal):
    path, dist, method = bn.plan_path(start, goal, SAMS)
    return path, dist


def test_cached_graph_gives_the_same_path_as_a_fresh_one():
    bn._BASE_GRAPH_CACHE.clear()
    first = _plan([0, 0], [80, 0])
    assert len(bn._BASE_GRAPH_CACHE) == 1  # built once...
    second = _plan([0, 0], [80, 0])
    assert len(bn._BASE_GRAPH_CACHE) == 1  # ...and reused
    bn._BASE_GRAPH_CACHE.clear()
    fresh = _plan([0, 0], [80, 0])
    assert first[1] == pytest.approx(fresh[1]) == pytest.approx(second[1])
    assert first[1] > 80  # it went around the SAMs


def test_different_queries_on_the_same_sams_share_the_graph():
    bn._BASE_GRAPH_CACHE.clear()
    _plan([0, 0], [80, 0])
    d_other = _plan([0, 5], [80, -5])[1]
    assert len(bn._BASE_GRAPH_CACHE) == 1
    bn._BASE_GRAPH_CACHE.clear()
    assert _plan([0, 5], [80, -5])[1] == pytest.approx(d_other)


def test_bounding_box_exit_never_hides_a_crossing():
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert bn._segment_crosses_polygon((-5, 5), (15, 5), square) is True  # through it
    assert bn._segment_crosses_polygon((-5, 20), (15, 20), square) is False  # above it
    assert bn._segment_crosses_polygon((20, -5), (20, 15), square) is False  # beside it
