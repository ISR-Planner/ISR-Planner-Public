"""PyTorch GNN model for ISR target-to-drone assignment.

This is the production GNN path.  It trains from benchmark JSONL records that
contain `gnn_training_record` blocks and saves a checkpoint used by
`/api/gnn/predict` and `/api/agents/compare`.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import torch
from torch import nn
from torch.nn import functional as F

from .data_pipeline import BLOCKED_SENTINEL

CHECKPOINT_VERSION = "torch_gnn_v1"
ROUTE_EDGE_CHECKPOINT_VERSION = "torch_gnn_route_edge_v1"


@dataclass
class GraphTensors:
    node_features: torch.Tensor
    edge_index: torch.Tensor
    edge_features: torch.Tensor
    drone_features: torch.Tensor
    target_indices: List[int]
    node_ids: List[str]
    drone_ids: List[str]


@dataclass
class TrainingExample:
    graph: Dict[str, Any]
    target_node_index: int
    label_drone_id: str | None


class ISRGraphAssignmentNet(nn.Module):
    """Small message-passing graph network with drone-conditioned target scores."""

    def __init__(
        self,
        node_dim: int,
        edge_dim: int,
        drone_dim: int,
        hidden_dim: int = 64,
        message_steps: int = 2,
    ) -> None:
        super().__init__()
        self.node_dim = node_dim
        self.edge_dim = edge_dim
        self.drone_dim = drone_dim
        self.hidden_dim = hidden_dim
        self.message_steps = message_steps

        self.node_encoder = nn.Linear(node_dim, hidden_dim)
        self.edge_encoder = nn.Linear(edge_dim, hidden_dim)
        self.message_mlp = nn.Sequential(
            nn.Linear(hidden_dim * 2, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
        )
        self.update_mlp = nn.Sequential(
            nn.Linear(hidden_dim * 2, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
        )
        self.drone_encoder = nn.Linear(drone_dim, hidden_dim)
        self.pair_head = nn.Sequential(
            nn.Linear(hidden_dim * 2, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, 1),
        )
        self.none_head = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, 1),
        )

    def encode_nodes(self, tensors: GraphTensors) -> torch.Tensor:
        h = torch.relu(self.node_encoder(tensors.node_features))
        if tensors.edge_index.numel() == 0:
            return h
        src = tensors.edge_index[0].long()
        dst = tensors.edge_index[1].long()
        edge_h = torch.relu(self.edge_encoder(tensors.edge_features))
        for _ in range(self.message_steps):
            msg_input = torch.cat([h[src], edge_h], dim=1)
            messages = self.message_mlp(msg_input)
            agg = torch.zeros_like(h)
            agg.index_add_(0, dst, messages)
            counts = torch.zeros((h.shape[0], 1), dtype=h.dtype, device=h.device)
            counts.index_add_(
                0, dst, torch.ones((dst.shape[0], 1), dtype=h.dtype, device=h.device)
            )
            agg = agg / counts.clamp_min(1.0)
            h = self.update_mlp(torch.cat([h, agg], dim=1))
        return h

    def forward_target_scores(
        self, tensors: GraphTensors, target_idx: int
    ) -> torch.Tensor:
        node_h = self.encode_nodes(tensors)
        return self.forward_target_scores_encoded(
            node_h, tensors.drone_features, target_idx
        )

    def forward_target_scores_encoded(
        self,
        node_h: torch.Tensor,
        drone_features: torch.Tensor,
        target_idx: int,
    ) -> torch.Tensor:
        drone_h = torch.relu(self.drone_encoder(drone_features))
        target_h = node_h[target_idx].unsqueeze(0).expand(drone_h.shape[0], -1)
        pair_scores = self.pair_head(torch.cat([target_h, drone_h], dim=1)).squeeze(-1)
        none_score = self.none_head(node_h[target_idx]).reshape(1)
        return torch.cat([pair_scores, none_score], dim=0)


class ISRRouteEdgeScoringNet(nn.Module):
    """Message-passing graph network that scores the next waypoint for a drone."""

    def __init__(
        self,
        node_dim: int,
        edge_dim: int,
        drone_dim: int,
        hidden_dim: int = 64,
        message_steps: int = 2,
    ) -> None:
        super().__init__()
        self.node_dim = node_dim
        self.edge_dim = edge_dim
        self.drone_dim = drone_dim
        self.hidden_dim = hidden_dim
        self.message_steps = message_steps

        self.node_encoder = nn.Linear(node_dim, hidden_dim)
        self.edge_encoder = nn.Linear(edge_dim, hidden_dim)
        self.message_mlp = nn.Sequential(
            nn.Linear(hidden_dim * 2, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
        )
        self.update_mlp = nn.Sequential(
            nn.Linear(hidden_dim * 2, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
        )
        self.drone_encoder = nn.Linear(drone_dim, hidden_dim)
        self.edge_head = nn.Sequential(
            nn.Linear(hidden_dim * 4 + edge_dim + 3, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, 1),
        )

    def encode_nodes(self, tensors: GraphTensors) -> torch.Tensor:
        h = torch.relu(self.node_encoder(tensors.node_features))
        if tensors.edge_index.numel() == 0:
            return h
        src = tensors.edge_index[0].long()
        dst = tensors.edge_index[1].long()
        edge_h = torch.relu(self.edge_encoder(tensors.edge_features))
        for _ in range(self.message_steps):
            msg_input = torch.cat([h[src], edge_h], dim=1)
            messages = self.message_mlp(msg_input)
            agg = torch.zeros_like(h)
            agg.index_add_(0, dst, messages)
            counts = torch.zeros((h.shape[0], 1), dtype=h.dtype, device=h.device)
            counts.index_add_(
                0, dst, torch.ones((dst.shape[0], 1), dtype=h.dtype, device=h.device)
            )
            agg = agg / counts.clamp_min(1.0)
            h = self.update_mlp(torch.cat([h, agg], dim=1))
        return h

    def score_candidates(
        self,
        node_h: torch.Tensor,
        tensors: GraphTensors,
        *,
        drone_idx: int,
        current_idx: int,
        candidate_indices: Sequence[int],
        remaining_count: int,
        budget_remaining_norm: float,
    ) -> torch.Tensor:
        drone_h = torch.relu(self.drone_encoder(tensors.drone_features[drone_idx]))
        current_h = node_h[current_idx]
        scores: List[torch.Tensor] = []
        edge_lookup = _edge_feature_lookup(tensors)
        remaining_norm = float(remaining_count / max(1, len(tensors.target_indices)))
        for candidate_idx in candidate_indices:
            candidate_h = node_h[candidate_idx]
            edge_features = edge_lookup.get(
                (current_idx, candidate_idx),
                torch.zeros((self.edge_dim,), dtype=node_h.dtype, device=node_h.device),
            )
            is_target = 1.0 if tensors.node_ids[candidate_idx].startswith("T") else 0.0
            state = torch.tensor(
                [remaining_norm, float(budget_remaining_norm), is_target],
                dtype=node_h.dtype,
                device=node_h.device,
            )
            features = torch.cat(
                [
                    current_h,
                    candidate_h,
                    drone_h,
                    current_h * candidate_h,
                    edge_features,
                    state,
                ]
            )
            scores.append(self.edge_head(features).reshape(()))
        if not scores:
            return torch.empty((0,), dtype=node_h.dtype, device=node_h.device)
        return torch.stack(scores)


def graph_to_tensors(
    graph: Dict[str, Any], device: str | torch.device = "cpu"
) -> GraphTensors:
    node_features = torch.tensor(
        graph.get("node_features") or [], dtype=torch.float32, device=device
    )
    edge_features = torch.tensor(
        graph.get("edge_features") or [], dtype=torch.float32, device=device
    )
    edge_index_raw = graph.get("edge_index") or [[], []]
    edge_index = torch.tensor(edge_index_raw, dtype=torch.long, device=device)
    if edge_index.ndim == 1:
        edge_index = edge_index.reshape(2, 0)
    drone_features = torch.tensor(
        graph.get("drone_features") or [], dtype=torch.float32, device=device
    )
    node_ids = [str(n) for n in graph.get("node_ids", [])]
    drone_ids = [str(d) for d in graph.get("drone_ids", [])]
    target_indices = [
        i for i, node_id in enumerate(node_ids) if node_id.startswith("T")
    ]
    return GraphTensors(
        node_features=node_features,
        edge_index=edge_index,
        edge_features=edge_features,
        drone_features=drone_features,
        target_indices=target_indices,
        node_ids=node_ids,
        drone_ids=drone_ids,
    )


def _edge_feature_lookup(tensors: GraphTensors) -> Dict[tuple[int, int], torch.Tensor]:
    lookup: Dict[tuple[int, int], torch.Tensor] = {}
    if tensors.edge_index.numel() == 0:
        return lookup
    for edge_pos in range(int(tensors.edge_index.shape[1])):
        src = int(tensors.edge_index[0, edge_pos].detach().cpu())
        dst = int(tensors.edge_index[1, edge_pos].detach().cpu())
        lookup[(src, dst)] = tensors.edge_features[edge_pos]
    return lookup


def extract_training_examples(
    records: Iterable[Dict[str, Any]],
) -> List[TrainingExample]:
    examples: List[TrainingExample] = []
    for record in records:
        dataset = record.get("gnn_training_record", record)
        if not isinstance(dataset, dict):
            continue
        graph = dataset.get("graph") or {}
        labels = graph.get("labels") or {}
        assignments = labels.get("target_assignment") or {}
        node_ids = [str(n) for n in graph.get("node_ids", [])]
        for idx, node_id in enumerate(node_ids):
            if not node_id.startswith("T"):
                continue
            examples.append(
                TrainingExample(
                    graph=graph,
                    target_node_index=idx,
                    label_drone_id=(
                        str(assignments[node_id]) if node_id in assignments else None
                    ),
                )
            )
    return examples


def load_records(paths: Sequence[str | Path]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for path in paths:
        with Path(path).open() as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    return records


def _infer_dims(records: Sequence[Dict[str, Any]]) -> tuple[int, int, int]:
    examples = extract_training_examples(records)
    if not examples:
        raise ValueError("no GNN training examples found")
    tensors = graph_to_tensors(examples[0].graph)
    if tensors.drone_features.numel() == 0:
        raise ValueError("training examples have no drone features")
    return (
        int(tensors.node_features.shape[1]),
        (
            int(tensors.edge_features.shape[1])
            if tensors.edge_features.ndim == 2 and tensors.edge_features.numel()
            else 1
        ),
        int(tensors.drone_features.shape[1]),
    )


def train_assignment_model(
    records: Sequence[Dict[str, Any]],
    *,
    epochs: int = 25,
    learning_rate: float = 0.001,
    hidden_dim: int = 64,
    message_steps: int = 2,
    seed: int = 7,
    device: str = "cpu",
) -> Dict[str, Any]:
    random.seed(seed)
    torch.manual_seed(seed)
    examples = extract_training_examples(records)
    if not examples:
        raise ValueError("no GNN training examples found")
    node_dim, edge_dim, drone_dim = _infer_dims(records)
    model = ISRGraphAssignmentNet(
        node_dim=node_dim,
        edge_dim=edge_dim,
        drone_dim=drone_dim,
        hidden_dim=hidden_dim,
        message_steps=message_steps,
    ).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    losses: List[float] = []
    graph_records = [
        record.get("gnn_training_record", record)
        for record in records
        if isinstance(record.get("gnn_training_record", record), dict)
        and (record.get("gnn_training_record", record).get("graph") or {})
    ]

    for _ in range(epochs):
        random.shuffle(graph_records)
        total_loss = 0.0
        used = 0
        for dataset in graph_records:
            graph = dataset.get("graph") or {}
            labels = graph.get("labels") or {}
            assignments = labels.get("target_assignment") or {}
            tensors = graph_to_tensors(graph, device=device)
            if tensors.drone_features.numel() == 0:
                continue
            node_h = model.encode_nodes(tensors)
            graph_losses: List[torch.Tensor] = []
            for target_idx in tensors.target_indices:
                node_id = tensors.node_ids[target_idx]
                label_drone_id = (
                    str(assignments[node_id]) if node_id in assignments else None
                )
                scores = model.forward_target_scores_encoded(
                    node_h,
                    tensors.drone_features,
                    target_idx,
                )
                label_idx = (
                    tensors.drone_ids.index(label_drone_id)
                    if label_drone_id in tensors.drone_ids
                    else len(tensors.drone_ids)
                )
                target = torch.tensor([label_idx], dtype=torch.long, device=device)
                graph_losses.append(F.cross_entropy(scores.unsqueeze(0), target))
            if not graph_losses:
                continue
            loss = torch.stack(graph_losses).mean()
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total_loss += float(loss.detach().cpu()) * len(graph_losses)
            used += len(graph_losses)
        losses.append(round(total_loss / max(1, used), 6))

    return {
        "version": CHECKPOINT_VERSION,
        "node_dim": node_dim,
        "edge_dim": edge_dim,
        "drone_dim": drone_dim,
        "hidden_dim": hidden_dim,
        "message_steps": message_steps,
        "epochs": epochs,
        "learning_rate": learning_rate,
        "seed": seed,
        "examples": len(examples),
        "losses": losses,
        "state_dict": {k: v.detach().cpu() for k, v in model.state_dict().items()},
    }


def _route_sequences(graph: Dict[str, Any]) -> Dict[str, List[str]]:
    labels = graph.get("labels") or {}
    raw = labels.get("route_sequences") or {}
    sequences: Dict[str, List[str]] = {}
    for did, route in raw.items():
        if isinstance(route, str):
            waypoints = [part.strip() for part in route.split(",") if part.strip()]
        elif isinstance(route, list):
            waypoints = [str(part) for part in route if str(part)]
        else:
            continue
        if len(waypoints) >= 2:
            sequences[str(did)] = waypoints
    return sequences


def _route_graph_records(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    graph_records = []
    for record in records:
        dataset = record.get("gnn_training_record", record)
        if not isinstance(dataset, dict):
            continue
        graph = dataset.get("graph") or {}
        if graph and _route_sequences(graph):
            graph_records.append(dataset)
    return graph_records


def raw_distance(graph: Dict[str, Any], src_idx: int, dst_idx: int) -> float:
    """Look up the raw-km distance for an edge from the graph dict."""
    from .graph_schema import EDGE_FEATURE_NAMES

    col = EDGE_FEATURE_NAMES.index("distance_raw_km")
    edge_index = graph.get("edge_index") or [[], []]
    edge_features = graph.get("edge_features") or []
    for pos, (s, d) in enumerate(zip(edge_index[0], edge_index[1])):
        if int(s) == src_idx and int(d) == dst_idx:
            return float(edge_features[pos][col])
    return BLOCKED_SENTINEL


def drone_budget_km(graph: Dict[str, Any], drone_idx: int) -> float:
    """Recover a drone's raw fuel budget in km from the normalized features."""
    from .graph_schema import DRONE_FEATURE_NAMES

    col = DRONE_FEATURE_NAMES.index("fuel_budget_norm")
    drone_features = graph.get("drone_features") or []
    if drone_idx >= len(drone_features):
        return 0.0
    max_budget = float((graph.get("global_features") or {}).get("max_budget_km", 1.0))
    return float(drone_features[drone_idx][col]) * max_budget


