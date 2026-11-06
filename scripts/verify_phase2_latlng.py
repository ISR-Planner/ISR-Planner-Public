#!/usr/bin/env python3
"""Phase 2 verification script for lat/lng migration.

This script verifies that the solver correctly:
1. Projects lat/lng environments to local km for internal processing
2. Solves missions with plausible km-scale distances
3. Unprojects trajectories back to lat/lng coordinates

Run from project root:
    python scripts/verify_phase2_latlng.py
"""

import json
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from server.solver.solver_bridge import solve_mission, solve_mission_with_allocation


def load_test_env():
    """Load the converted tmp_environment.json."""
    env_path = project_root / "webapp" / "editor" / "tmp_environment.json"
    with open(env_path) as f:
        return json.load(f)


def build_drone_configs(env):
    """Build drone configs from env's drone_states, using km fuel budgets.

    Only enables drones whose start/end airports exist in the environment.
    """
    # Get valid airport IDs
    valid_airports = {a["id"] for a in env.get("airports", [])}

    drone_configs = {}
    for did, state in env.get("drone_states", {}).items():
        start_ap = state.get("start_airport", "A1")
        end_ap = state.get("end_airport", "A1")

        # Only enable drones with valid airports
        enabled = start_ap in valid_airports and end_ap in valid_airports

        # Use fuel_budget_km if present, otherwise use a default 50 km
        fuel_budget = env.get("fuel_budget_km", state.get("fuel_budget_km", 50.0))
        drone_configs[did] = {
            "enabled": enabled,
            "fuel_budget": fuel_budget,
            "start_airport": start_ap if start_ap in valid_airports else "A1",
            "end_airport": end_ap if end_ap in valid_airports else "A1",
            "speed": 120,  # km/h
        }

        if not enabled:
            print(f"  D{did}: DISABLED (airport {start_ap}/{end_ap} not in {valid_airports})")

    return drone_configs


def validate_trajectory_point(point, idx, drone_id):
    """Validate a single trajectory point is a valid lat/lng."""
    errors = []

    if not isinstance(point, (list, tuple)):
        errors.append(f"D{drone_id} point {idx}: not a list/tuple: {type(point)}")
        return errors

    if len(point) < 2:
        errors.append(f"D{drone_id} point {idx}: too few elements: {len(point)}")
        return errors

    lat, lng = point[0], point[1]

    # Check lat is in SF range [37.7, 37.85]
    if not (37.65 <= lat <= 37.90):
        errors.append(f"D{drone_id} point {idx}: lat {lat:.6f} outside SF range [37.65, 37.90]")

    # Check lng is in SF range [-122.55, -122.30]
    if not (-122.55 <= lng <= -122.30):
        errors.append(f"D{drone_id} point {idx}: lng {lng:.6f} outside SF range [-122.55, -122.30]")

    return errors


