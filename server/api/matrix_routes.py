from fastapi import APIRouter

from server.solver.route_evaluation import evaluate_route, route_animation_frame
from server.solver.solver_bridge import (
    clear_cached_matrix,
    get_current_matrix,
    prepare_distance_matrix,
)

from .models import (
    ApplySequenceRequest,
    ApplySequenceResponse,
    PrepareMatrixRequest,
    PrepareMatrixResponse,
)

router = APIRouter()


@router.post("/api/prepare_matrix", response_model=PrepareMatrixResponse)
def api_prepare_matrix(req: PrepareMatrixRequest):
    """
    Pre-calculate SAM-aware distance matrix for the environment.

    Call this after editing is complete to have the matrix ready
    for fast solving. The matrix is cached and reused by solve endpoints.
    """
    matrix_data = prepare_distance_matrix(req.env, req.buffer)

    num_waypoints = len(matrix_data.get("labels", []))
    num_paths = len(matrix_data.get("paths", {}))
    excluded = matrix_data.get("excluded_targets", [])

    return PrepareMatrixResponse(
        success=True,
        num_waypoints=num_waypoints,
        num_paths_with_sam_avoidance=num_paths,
        excluded_targets=excluded,
    )


@router.get("/api/matrix_status")
def api_matrix_status():
    """
    Get the status of the cached distance matrix.
    """
    matrix = get_current_matrix()

    if matrix is None:
        return {
            "cached": False,
            "num_waypoints": 0,
            "num_paths": 0,
        }

    return {
        "cached": True,
        "num_waypoints": len(matrix.get("labels", [])),
        "num_paths": len(matrix.get("paths", {})),
        "excluded_targets": matrix.get("excluded_targets", []),
    }


@router.post("/api/clear_matrix")
def api_clear_matrix():
    """
    Clear the cached distance matrix.
    """
    clear_cached_matrix()
    return {"success": True}


@router.post("/api/apply_sequence", response_model=ApplySequenceResponse)
def api_apply_sequence(req: ApplySequenceRequest):
    """
    Apply a manually edited sequence for a single drone: trajectory, distance and
    points for exactly that route. Same code as the agent's route-edit tools
    (server/solver/route_evaluation.py).
    """
    route_ids = [wp.strip().upper() for wp in req.sequence.split(",") if wp.strip()]
    result = evaluate_route(
        route_ids,
        req.env,
        req.drone_id,
        distance_matrix=get_current_matrix(),
        drone_config=req.drone_config,
    )
    if not result["success"]:
        print(f"[apply_sequence] D{req.drone_id}: {result['error']}", flush=True)
    return ApplySequenceResponse(
        **result,
        anim_frame=route_animation_frame(
            result, req.env, req.drone_config, req.drone_id
        ),
    )