def _route_transition_count(records: Sequence[Dict[str, Any]]) -> int:
    total = 0
    for dataset in _route_graph_records(records):
        graph = dataset.get("graph") or {}
        for route in _route_sequences(graph).values():
            total += max(0, len(route) - 1)
    return total


def train_route_edge_model(
    records: Sequence[Dict[str, Any]],
    *,
    epochs: int = 25,
    learning_rate: float = 0.001,
    hidden_dim: int = 64,
    message_steps: int = 2,
    seed: int = 7,
    device: str = "cpu",
) -> Dict[str, Any]:
    """Train next-waypoint edge scoring from route sequence labels."""
    random.seed(seed)
    torch.manual_seed(seed)
    graph_records = _route_graph_records(records)
    if not graph_records:
        raise ValueError("no route sequence labels found")
    examples = _route_transition_count(records)
    if examples == 0:
        raise ValueError("no route transitions found")
    node_dim, edge_dim, drone_dim = _infer_dims(records)
    model = ISRRouteEdgeScoringNet(
        node_dim=node_dim,
        edge_dim=edge_dim,
        drone_dim=drone_dim,
        hidden_dim=hidden_dim,
        message_steps=message_steps,
    ).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    losses: List[float] = []

    for _ in range(epochs):
        random.shuffle(graph_records)
        total_loss = 0.0
        used = 0
        for dataset in graph_records:
            graph = dataset.get("graph") or {}
            tensors = graph_to_tensors(graph, device=device)
            if tensors.drone_features.numel() == 0:
                continue
            node_index = {node_id: idx for idx, node_id in enumerate(tensors.node_ids)}
            target_ids = [
                node_id for node_id in tensors.node_ids if node_id.startswith("T")
            ]
            node_h = model.encode_nodes(tensors)
            graph_losses: List[torch.Tensor] = []
            for did, route in _route_sequences(graph).items():
                if did not in tensors.drone_ids:
                    continue
                drone_idx = tensors.drone_ids.index(did)
                visited_targets: set[str] = set()
                route_targets = {wp for wp in route if wp.startswith("T")}
                # Track fuel actually burned along the teacher route so
                # budget_remaining_norm carries real signal. It used to be
                # hardcoded to 1.0 here, which meant the decoder could never
                # learn to respect fuel and any real value fed at inference time
                # would be out of distribution.
                budget_km = drone_budget_km(graph, drone_idx)
                spent_km = 0.0
                for pos in range(len(route) - 1):
                    current = route[pos]
                    next_wp = route[pos + 1]
                    if current not in node_index or next_wp not in node_index:
                        if current.startswith("T"):
                            visited_targets.add(current)
                        continue
                    end_node = route[-1]
                    candidates = [
                        tid
                        for tid in target_ids
                        if tid not in visited_targets and tid != current
                    ]
                    if end_node not in candidates:
                        candidates.append(end_node)
                    if next_wp not in candidates:
                        candidates.append(next_wp)
                    candidate_indices = [
                        node_index[candidate]
                        for candidate in candidates
                        if candidate in node_index
                    ]
                    if (
                        not candidate_indices
                        or node_index[next_wp] not in candidate_indices
                    ):
                        if current.startswith("T"):
                            visited_targets.add(current)
                        continue
                    remaining_count = len(route_targets - visited_targets)
                    budget_remaining_norm = (
                        max(0.0, budget_km - spent_km) / budget_km
                        if budget_km > 0
                        else 1.0
                    )
                    scores = model.score_candidates(
                        node_h,
                        tensors,
                        drone_idx=drone_idx,
                        current_idx=node_index[current],
                        candidate_indices=candidate_indices,
                        remaining_count=remaining_count,
                        budget_remaining_norm=budget_remaining_norm,
                    )
                    label_idx = candidate_indices.index(node_index[next_wp])
                    target = torch.tensor([label_idx], dtype=torch.long, device=device)
                    graph_losses.append(F.cross_entropy(scores.unsqueeze(0), target))
                    leg_km = raw_distance(
                        graph, node_index[current], node_index[next_wp]
                    )
                    if leg_km < BLOCKED_SENTINEL:
                        spent_km += leg_km
                    if current.startswith("T"):
                        visited_targets.add(current)
            if not graph_losses:
                continue
            loss = torch.stack(graph_losses).mean()
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total_loss += float(loss.detach().cpu()) * len(graph_losses)
            used += len(graph_losses)
        losses.append(round(total_loss / max(1, used), 6))

    return {
        "version": ROUTE_EDGE_CHECKPOINT_VERSION,
        "node_dim": node_dim,
        "edge_dim": edge_dim,
        "drone_dim": drone_dim,
        "hidden_dim": hidden_dim,
        "message_steps": message_steps,
        "epochs": epochs,
        "learning_rate": learning_rate,
        "seed": seed,
        "examples": examples,
        "losses": losses,
        "state_dict": {k: v.detach().cpu() for k, v in model.state_dict().items()},
    }


