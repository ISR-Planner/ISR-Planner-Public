import copy
import hashlib
from typing import Any, Dict, Iterable, List, Tuple

from server.solver.solver_bridge import (
    _project_env_to_local_km,
    prepare_distance_matrix,
)

from .graph_schema import GraphSample

BLOCKED_SENTINEL = 99999.0
TARGET_TYPES = ("a", "b", "c", "d", "e")


def _drone_budget(cfg: Dict[str, Any]) -> float:
    return float(
        cfg.get("fuel_budget_km", cfg.get("fuel_budget", cfg.get("fuelBudget", 0.0)))
        or 0.0
    )


def _target_access(cfg: Dict[str, Any]) -> Dict[str, bool]:
    raw = cfg.get("target_access") or {}
    if not raw:
        return {t: True for t in TARGET_TYPES}
    return {t: bool(raw.get(t, raw.get(t.upper(), False))) for t in TARGET_TYPES}


def _target_type_allowed(cfg: Dict[str, Any], waypoint: Dict[str, Any]) -> bool:
    if not str(waypoint.get("id", "")).startswith("T"):
        return True
    access = _target_access(cfg)
    return access.get(str(waypoint.get("type", "a")).lower(), True)


def _sam_centers(sams: Iterable[Dict[str, Any]]) -> List[Tuple[float, float, float]]:
    """Extract (x, y, radius) triples from projected SAM records."""
    centers: List[Tuple[float, float, float]] = []
    for sam in sams:
        sx = sam.get("x")
        sy = sam.get("y")
        if sx is None or sy is None:
            pos = sam.get("pos") or sam.get("position") or []
            if len(pos) >= 2:
                sx, sy = pos[0], pos[1]
        if sx is None or sy is None:
            continue
        radius = float(
            sam.get("range_km", sam.get("range", sam.get("radius", 0.0))) or 0.0
        )
        if radius <= 0:
            continue
        centers.append((float(sx), float(sy), radius))
    return centers


def _sam_clearance(
    x: float,
    y: float,
    centers: List[Tuple[float, float, float]],
    scale: float,
) -> float:
    """Normalized clearance from the nearest SAM ring edge, in 0..1.

    0.0 means the node sits on (or inside) a ring edge, 1.0 means it is at
    least `scale` km clear of every ring. Unlike the old `sam_risk`, this
    varies continuously *outside* the rings, which is where every surviving
    target actually lives.
    """
    if not centers:
        return 1.0
    gaps = [
        (((x - sx) ** 2 + (y - sy) ** 2) ** 0.5) - radius for sx, sy, radius in centers
    ]
    nearest_gap = min(gaps)
    if nearest_gap <= 0.0:
        return 0.0
    return min(1.0, nearest_gap / scale) if scale > 0 else 1.0


def _matrix_lookup(
    labels: List[str], matrix: List[List[float]], a: str, b: str
) -> float:
    try:
        return float(matrix[labels.index(a)][labels.index(b)])
    except (ValueError, IndexError):
        return BLOCKED_SENTINEL


def _end_airport(cfg: Dict[str, Any]) -> str:
    end = str(cfg.get("end_airport") or cfg.get("start_airport") or "")
    return str(cfg.get("start_airport") or "") if end == "-" else end


