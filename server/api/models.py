from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class SolveRequest(BaseModel):
    env: Dict[str, Any]
    drone_configs: Dict[str, Any]
    allocation_strategy: str = "geographic"
    mission_id: Optional[str] = None
    time_sync: Optional[Dict[str, Any]] = None


class SolveResponse(BaseModel):
    success: bool
    routes: Dict[str, Any]
    sequences: Dict[str, str]
    wrapped_polygons: Optional[List[List[List[float]]]] = None
    allocations: Optional[Dict[str, List[str]]] = None
    distance_matrix: Optional[Dict[str, Any]] = None
    mission_id: Optional[str] = None
    env_hash: Optional[str] = None
    continuity: Optional[bool] = None
    errors: Optional[List[str]] = None
    pipeline_summary: Optional[Dict[str, Any]] = None
    time_sync: Optional[Dict[str, Any]] = None
    warnings: Optional[List[str]] = None


class AgentChatRequest(BaseModel):
    message: str
    env: Dict[str, Any]
    sequences: Optional[Dict[str, str]] = None
    drone_configs: Optional[Dict[str, Any]] = None
    mission_id: Optional[str] = None
    existing_solution: Optional[Dict[str, Any]] = None
    llm_config: Optional[Dict[str, Any]] = None
    time_sync: Optional[Dict[str, Any]] = None
    is_segmented: bool = False
    segments: Optional[List[Dict[str, Any]]] = None


class AgentTraceEvent(BaseModel):
    t: str
    msg: str
    data: Optional[Dict[str, Any]] = None
    ts_ms: Optional[int] = None


class AgentChatResponse(BaseModel):
    reply: str
    intent: Optional[str] = None
    actions: Optional[List[Dict[str, Any]]] = None
    policy: Optional[Dict[str, Any]] = None
    constraints: Optional[Dict[str, Any]] = None
    valid: Optional[bool] = None
    warnings: Optional[List[str]] = None
    errors: Optional[List[str]] = None
    route: Optional[List[str]] = None
    routes: Optional[Dict[str, List[str]]] = None
    trajectories: Optional[Dict[str, List[List[float]]]] = None
    points: Optional[int] = None
    fuel: Optional[float] = None
    allocations: Optional[Dict[str, List[str]]] = None
    allocation_strategy: Optional[str] = None
    route_distances: Optional[Dict[str, float]] = None
    mission_id: Optional[str] = None
    time_sync: Optional[Dict[str, Any]] = None
    trace: Optional[Dict[str, Any]] = None
    trace_events: Optional[List[AgentTraceEvent]] = None
    segment_results: Optional[List[Dict[str, Any]]] = None


class ApplySequenceRequest(BaseModel):
    drone_id: str
    sequence: str
    env: Dict[str, Any]
    fuel_budget: float = 50.0
    # The drone's settings (detection radius etc.), so an edited route is shaped
    # like a planned one.
    drone_config: Optional[Dict[str, Any]] = None


class ApplySequenceResponse(BaseModel):
    success: bool
    route: List[str]
    distance: float
    points: int
    trajectory: List[List[float]]
    waypoint_indices: Dict[int, int] = {}
    span_distances: List[float] = []
    anim_frame: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class ExecutiveTickRequest(BaseModel):
    mission_id: Optional[str] = None
    ui_state: Dict[str, Any] = {}
    events: List[Dict[str, Any]] = []
    env: Optional[Dict[str, Any]] = None
    drone_configs: Optional[Dict[str, Any]] = None


class ExecutiveTickResponse(BaseModel):
    action: str
    mission_id: str
    draft_plan: Optional[Dict[str, Any]] = None
    joined_plan: Optional[Dict[str, Any]] = None
    visited_targets: List[str] = []
    markers: Dict[str, List[str]] = {}
    message: str = ""


class MissionCreateRequest(BaseModel):
    name: Optional[str] = None
    env: Optional[Dict[str, Any]] = None
    drone_configs: Optional[Dict[str, Any]] = None


class MissionResponse(BaseModel):
    mission_id: str
    name: Optional[str] = None
    status: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    ended_at: Optional[str] = None
    total_points: Optional[int] = None
    total_fuel: Optional[float] = None
    has_solution: bool = False


class LLMTestRequest(BaseModel):
    provider: str
    model: str
    api_key: str = ""
    base_url: Optional[str] = None


class LLMTestResponse(BaseModel):
    success: bool
    message: str