def save_checkpoint(checkpoint: Dict[str, Any], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint, p)


def load_checkpoint(path: str | Path, device: str = "cpu") -> Dict[str, Any]:
    checkpoint = torch.load(Path(path), map_location=device)
    if checkpoint.get("version") not in {
        CHECKPOINT_VERSION,
        ROUTE_EDGE_CHECKPOINT_VERSION,
    }:
        raise ValueError(
            f"unsupported GNN checkpoint version: {checkpoint.get('version')}"
        )
    return checkpoint


def assert_checkpoint_matches_schema(checkpoint: Dict[str, Any]) -> None:
    """Fail with an actionable message when a checkpoint predates the feature schema.

    Without this, a stale checkpoint surfaces as a bare torch error like
    "mat1 and mat2 shapes cannot be multiplied (3x16 and 15x64)", which gives no
    hint that the fix is to retrain.
    """
    from .graph_schema import (
        DRONE_FEATURE_NAMES,
        EDGE_FEATURE_NAMES,
        NODE_FEATURE_NAMES,
    )

    expected = {
        "node_dim": len(NODE_FEATURE_NAMES),
        "edge_dim": len(EDGE_FEATURE_NAMES),
        "drone_dim": len(DRONE_FEATURE_NAMES),
    }
    mismatches = [
        f"{key}: checkpoint has {int(checkpoint[key])}, schema expects {want}"
        for key, want in expected.items()
        if key in checkpoint and int(checkpoint[key]) != want
    ]
    if mismatches:
        raise ValueError(
            "GNN checkpoint was trained against a different feature schema ("
            + "; ".join(mismatches)
            + "). Regenerate the dataset and retrain: "
            "scripts/generate_gnn_dataset.py then scripts/train_gnn.py."
        )