def build_graph_sample(
    env: Dict[str, Any],
    buffer: float = 0.0,
    drone_configs: Dict[str, Any] | None = None,
    labels: Dict[str, Any] | None = None,
    matrix_data: Dict[str, Any] | None = None,
) -> GraphSample:
    """
    Convert an ISR environment into a GNN-ready graph.

    Nodes are airports + non-excluded targets. Edges are fully connected
    directed pairs (i != j). Edge weights come from the SAM-aware distance
    matrix, so the GNN sees obstacle-aware geometry rather than having to
    learn SAM avoidance from raw coordinates.
    """
    # Callers that already hold the matrix should pass it in. Building it is the
    # single most expensive step in the pipeline (O(n^2) visibility-graph
    # pathfinding) and calculate_sam_aware_matrix has no in-memory hit check, so
    # every call recomputes from scratch.
    if matrix_data is None:
        matrix_data = prepare_distance_matrix(env, buffer)
    # SAM risk must be derived from the *projected* env rather than from
    # matrix_data["sams"]. The fresh-matrix path returns projected SAMs, but the
    # Supabase cache-hit path returns "sams": [] (see sam_distance_matrix.py),
    # and an empty list is a present key, so a .get() default never fires. That
    # made the sam_risk feature depend on cache state — correct when cold, zero
    # when warm. Projecting here is cache-independent and is a no-op for envs
    # that already carry x/y.
    projected_env, _, _ = _project_env_to_local_km(env)
    sams_for_risk = projected_env.get("sams", [])
    waypoints = matrix_data["waypoints"]
    matrix = matrix_data["matrix"]
    matrix_labels = matrix_data["labels"]
    paths = matrix_data.get("paths", {})
    excluded = list(matrix_data.get("excluded_targets", []))
    drone_configs = drone_configs or env.get("drone_configs", {}) or {}
    enabled_drones = {
        str(did): cfg
        for did, cfg in drone_configs.items()
        if isinstance(cfg, dict) and cfg.get("enabled", True)
    }

    n = len(waypoints)
    if n == 0:
        return GraphSample(
            node_ids=[],
            node_features=[],
            edge_index=[[], []],
            edge_features=[],
            excluded_targets=excluded,
            drone_ids=[],
            drone_features=[],
            edge_drone_features=[],
            global_features={"n_enabled_drones": float(len(enabled_drones))},
            labels=labels or {},
            meta={"n_airports": 0, "n_targets": 0, "buffer": buffer},
        )

    xs = [float(wp["x"]) for wp in waypoints]
    ys = [float(wp["y"]) for wp in waypoints]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    x_span = (x_max - x_min) or 1.0
    y_span = (y_max - y_min) or 1.0

    # Clearance is normalized against the map diagonal so the feature is
    # scale-free across environments of different physical extent.
    sam_centers = _sam_centers(sams_for_risk)
    clearance_scale = ((x_span**2) + (y_span**2)) ** 0.5 or 1.0

    finite_distances = [
        matrix[i][j]
        for i in range(n)
        for j in range(n)
        if i != j and matrix[i][j] < BLOCKED_SENTINEL
    ]
    d_max = max(finite_distances) if finite_distances else 1.0

    max_priority = max([float(wp.get("priority", 0) or 0) for wp in waypoints] + [1.0])
    max_budget = max([_drone_budget(cfg) for cfg in enabled_drones.values()] + [1.0])
    max_speed = max(
        [
            float(cfg.get("speed_kmh", cfg.get("speed", 0.0)) or 0.0)
            for cfg in enabled_drones.values()
        ]
        + [1.0]
    )

    node_index = {str(wp["id"]): i for i, wp in enumerate(waypoints)}
    start_ids = {
        str(cfg.get("start_airport"))
        for cfg in enabled_drones.values()
        if cfg.get("start_airport")
    }
    end_ids = {
        _end_airport(cfg) for cfg in enabled_drones.values() if cfg.get("start_airport")
    }

    # How much SAM geometry distorts routing through each node: the mean ratio
    # of SAM-aware matrix distance to straight-line distance over all reachable
    # partners. 0.0 means every leg is a straight shot; higher means the node
    # is boxed in and reaching it costs detours. Rescaled from [1, 3] to [0, 1].
    detour_ratios: List[float] = []
    for i in range(n):
        ratios: List[float] = []
        for j in range(n):
            if i == j:
                continue
            d = matrix[i][j]
            if d >= BLOCKED_SENTINEL:
                continue
            straight = ((xs[i] - xs[j]) ** 2 + (ys[i] - ys[j]) ** 2) ** 0.5
            if straight <= 1e-9:
                continue
            ratios.append(d / straight)
        mean_ratio = (sum(ratios) / len(ratios)) if ratios else 1.0
        detour_ratios.append(max(0.0, min(1.0, (mean_ratio - 1.0) / 2.0)))

    node_ids: List[str] = []
    node_features: List[List[float]] = []
    for node_pos, wp in enumerate(waypoints):
        wid = str(wp["id"])
        is_airport = 1.0 if wid.startswith("A") else 0.0
        is_target = 1.0 if wid.startswith("T") else 0.0
        priority = float(wp.get("priority", 0) or 0.0) / max_priority
        x_norm = (float(wp["x"]) - x_min) / x_span
        y_norm = (float(wp["y"]) - y_min) / y_span
        target_type = str(wp.get("type", "")).lower()
        type_features = [1.0 if target_type == t else 0.0 for t in TARGET_TYPES]
        access_count = sum(
            1 for cfg in enabled_drones.values() if _target_type_allowed(cfg, wp)
        )
        access_ratio = (
            access_count / len(enabled_drones)
            if enabled_drones and is_target
            else 1.0 if is_airport else 0.0
        )
        slacks: List[float] = []
        for cfg in enabled_drones.values():
            start = str(cfg.get("start_airport") or "")
            end = _end_airport(cfg)
            budget = _drone_budget(cfg)
            outbound = _matrix_lookup(matrix_labels, matrix, start, wid)
            inbound = _matrix_lookup(matrix_labels, matrix, wid, end)
            if outbound < BLOCKED_SENTINEL and inbound < BLOCKED_SENTINEL:
                slacks.append((budget - outbound - inbound) / max_budget)
        min_slack = min(slacks) if slacks else 0.0
        node_ids.append(wid)
        node_features.append(
            [
                is_airport,
                is_target,
                priority,
                x_norm,
                y_norm,
                *type_features,
                1.0 if wid in start_ids else 0.0,
                1.0 if wid in end_ids else 0.0,
                float(access_ratio),
                _sam_clearance(
                    float(wp["x"]), float(wp["y"]), sam_centers, clearance_scale
                ),
                detour_ratios[node_pos],
                float(max(-1.0, min(1.0, min_slack))),
            ]
        )

    drone_ids: List[str] = []
    drone_features: List[List[float]] = []
    for did, cfg in sorted(enabled_drones.items(), key=lambda item: item[0]):
        start_idx = node_index.get(str(cfg.get("start_airport") or ""), -1)
        end_idx = node_index.get(_end_airport(cfg), start_idx)
        access = _target_access(cfg)
        drone_ids.append(did)
        drone_features.append(
            [
                1.0 if cfg.get("enabled", True) else 0.0,
                _drone_budget(cfg) / max_budget,
                float(cfg.get("speed_kmh", cfg.get("speed", 0.0)) or 0.0) / max_speed,
                (start_idx / max(1, n - 1)) if start_idx >= 0 else -1.0,
                (end_idx / max(1, n - 1)) if end_idx >= 0 else -1.0,
                *[1.0 if access.get(t, True) else 0.0 for t in TARGET_TYPES],
            ]
        )

    src: List[int] = []
    dst: List[int] = []
    edge_features: List[List[float]] = []
    edge_drone_features: List[List[List[float]]] = []
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            d = matrix[i][j]
            blocked = d >= BLOCKED_SENTINEL
            distance_norm = 1.0 if blocked else (d / d_max)
            path_key = f"{matrix_labels[i]}->{matrix_labels[j]}"
            is_direct = 0.0 if path_key in paths else 1.0
            per_drone_edge: List[List[float]] = []
            feasible_count = 0
            slack_values: List[float] = []
            for cfg in enabled_drones.values():
                budget = _drone_budget(cfg)
                allowed = _target_type_allowed(cfg, waypoints[j])
                reachable = (not blocked) and allowed and d <= budget
                if reachable:
                    feasible_count += 1
                slack = (budget - d) / max_budget if not blocked else -1.0
                slack = max(-1.0, min(1.0, slack))
                slack_values.append(slack)
                per_drone_edge.append(
                    [
                        1.0 if reachable else 0.0,
                        float(slack),
                        1.0 if allowed else 0.0,
                    ]
                )
            feasible_ratio = (
                feasible_count / len(enabled_drones) if enabled_drones else 0.0
            )
            min_budget_slack = min(slack_values) if slack_values else 0.0
            src.append(i)
            dst.append(j)
            edge_features.append(
                [
                    float(distance_norm),
                    float(is_direct),
                    1.0 if blocked else 0.0,
                    float(d if not blocked else BLOCKED_SENTINEL),
                    float(feasible_ratio),
                    float(min_budget_slack),
                ]
            )
            edge_drone_features.append(per_drone_edge)

    n_airports = sum(1 for nid in node_ids if nid.startswith("A"))
    n_targets = sum(1 for nid in node_ids if nid.startswith("T"))

    return GraphSample(
        node_ids=node_ids,
        node_features=node_features,
        edge_index=[src, dst],
        edge_features=edge_features,
        excluded_targets=excluded,
        drone_ids=drone_ids,
        drone_features=drone_features,
        edge_drone_features=edge_drone_features,
        global_features={
            "n_enabled_drones": float(len(enabled_drones)),
            "n_targets": float(n_targets),
            "n_airports": float(n_airports),
            "max_priority": float(max_priority),
            "max_budget_km": float(max_budget),
        },
        labels=labels or {},
        meta={
            "n_airports": n_airports,
            "n_targets": n_targets,
            "distance_max_raw": d_max,
            "blocked_sentinel": BLOCKED_SENTINEL,
            "buffer": buffer,
            "model_contract": "gnn_supervised_routes_v1",
        },
    )