def validate_solution(result, test_name):
    """Validate solution meets Phase 2 requirements."""
    print(f"\n{'='*60}")
    print(f"Validating: {test_name}")
    print(f"{'='*60}")

    errors = []
    warnings = []

    # Check routes exist
    routes = result.get("routes", {})
    if not routes:
        errors.append("No routes in result")
        return errors, warnings

    print(f"Found {len(routes)} drone routes")

    for did, route_data in routes.items():
        route = route_data.get("route", [])
        distance = route_data.get("distance", 0)
        trajectory = route_data.get("trajectory", [])

        print(f"\nDrone {did}:")
        print(f"  Route: {route}")
        print(f"  Distance: {distance:.2f} km")
        print(f"  Trajectory points: {len(trajectory)}")

        # Validate distance is in plausible km range
        # For SF-scale missions: 0-100 km is reasonable
        if distance < 0:
            errors.append(f"D{did}: negative distance {distance}")
        elif distance > 200:
            warnings.append(f"D{did}: unusually large distance {distance:.1f} km")
        elif distance == 0 and len(route) > 2:
            warnings.append(f"D{did}: zero distance but route has {len(route)} waypoints")

        # Validate trajectory points are lat/lng
        if not trajectory:
            if route and len(route) > 1:
                warnings.append(f"D{did}: no trajectory but route exists")
        else:
            # Check first, last, and some middle points
            check_indices = [0, len(trajectory) - 1]
            if len(trajectory) > 10:
                check_indices.extend([len(trajectory) // 4, len(trajectory) // 2, 3 * len(trajectory) // 4])

            for idx in check_indices:
                point = trajectory[idx]
                point_errors = validate_trajectory_point(point, idx, did)
                errors.extend(point_errors)

            # Show sample trajectory points
            if trajectory:
                print(f"  First point: [{trajectory[0][0]:.6f}, {trajectory[0][1]:.6f}]")
                if len(trajectory) > 1:
                    print(f"  Last point:  [{trajectory[-1][0]:.6f}, {trajectory[-1][1]:.6f}]")

    return errors, warnings


def main():
    print("=" * 60)
    print("Phase 2 Lat/Lng Migration Verification")
    print("=" * 60)

    # Load test environment
    print("\nLoading test environment...")
    env = load_test_env()

    print(f"  Airports: {len(env.get('airports', []))}")
    print(f"  Targets: {len(env.get('targets', []))}")
    print(f"  SAMs: {len(env.get('sams', []))}")

    # Show sample coordinates to verify they're lat/lng
    if env.get("airports"):
        a = env["airports"][0]
        print(f"  Sample airport: {a.get('id')} at lat={a.get('lat')}, lng={a.get('lng')}")
    if env.get("targets"):
        t = env["targets"][0]
        print(f"  Sample target: {t.get('id')} at lat={t.get('lat')}, lng={t.get('lng')}")
    if env.get("sams"):
        s = env["sams"][0]
        center = s.get("center", {})
        print(f"  Sample SAM: center lat={center.get('lat')}, lng={center.get('lng')}, range_km={s.get('range_km')}")

    # Build drone configs
    drone_configs = build_drone_configs(env)
    print(f"\nDrone configs: {len(drone_configs)} drones")
    for did, cfg in list(drone_configs.items())[:2]:
        print(f"  D{did}: fuel={cfg['fuel_budget']:.1f} km, start={cfg['start_airport']}")

    all_errors = []
    all_warnings = []

    # Test 1: solve_mission
    print("\n" + "=" * 60)
    print("TEST 1: solve_mission()")
    print("=" * 60)
    try:
        result1 = solve_mission(env, drone_configs)
        errors, warnings = validate_solution(result1, "solve_mission")
        all_errors.extend(errors)
        all_warnings.extend(warnings)
    except Exception as e:
        all_errors.append(f"solve_mission() raised exception: {e}")
        import traceback
        traceback.print_exc()

    # Test 2: solve_mission_with_allocation
    print("\n" + "=" * 60)
    print("TEST 2: solve_mission_with_allocation()")
    print("=" * 60)
    try:
        result2 = solve_mission_with_allocation(
            env, drone_configs,
            allocation_strategy="balanced",
            post_optimize=False
        )
        errors, warnings = validate_solution(result2, "solve_mission_with_allocation")
        all_errors.extend(errors)
        all_warnings.extend(warnings)

        # Also check wrapped_polygons if present
        wrapped_polygons = result2.get("wrapped_polygons", [])
        if wrapped_polygons:
            print(f"\nWrapped polygons: {len(wrapped_polygons)}")
            for i, poly in enumerate(wrapped_polygons[:2]):
                if poly:
                    print(f"  Polygon {i}: {len(poly)} points")
                    # Check first point is lat/lng
                    if poly[0]:
                        lat, lng = poly[0][0], poly[0][1]
                        if not (37.65 <= lat <= 37.90 and -122.55 <= lng <= -122.30):
                            all_errors.append(f"Polygon {i} point 0: invalid lat/lng ({lat}, {lng})")
                        else:
                            print(f"    First point: [{lat:.6f}, {lng:.6f}] ✓")
    except Exception as e:
        all_errors.append(f"solve_mission_with_allocation() raised exception: {e}")
        import traceback
        traceback.print_exc()

    # Summary
    print("\n" + "=" * 60)
    print("VERIFICATION SUMMARY")
    print("=" * 60)

    if all_warnings:
        print(f"\n⚠️  Warnings ({len(all_warnings)}):")
        for w in all_warnings:
            print(f"   - {w}")

    if all_errors:
        print(f"\n❌ Errors ({len(all_errors)}):")
        for e in all_errors:
            print(f"   - {e}")
        print("\n❌ VERIFICATION FAILED")
        return 1
    else:
        print("\n✅ All checks passed!")
        print("✅ Phase 2 verification SUCCESSFUL")
        return 0


if __name__ == "__main__":
    sys.exit(main())
