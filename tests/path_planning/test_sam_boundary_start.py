import math

import pytest

from path_planning_core.boundary_navigation import plan_path
from path_planning_core.sam_wrapping import wrap_sams


def test_start_on_sam_circumference_is_valid():
    radius = 10.0
    boundary = radius / math.sqrt(2)
    sams = [{"pos": [0.0, 0.0], "range": radius}]

    path, distance, method = plan_path(
        (boundary, boundary),
        (-30.0, -30.0),
        sams,
    )

    assert path
    assert distance < float("inf")
    assert not method.startswith("INVALID")


def test_start_on_sam_circumference_snaps_to_nearest_vertex():
    radius = 10.0
    angle = math.radians(43.0)
    sams = [{"pos": [0.0, 0.0], "range": radius}]

    path, distance, method = plan_path(
        (radius * math.cos(angle), radius * math.sin(angle)),
        (-30.0, -30.0),
        sams,
    )

    nearest_vertex = (
        radius * math.cos(math.radians(45.0)),
        radius * math.sin(math.radians(45.0)),
    )
    assert path
    assert distance < float("inf")
    assert not method.startswith("INVALID")
    # The ring is measured on the globe: a hair (1e-6 km) bigger on the plane.
    assert path[0] == pytest.approx(nearest_vertex, abs=1e-5)


def test_start_on_wrapped_polygon_boundary_snaps_to_nearest_vertex():
    sams = [{"x": 0.0, "y": 0.0, "range": 10.0}]
    polygons, _ = wrap_sams(sams, min_seg=0.3)
    polygon = polygons[0]
    start = (
        (polygon[0][0] + polygon[1][0]) / 2,
        (polygon[0][1] + polygon[1][1]) / 2,
    )

    path, distance, method = plan_path(
        start,
        (-30.0, -30.0),
        polygons=polygons,
    )

    assert path
    assert distance < float("inf")
    assert not method.startswith("INVALID")
    assert path[0] == polygon[1]


def test_start_a_hair_inside_sam_edge_is_treated_as_on_the_edge():
    # Within NEAR_BOUNDARY_KM (50 m) of the edge counts as ON the edge: a cut on a
    # path that skims the SAM picks up rounding and must still be replannable
    # (2026-09-25). The path still starts at the real point.
    radius = 10.0
    inside = (radius - 1e-4) / math.sqrt(2)
    sams = [{"pos": [0.0, 0.0], "range": radius}]

    path, distance, method = plan_path(
        (inside, inside),
        (-30.0, -30.0),
        sams,
    )

    assert path and path[0] == (inside, inside)
    assert distance < float("inf")
    assert not method.startswith("INVALID")


def test_start_clearly_inside_sam_remains_invalid():
    radius = 10.0
    inside = (radius - 0.5) / math.sqrt(2)
    sams = [{"pos": [0.0, 0.0], "range": radius}]

    path, distance, method = plan_path(
        (inside, inside),
        (-30.0, -30.0),
        sams,
    )

    assert path == []
    assert distance == float("inf")
    assert method.startswith("INVALID")
