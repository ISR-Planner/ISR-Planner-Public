"""GNN planner routes.

Exposes the graph builder and the GNN planner so the frontend GNN tab and the
benchmark harness can inspect the same graph the model consumes.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

MODELS_DIR = Path(__file__).resolve().parents[2] / "models"


@router.get("/api/gnn/models")
def api_gnn_models() -> Dict[str, List[Dict[str, Any]]]:
    """List trained checkpoints so the UI can offer them as a dropdown.

    Checkpoints are split by kind: the assignment model decides which drone
    gets which target, the route model decides visit order. They are separate
    dropdowns because a run can use either, both, or neither.
    """
    from server.gnn.torch_gnn import (
        CHECKPOINT_VERSION,
        ROUTE_EDGE_CHECKPOINT_VERSION,
        load_checkpoint,
    )

    assignment: List[Dict[str, Any]] = []
    route: List[Dict[str, Any]] = []
    if MODELS_DIR.is_dir():
        for path in sorted(MODELS_DIR.glob("*.pt")):
            entry: Dict[str, Any] = {
                "path": str(path.relative_to(MODELS_DIR.parent)),
                "name": path.stem,
            }
            try:
                ck = load_checkpoint(path)
                entry["version"] = ck.get("version")
                entry["epochs"] = ck.get("epochs")
                entry["examples"] = ck.get("examples")
                losses = ck.get("losses") or []
                entry["final_loss"] = round(losses[-1], 4) if losses else None
            except Exception as exc:  # noqa: BLE001
                entry["error"] = str(exc)
                entry["version"] = None

            if entry.get("version") == ROUTE_EDGE_CHECKPOINT_VERSION:
                route.append(entry)
            elif entry.get("version") == CHECKPOINT_VERSION:
                assignment.append(entry)
            else:
                # Unreadable or unknown-version checkpoint: surface it in both
                # lists so it is visible rather than silently missing.
                assignment.append(entry)
                route.append(entry)

    return {"assignment": assignment, "route": route}


class GnnBuildGraphRequest(BaseModel):
    env: Dict[str, Any]
    drone_configs: Optional[Dict[str, Any]] = None
    buffer: float = 0.0
    model_path: Optional[str] = None
    route_model_path: Optional[str] = None
    # False routes with the learned edge decoder instead of the exact solver.
    use_solver: bool = True


@router.post("/api/gnn/build_graph")
def api_gnn_build_graph(req: GnnBuildGraphRequest):
    """Return the GNN-ready graph for an environment, without running a model."""
    from server.gnn import build_graph_sample

    drone_configs = req.drone_configs or req.env.get("drone_configs", {})
    sample = build_graph_sample(req.env, req.buffer, drone_configs=drone_configs)
    return sample.to_dict()


@router.post("/api/gnn/benchmark")
def api_gnn_benchmark(req: GnnBuildGraphRequest) -> Dict[str, Any]:
    """Run every planner on the current environment and return them side by side.

    One call so a manual tester can press a single button and get a like-for-like
    comparison. No LLM agents: this is the algorithmic comparison only, and it
    scores every planner with the same `extract_solution_metrics` rule so the
    fuel-feasibility flag is comparable across rows.
    """
    import time

    from server.agents.agent_metrics import (
        run_gnn_with_metrics,
        run_solver_control_with_metrics,
    )

    drone_configs = req.drone_configs or req.env.get("drone_configs", {})
    results: Dict[str, Any] = {}

    entrants = (
        (
            "solver_control",
            lambda: run_solver_control_with_metrics(req.env, drone_configs),
        ),
        (
            "gnn",
            lambda: run_gnn_with_metrics(
                req.env,
                drone_configs,
                model_path=req.model_path,
                route_model_path=req.route_model_path,
                use_solver=True,
            ),
        ),
        (
            "gnn_decoder",
            lambda: run_gnn_with_metrics(
                req.env,
                drone_configs,
                model_path=req.model_path,
                route_model_path=req.route_model_path,
                use_solver=False,
            ),
        ),
    )

    for name, run in entrants:
        t0 = time.time()
        try:
            m = run()
            results[name] = {
                "latency_s": m.latency_s,
                "total_points": m.total_points,
                "total_fuel_km": m.total_fuel_km,
                "targets_visited": m.targets_visited,
                "targets_total": m.targets_total,
                "target_coverage": m.target_coverage,
                "valid_solution": m.valid_solution,
                "allocation_strategy": m.allocation_strategy,
                "routes": m.routes,
                "trajectories": m.trajectories,
                "error": m.error,
            }
        except Exception as exc:  # noqa: BLE001
            results[name] = {
                "latency_s": round(time.time() - t0, 3),
                "error": f"{type(exc).__name__}: {exc}",
            }

    return {
        "results": results,
        "env_summary": {
            "airports": len(req.env.get("airports", [])),
            "targets": len(req.env.get("targets", [])),
            "sams": len(req.env.get("sams", [])),
            "drones_enabled": sum(
                1
                for c in drone_configs.values()
                if isinstance(c, dict) and c.get("enabled", True)
            ),
        },
    }


@router.post("/api/gnn/predict")
def api_gnn_predict(req: GnnBuildGraphRequest):
    """Plan a mission with the GNN planner."""
    from server.gnn import run_gnn_planner

    drone_configs = req.drone_configs or req.env.get("drone_configs", {})
    result = run_gnn_planner(
        req.env,
        drone_configs,
        buffer=req.buffer,
        model_path=req.model_path,
        route_model_path=req.route_model_path,
        use_solver=req.use_solver,
    )
    return result.to_agent_payload()
