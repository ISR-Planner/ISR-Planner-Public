"""Environment parsing helpers for solver orchestration."""

from typing import Any, Dict, List, Tuple


def _parse_env_for_solver(
    env: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Pull airports, targets, and SAMs out of the web environment JSON.

    Also handles checkpoints and synthetic starts for segmented replanning by
    adding them to the airport list as valid start nodes.
    """
    airports: List[Dict[str, Any]] = []
    for a in env.get("airports", []):
        airport_entry = {
            "id": str(a["id"]),
            "x": float(a["x"]),
            "y": float(a["y"]),
            "runway": str(a.get("runway", "L")).upper(),
        }
        if a.get("is_synthetic", False):
            airport_entry["is_synthetic"] = True
        if a.get("type") == "tanker" or a.get("is_tanker"):
            airport_entry["is_tanker"] = True
        airports.append(airport_entry)

    for checkpoint in env.get("checkpoints", []):
        airports.append(
            {
                "id": str(checkpoint["id"]),
                "x": float(checkpoint["x"]),
                "y": float(checkpoint["y"]),
                "is_checkpoint": True,
            }
        )
        print(
            f"[parse] Added checkpoint: {checkpoint['id']} "
            f"at ({checkpoint['x']:.1f}, {checkpoint['y']:.1f})",
            flush=True,
        )

    for node_id, node_data in env.get("synthetic_starts", {}).items():
        airports.append(
            {
                "id": str(node_id),
                "x": float(node_data["x"]),
                "y": float(node_data["y"]),
                "is_synthetic": True,
            }
        )
        print(
            f"[parse] Added synthetic start: {node_id} "
            f"at ({node_data['x']:.1f}, {node_data['y']:.1f})",
            flush=True,
        )

    visited_target_ids = set(str(tid) for tid in env.get("visited_targets", []))

    targets: List[Dict[str, Any]] = []
    for t in env.get("targets", []):
        target_id = str(t["id"])
        if target_id in visited_target_ids:
            print(
                f"[parse] Skipping visited target: {target_id} " "(already completed)",
                flush=True,
            )
            continue

        targets.append(
            {
                "id": target_id,
                "x": float(t["x"]),
                "y": float(t["y"]),
                "priority": int(t.get("priority", 5)),
                "type": t.get("type", "a"),
                "assigned_drone": t.get("assigned_drone"),
            }
        )

    sams: List[Dict[str, Any]] = []
    for s in env.get("sams", []):
        pos = s.get("pos") or s.get("position")
        if pos is None and "x" in s and "y" in s:
            pos = [float(s["x"]), float(s["y"])]
        sams.append(
            {
                "pos": pos,
                "range": float(s.get("range", s.get("radius", 15.0))),
                "id": s.get("id", "SAM"),
                "avoidance_polygon": s.get("avoidance_polygon"),
                "x": pos[0] if pos and len(pos) > 0 else None,
                "y": pos[1] if pos and len(pos) > 1 else None,
            }
        )

    return airports, targets, sams
