#!/usr/bin/env python3
"""
Interactive test script for SAM wrapping hull intersection fix.
Run this to visualize how the algorithm handles various SAM configurations.
"""

from path_planning_core.sam_wrapping import wrap_sams
import math


def test_scenario(name, sams):
    """Test a SAM configuration and print results."""
    print(f"\n{'='*60}")
    print(f"Scenario: {name}")
    print(f"{'='*60}")
    
    # Show SAM positions and distances
    print("\nSAM Configuration:")
    for sam in sams:
        print(f"  {sam['id']}: position ({sam['x']:.1f}, {sam['y']:.1f}), range {sam['range']:.1f} km")
    
    # Check circle-to-circle overlaps
    print("\nCircle-to-Circle Overlaps:")
    n = len(sams)
    for i in range(n):
        for j in range(i + 1, n):
            s1, s2 = sams[i], sams[j]
            dist = math.hypot(s1['x'] - s2['x'], s1['y'] - s2['y'])
            sum_radii = s1['range'] + s2['range']
            overlap = dist <= sum_radii
            print(f"  {s1['id']}-{s2['id']}: distance {dist:.2f} km, sum of radii {sum_radii:.1f} km → {'OVERLAP' if overlap else 'no overlap'}")
    
    # Run wrapping algorithm
    wrapped_polygons, _ = wrap_sams(sams, min_seg=0.3)
    
    print(f"\n{'Result:'}")
    print(f"  Number of wrapper polygons: {len(wrapped_polygons)}")
    for i, poly in enumerate(wrapped_polygons):
        print(f"  Polygon {i+1}: {len(poly)} vertices")
    
    return wrapped_polygons


def main():
    print("SAM Wrapping Hull Intersection Test")
    print("=" * 60)
    
    # Test 1: The bug scenario - C intersects A-B hull but not individual circles
    test_scenario(
        "Bug Fix Test - C intersects A-B hull",
        [
            {"id": "A", "x": 0.0, "y": 0.0, "range": 2.0},
            {"id": "B", "x": 3.0, "y": 0.0, "range": 2.0},
            {"id": "C", "x": 1.5, "y": 2.8, "range": 1.0},
        ]
    )
    
    # Test 2: Three isolated SAMs - should stay separate
    test_scenario(
        "Isolated SAMs - should create 3 polygons",
        [
            {"id": "A", "x": 0.0, "y": 0.0, "range": 1.0},
            {"id": "B", "x": 20.0, "y": 0.0, "range": 1.0},
            {"id": "C", "x": 40.0, "y": 0.0, "range": 1.0},
        ]
    )
    
    # Test 3: All overlapping - should create 1 polygon
    test_scenario(
        "All overlapping - should create 1 polygon",
        [
            {"id": "A", "x": 0.0, "y": 0.0, "range": 3.0},
            {"id": "B", "x": 4.0, "y": 0.0, "range": 3.0},
            {"id": "C", "x": 2.0, "y": 3.0, "range": 3.0},
        ]
    )
    
    # Test 4: Transitive merging through hull intersection
    test_scenario(
        "Transitive Hull Merging",
        [
            {"id": "A", "x": 0.0, "y": 0.0, "range": 1.5},
            {"id": "B", "x": 2.5, "y": 0.0, "range": 1.5},
            {"id": "C", "x": 1.25, "y": 2.2, "range": 0.9},
            {"id": "D", "x": 1.25, "y": 4.0, "range": 0.9},
        ]
    )
    
    print("\n" + "="*60)
    print("All scenarios tested successfully!")
    print("="*60)


if __name__ == "__main__":
    main()