class TTSRequest(BaseModel):
    text: str
    voice_id: Optional[str] = None


class SolveWithAllocationRequest(BaseModel):
    env: Dict[str, Any]
    drone_configs: Dict[str, Any]
    allocation_strategy: str = "geographic"
    use_sam_aware_distances: bool = True
    post_optimize: bool = False
    constraints: Optional[Dict[str, Any]] = None
    visited_targets: List[str] = []
    is_checkpoint_replan: bool = False
    repair_routes: Optional[Dict[str, Any]] = None
    mission_id: Optional[str] = None
    mission_name: Optional[str] = None
    time_sync: Optional[Dict[str, Any]] = None


class PrepareMatrixRequest(BaseModel):
    env: Dict[str, Any]
    buffer: float = 0.0


class PrepareMatrixResponse(BaseModel):
    success: bool
    num_waypoints: int
    num_paths_with_sam_avoidance: int
    excluded_targets: List[str]


class CoverageStatsRequest(BaseModel):
    solution: Dict[str, Any]
    env: Dict[str, Any]


class CoverageStatsResponse(BaseModel):
    targets_visited: int
    targets_total: int
    coverage_percent: float
    points_collected: int
    points_possible: int
    points_percent: float
    total_distance: float
    unvisited_targets: List[str]


class ReasoningLLMConfig(BaseModel):
    """Per-request model selection; credentials remain on the server."""

    provider: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None  # Only used for the existing Ollama host option.


class ReasoningAgentRequest(BaseModel):
    user_request: str
    env: Dict[str, Any]
    drone_configs: Optional[Dict[str, Any]] = None
    distance_matrix: Optional[Dict[str, Any]] = None
    time_sync: Optional[Dict[str, Any]] = None
    mission_id: Optional[str] = None
    # Checkpoint fields — the same ones Run Planner sends to /api/solve_with_allocation
    # (is_continue_from_cut plays is_checkpoint_replan's role), so the agent's planner
    # call solves exactly the problem Run Planner would.
    is_continue_from_cut: bool = False
    visited_targets: List[str] = []
    repair_routes: Optional[Dict[str, Any]] = None
    # Each drone's route as the operator sees it now ({did: [start, ..., end]}),
    # for the agent's route-edit tools (insert_in_route / remove_from_route).
    current_routes: Optional[Dict[str, List[str]]] = None
    llm_config: Optional[ReasoningLLMConfig] = None


class ReasoningAgentResponse(BaseModel):
    response: str
    reasoning: List[str]
    iterations: int
    success: bool
    error: Optional[str] = None
    routes: Optional[Dict[str, Any]] = None
    routes_changed: bool = True
    solution_kind: Optional[str] = None
    mission_id: Optional[str] = None
    distance_matrix: Optional[Dict[str, Any]] = None
    time_sync: Optional[Dict[str, Any]] = None
    # SAM wrap polygons from the agent's last planner call — same field Run
    # Planner's response carries; the UI draws them as the dashed SAM boundary.
    wrapped_polygons: Optional[List[List[List[float]]]] = None
    # Drone settings the agent changed this turn (set_drone), so the UI adopts them.
    drone_configs: Optional[Dict[str, Any]] = None


class AgentCompareRequest(BaseModel):
    message: str
    env: Dict[str, Any]
    drone_configs: Optional[Dict[str, Any]] = None
    llm_config: Optional[Dict[str, Any]] = None

    # Entrant selection. The scaling sweep runs the LLM agents only at small
    # drone counts (they are neither affordable nor reliable at hundreds), while
    # the GNN and the deterministic solver control run at every scale point.
    include_llm_agents: bool = True
    include_gnn: bool = False
    include_gnn_decoder: bool = False
    include_solver_control: bool = False
    gnn_model_path: Optional[str] = None
    gnn_route_model_path: Optional[str] = None
    allocation_strategy: str = "geographic"


class AddMemoryRequest(BaseModel):
    content: str
    category: str = "correction"


class TrajectorySwapRequest(BaseModel):
    solution: Dict[str, Any]
    env: Dict[str, Any]
    drone_configs: Dict[str, Any]
    visited_targets: List[str] = []
    auto_iterate: bool = False
    auto_regen: bool = False
    debug: bool = False


class InsertMissedRequest(BaseModel):
    solution: Dict[str, Any]
    env: Dict[str, Any]
    drone_configs: Dict[str, Any]
