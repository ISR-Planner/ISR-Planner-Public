from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


@dataclass(frozen=True)
class RoutingContext:
    """Explicit routing inputs shared by solve and optimizer paths."""

    env: Dict[str, Any]
    drone_configs: Dict[str, Any]
    distance_matrix: Optional[Dict[str, Any]] = None
    ref_lat: Optional[float] = None
    ref_lng: Optional[float] = None
    env_hash: Optional[str] = None
    routing_model_hash: Optional[str] = None

    @classmethod
    def from_inputs(
        cls,
        *,
        env: Dict[str, Any],
        drone_configs: Optional[Dict[str, Any]] = None,
        distance_matrix: Optional[Dict[str, Any]] = None,
        ref_lat: Optional[float] = None,
        ref_lng: Optional[float] = None,
        env_hash: Optional[str] = None,
        routing_model_hash: Optional[str] = None,
    ) -> "RoutingContext":
        matrix_env_hash = (distance_matrix or {}).get("env_hash")
        matrix_routing_hash = (distance_matrix or {}).get("routing_model_hash")
        return cls(
            env=env,
            drone_configs=drone_configs or {},
            distance_matrix=distance_matrix,
            ref_lat=ref_lat,
            ref_lng=ref_lng,
            env_hash=env_hash or matrix_env_hash,
            routing_model_hash=routing_model_hash or matrix_routing_hash,
        )

    @property
    def has_distance_matrix(self) -> bool:
        return self.distance_matrix is not None

    @property
    def labels(self) -> list[str]:
        return [str(label) for label in (self.distance_matrix or {}).get("labels", [])]

    def with_projection(
        self,
        *,
        env: Dict[str, Any],
        ref_lat: Optional[float],
        ref_lng: Optional[float],
    ) -> "RoutingContext":
        return RoutingContext(
            env=env,
            drone_configs=self.drone_configs,
            distance_matrix=self.distance_matrix,
            ref_lat=ref_lat,
            ref_lng=ref_lng,
            env_hash=self.env_hash,
            routing_model_hash=self.routing_model_hash,
        )


def matrix_from_solution_or_cache(
    solution: Dict[str, Any],
    get_cached_matrix: Callable[[], Optional[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    """Prefer request-provided matrix data and fall back to the cached matrix."""
    explicit_matrix = solution.get("distance_matrix")
    if explicit_matrix:
        return explicit_matrix
    return get_cached_matrix()