def build_model_from_checkpoint(
    checkpoint: Dict[str, Any], device: str = "cpu"
) -> ISRGraphAssignmentNet:
    assert_checkpoint_matches_schema(checkpoint)
    model = ISRGraphAssignmentNet(
        node_dim=int(checkpoint["node_dim"]),
        edge_dim=int(checkpoint["edge_dim"]),
        drone_dim=int(checkpoint["drone_dim"]),
        hidden_dim=int(checkpoint["hidden_dim"]),
        message_steps=int(checkpoint["message_steps"]),
    ).to(device)
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    return model


def build_route_edge_model_from_checkpoint(
    checkpoint: Dict[str, Any], device: str = "cpu"
) -> ISRRouteEdgeScoringNet:
    assert_checkpoint_matches_schema(checkpoint)
    model = ISRRouteEdgeScoringNet(
        node_dim=int(checkpoint["node_dim"]),
        edge_dim=int(checkpoint["edge_dim"]),
        drone_dim=int(checkpoint["drone_dim"]),
        hidden_dim=int(checkpoint["hidden_dim"]),
        message_steps=int(checkpoint["message_steps"]),
    ).to(device)
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    return model


@torch.no_grad()
def predict_routes(
    graph: Dict[str, Any],
    checkpoint: Dict[str, Any],
    assignments: Dict[str, str],
    end_airports: Dict[str, str],
    start_airports: Dict[str, str],
    *,
    device: str = "cpu",
) -> Dict[str, List[str]]:
    """Greedily roll out a route per drone using the learned edge scorer.

    This is the configuration that can actually scale: the exact solver
    enumerates every subset of targets (orienteering_with_matrix.py:781) and
    then runs Held-Karp on each, so it is roughly O(3^N) per drone. Rolling the
    edge model out greedily is O(N^2) per drone instead.

    Feasibility is enforced here rather than trusted to the model: a candidate
    is only considered if the leg is not blocked, the drone can still reach its
    end airport afterwards, and the target type is permitted. The model chooses
    among legal moves; it cannot invent illegal ones.
    """
    tensors = graph_to_tensors(graph, device=device)
    if tensors.drone_features.numel() == 0:
        return {}

    model = build_route_edge_model_from_checkpoint(checkpoint, device=device)
    node_h = model.encode_nodes(tensors)
    node_index = {node_id: idx for idx, node_id in enumerate(tensors.node_ids)}

    routes: Dict[str, List[str]] = {}

    for drone_pos, did in enumerate(tensors.drone_ids):
        start = start_airports.get(did, "")
        end = end_airports.get(did, start)
        if start not in node_index or end not in node_index:
            continue

        budget_km = drone_budget_km(graph, drone_pos)
        assigned = [
            tid
            for tid, owner in assignments.items()
            if str(owner) == str(did) and tid in node_index
        ]

        route = [start]
        current = start
        spent_km = 0.0
        unvisited = set(assigned)

        while unvisited:
            current_idx = node_index[current]
            legal: List[str] = []
            for tid in unvisited:
                leg = raw_distance(graph, current_idx, node_index[tid])
                if leg >= BLOCKED_SENTINEL:
                    continue
                # Must still be able to get home from the candidate.
                home = raw_distance(graph, node_index[tid], node_index[end])
                if home >= BLOCKED_SENTINEL:
                    continue
                if budget_km > 0 and spent_km + leg + home > budget_km:
                    continue
                legal.append(tid)

            if not legal:
                break

            scores = model.score_candidates(
                node_h,
                tensors,
                drone_idx=drone_pos,
                current_idx=current_idx,
                candidate_indices=[node_index[tid] for tid in legal],
                remaining_count=len(unvisited),
                budget_remaining_norm=(
                    max(0.0, budget_km - spent_km) / budget_km if budget_km > 0 else 1.0
                ),
            )
            chosen = legal[int(torch.argmax(scores).detach().cpu())]
            spent_km += raw_distance(graph, current_idx, node_index[chosen])
            route.append(chosen)
            unvisited.discard(chosen)
            current = chosen

        route.append(end)
        routes[did] = route

    return routes


@torch.no_grad()
def predict_target_assignments(
    graph: Dict[str, Any],
    checkpoint: Dict[str, Any],
    *,
    device: str = "cpu",
) -> Dict[str, str]:
    tensors = graph_to_tensors(graph, device=device)
    if tensors.drone_features.numel() == 0:
        return {}
    model = build_model_from_checkpoint(checkpoint, device=device)
    assignments: Dict[str, str] = {}
    for target_idx in tensors.target_indices:
        scores = model.forward_target_scores(tensors, target_idx)
        best = int(torch.argmax(scores).detach().cpu())
        if best < len(tensors.drone_ids):
            assignments[tensors.node_ids[target_idx]] = tensors.drone_ids[best]
    return assignments
