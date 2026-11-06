"""Distance-matrix cache helpers for solver orchestration."""

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from path_planning_core.latlng_utils import plane_dist

from .sam_distance_matrix import (
    calculate_sam_aware_matrix,
    clear_matrix_cache,
    get_cached_matrix,
)


def _compute_env_hash(env: Dict[str, Any]) -> str:
    """Compute a stable hash of matrix-affecting environment geometry."""
    airports = env.get("airports", [])
    targets = env.get("targets", [])
    sams = env.get("sams", [])
    checkpoints = env.get("checkpoints", [])
    synthetic = env.get("synthetic_starts", {})

    def get_pos(obj):
        if "lat" in obj and "lng" in obj:
            return (round(float(obj["lat"]), 6), round(float(obj["lng"]), 6))
        center = obj.get("center")
        if center and "lat" in center and "lng" in center:
            return (round(float(center["lat"]), 6), round(float(center["lng"]), 6))
        if "x" in obj and "y" in obj:
            return (round(float(obj["x"]), 6), round(float(obj["y"]), 6))
        pos = obj.get("pos") or obj.get("position")
        if pos and len(pos) >= 2:
            return (round(float(pos[0]), 6), round(float(pos[1]), 6))
        return (0, 0)

    key_data = {
        "airports": sorted([(a.get("id", ""), get_pos(a)) for a in airports]),
        "targets": sorted([(t.get("id", ""), get_pos(t)) for t in targets]),
        "sams": sorted(
            [
                (
                    s.get("id", ""),
                    get_pos(s),
                    round(
                        float(s.get("range_km", s.get("range", s.get("radius", 0)))),
                        2,
                    ),
                )
                for s in sams
            ]
        ),
        "checkpoints": sorted(
            [(str(c.get("id", "")), get_pos(c)) for c in checkpoints]
        ),
        "synthetic": sorted(
            [(str(k), get_pos(v)) for k, v in (synthetic or {}).items()]
        ),
    }
    data_str = json.dumps(key_data, sort_keys=True)
    return hashlib.md5(data_str.encode()).hexdigest()


def _build_distance_matrix(
    airports: List[Dict[str, Any]],
    targets: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a simple Euclidean matrix for already-projected waypoints."""
    all_wp = airports + targets
    labels: List[str] = [wp["id"] for wp in all_wp]
    n = len(all_wp)

    matrix: List[List[float]] = [[0.0] * n for _ in range(n)]

    def get_pos(idx: int) -> Tuple[float, float]:
        wp = all_wp[idx]
        return float(wp["x"]), float(wp["y"])

    for i in range(n):
        xi, yi = get_pos(i)
        for j in range(n):
            if i == j:
                matrix[i][j] = 0.0
            else:
                xj, yj = get_pos(j)
                matrix[i][j] = plane_dist((xi, yi), (xj, yj))

    waypoint_details: List[Dict[str, Any]] = []
    for wp in all_wp:
        detail = {
            "id": wp["id"],
            "x": float(wp["x"]),
            "y": float(wp["y"]),
        }
        if str(wp["id"]).startswith("T"):
            detail["priority"] = int(wp.get("priority", 5))
            detail["type"] = wp.get("type", "a")
        waypoint_details.append(detail)

    return {
        "matrix": matrix,
        "labels": labels,
        "waypoints": waypoint_details,
        "excluded_targets": [],
    }


def prepare_distance_matrix(env: Dict[str, Any], buffer: float = 0.0) -> Dict[str, Any]:
    """Pre-calculate a SAM-aware distance matrix for an environment."""
    return calculate_sam_aware_matrix(env, buffer)


def get_current_matrix() -> Optional[Dict[str, Any]]:
    """Get the currently cached distance matrix."""
    return get_cached_matrix()


def clear_cached_matrix():
    """Clear the cached distance matrix."""
    clear_matrix_cache()