def build_dataset_record(
    env: Dict[str, Any],
    drone_configs: Dict[str, Any],
    teacher_result: Dict[str, Any],
    *,
    sample_id: str | None = None,
    buffer: float = 0.0,
) -> Dict[str, Any]:
    """Build one supervised GNN training/eval example.

    The label format is deliberately route-level, not tensor-framework-specific,
    so PyTorch Geometric/DGL/custom loaders can consume the same JSONL later.
    """
    sample = build_graph_sample(env, buffer=buffer, drone_configs=drone_configs)
    sid = (
        sample_id
        or hashlib.sha256(
            repr((env, drone_configs, teacher_result.get("sequences", {}))).encode()
        ).hexdigest()[:16]
    )
    routes = teacher_result.get("routes") or {}
    target_assignment: Dict[str, str] = {}
    route_sequences: Dict[str, List[str]] = {}
    for did, route_data in routes.items():
        route = (
            route_data.get("route", [])
            if isinstance(route_data, dict)
            else route_data if isinstance(route_data, list) else []
        )
        route_sequences[str(did)] = [str(wp) for wp in route]
        for wp in route:
            if str(wp).startswith("T"):
                target_assignment[str(wp)] = str(did)
    graph_dict = sample.to_dict()
    graph_dict["labels"] = {
        "target_assignment": target_assignment,
        "route_sequences": route_sequences,
        "total_points": teacher_result.get("total_points"),
        "total_fuel_km": teacher_result.get("total_fuel_km"),
    }
    return {
        "schema_version": "gnn_dataset_v1",
        "sample_id": sid,
        "graph": graph_dict,
        "env": copy.deepcopy(env),
        "drone_configs": copy.deepcopy(drone_configs),
        "teacher": {
            "routes": routes,
            "sequences": teacher_result.get("sequences", {}),
            "allocations": teacher_result.get("allocations", {}),
        },
    }


def split_dataset_records(
    records: List[Dict[str, Any]],
    train_ratio: float = 0.8,
    val_ratio: float = 0.1,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    train_end = int(len(records) * train_ratio)
    val_end = train_end + int(len(records) * val_ratio)
    return records[:train_end], records[train_end:val_end], records[val_end:]
