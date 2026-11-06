from dataclasses import dataclass, field
from typing import Any, Dict, List

NODE_FEATURE_NAMES: List[str] = [
    "is_airport",
    "is_target",
    "priority_norm",
    "x_norm",
    "y_norm",
    "target_type_a",
    "target_type_b",
    "target_type_c",
    "target_type_d",
    "target_type_e",
    "is_any_drone_start",
    "is_any_drone_end",
    "target_access_ratio",
    # Replaces the former "sam_risk" feature, which was dead by construction:
    # it was only non-zero strictly inside a SAM ring, but the distance-matrix
    # builder excludes any target inside a ring before the graph is built, so
    # every surviving target scored exactly 0.0.
    "sam_clearance_norm",
    "sam_detour_ratio",
    "min_round_trip_slack_norm",
]

EDGE_FEATURE_NAMES: List[str] = [
    "distance_norm",
    "is_direct",
    "is_blocked",
    "distance_raw_km",
    "feasible_drone_ratio",
    "min_budget_slack_norm",
]

DRONE_FEATURE_NAMES: List[str] = [
    "enabled",
    "fuel_budget_norm",
    "speed_norm",
    "start_node_index_norm",
    "end_node_index_norm",
    "access_type_a",
    "access_type_b",
    "access_type_c",
    "access_type_d",
    "access_type_e",
]


@dataclass
class GraphSample:
    node_ids: List[str]
    node_features: List[List[float]]
    edge_index: List[List[int]]
    edge_features: List[List[float]]
    excluded_targets: List[str]
    drone_ids: List[str] = field(default_factory=list)
    drone_features: List[List[float]] = field(default_factory=list)
    edge_drone_features: List[List[List[float]]] = field(default_factory=list)
    global_features: Dict[str, float] = field(default_factory=dict)
    labels: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_ids": self.node_ids,
            "node_features": self.node_features,
            "node_feature_names": NODE_FEATURE_NAMES,
            "edge_index": self.edge_index,
            "edge_features": self.edge_features,
            "edge_feature_names": EDGE_FEATURE_NAMES,
            "drone_ids": self.drone_ids,
            "drone_features": self.drone_features,
            "drone_feature_names": DRONE_FEATURE_NAMES,
            "edge_drone_features": self.edge_drone_features,
            "edge_drone_feature_names": [
                "reachable_with_budget",
                "budget_slack_norm",
                "target_type_allowed",
            ],
            "global_features": self.global_features,
            "n_nodes": len(self.node_ids),
            "n_edges": len(self.edge_features),
            "n_drones": len(self.drone_ids),
            "excluded_targets": self.excluded_targets,
            "labels": self.labels,
            "meta": self.meta,
        }
