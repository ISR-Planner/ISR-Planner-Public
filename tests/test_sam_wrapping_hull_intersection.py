"""
Test for SAM wrapping with circle-to-hull intersection cases.

This test demonstrates the fix for the issue where a SAM circle that intersects
a convex hull (but not any individual SAM circles in the hull) should be
wrapped into the same cluster.
"""

from path_planning_core.sam_wrapping import wrap_sams


def test_sam_intersects_hull_not_circles():
    """
    Test case where SAM-C intersects the convex hull of (SAM-A + SAM-B)
    but doesn't intersect either SAM-A or SAM-B individually.

    Arrangement:
        SAM-A (0, 0, r=2)    SAM-B (3, 0, r=2)  [A and B overlap, form initial cluster]

        Convex hull of A+B extends from y=-2 to y=+2 (approximately)

        SAM-C (1.5, 2.8, r=1.0) sits above the midpoint
        - Distance to A: ~3.38 km > (2 + 1) = 3.0 km (no direct overlap)
        - Distance to B: ~3.38 km > (2 + 1) = 3.0 km (no direct overlap)
        - But C's bottom (y=1.8) reaches into the hull's top edge (y~2)
    """
    sams = [
        {"id": "A", "x": 0.0, "y": 0.0, "range": 2.0},
        {"id": "B", "x": 3.0, "y": 0.0, "range": 2.0},  # Overlaps with A
        {"id": "C", "x": 1.5, "y": 2.8, "range": 1.0},  # Above A-B, intersects hull
    ]

    wrapped_polygons, _ = wrap_sams(sams, min_seg=0.3)

    # Without the fix: would produce 2 separate polygons (A+B cluster, C alone)
    # With the fix: should produce 1 polygon wrapping all three SAMs
    assert len(wrapped_polygons) == 1, (
        f"Expected 1 cluster wrapping all SAMs, got {len(wrapped_polygons)} clusters. "
        "SAM-C should be merged into the A+B hull."
    )

    # The single polygon should have enough vertices to wrap all three circles
    polygon = wrapped_polygons[0]
    assert len(polygon) >= 3, "Polygon should be valid"


def test_sam_intersects_hull_edge_case():
    """
    Test with arrangement where middle SAM doesn't overlap endpoints
    but intersects their hull.

    Arrangement:
        SAM-A (0, 0, r=1.5)    SAM-B (2.5, 0, r=1.5)  [A and B overlap, form cluster]

        SAM-C (1.25, 2.2, r=0.8) sits above midpoint
        - Distance to A: ~2.5 km > (1.5 + 0.8) = 2.3 km (no direct overlap)
        - Distance to B: ~2.5 km > (1.5 + 0.8) = 2.3 km (no direct overlap)
        - But C's circle intersects the hull boundary between A and B
    """
    sams = [
        {"id": "A", "x": 0.0, "y": 0.0, "range": 1.5},
        {"id": "B", "x": 2.5, "y": 0.0, "range": 1.5},  # Overlaps A
        {"id": "C", "x": 1.25, "y": 2.2, "range": 0.8},  # Above A-B
    ]

    wrapped_polygons, _ = wrap_sams(sams, min_seg=0.3)

    # All three should be in one cluster
    assert len(wrapped_polygons) == 1, (
        f"Expected 1 cluster, got {len(wrapped_polygons)}. "
        "SAM-C intersects the hull of A-B cluster."
    )


def test_isolated_sams_remain_separate():
    """
    Test that truly isolated SAMs still get separate polygons.
    """
    sams = [
        {"id": "A", "x": 0.0, "y": 0.0, "range": 1.0},
        {"id": "B", "x": 20.0, "y": 0.0, "range": 1.0},
        {"id": "C", "x": 40.0, "y": 0.0, "range": 1.0},
    ]

    wrapped_polygons, _ = wrap_sams(sams, min_seg=0.3)

    # All three are far apart and should remain separate
    assert len(wrapped_polygons) == 3, (
        f"Expected 3 separate clusters, got {len(wrapped_polygons)}. "
        "Isolated SAMs should not be merged."
    )


def test_transitive_hull_intersection():
    """
    Test transitive merging: A-B cluster hull intersects C.
    After C merges with A-B, the expanded hull intersects D.
    All should end up in one cluster through iterative hull expansion.
    """
    sams = [
        {"id": "A", "x": 0.0, "y": 0.0, "range": 1.5},
        {"id": "B", "x": 2.5, "y": 0.0, "range": 1.5},  # Overlaps A
        {"id": "C", "x": 1.25, "y": 2.2, "range": 0.9},  # Intersects A-B hull
        {
            "id": "D",
            "x": 1.25,
            "y": 4.0,
            "range": 0.9,
        },  # Intersects A-B-C hull after C merges
    ]

    wrapped_polygons, _ = wrap_sams(sams, min_seg=0.3)

    # All should merge transitively into one cluster
    assert (
        len(wrapped_polygons) == 1
    ), f"Expected 1 cluster from transitive hull merging, got {len(wrapped_polygons)}."


if __name__ == "__main__":
    # Run tests manually
    test_sam_intersects_hull_not_circles()
    print("✓ test_sam_intersects_hull_not_circles passed")

    test_sam_intersects_hull_edge_case()
    print("✓ test_sam_intersects_hull_edge_case passed")

    test_isolated_sams_remain_separate()
    print("✓ test_isolated_sams_remain_separate passed")

    test_transitive_hull_intersection()
    print("✓ test_transitive_hull_intersection passed")

    print("\n✅ All SAM wrapping hull intersection tests passed!")
