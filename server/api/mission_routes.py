from fastapi import APIRouter, HTTPException

from .mission_state import (
    compute_cfg_hash,
    compute_env_hash,
    end_mission_record,
    get_mission_record,
    mission_to_response,
    new_mission_id,
    set_mission_name,
    upsert_mission,
)
from .models import MissionCreateRequest, MissionResponse

router = APIRouter()


@router.post("/api/missions", response_model=MissionResponse)
def create_mission(req: MissionCreateRequest):
    """Create a new, explicitly-declared mission."""
    mission_id = new_mission_id()
    env = req.env or {}
    drone_configs = req.drone_configs or {}
    rec = upsert_mission(
        mission_id,
        env=env,
        drone_configs=drone_configs,
        env_hash=compute_env_hash(env) if env else "",
        cfg_hash=compute_cfg_hash(drone_configs) if drone_configs else "",
        routes={},
        allocation=None,
    )
    if req.name:
        rec = set_mission_name(mission_id, req.name) or rec
    print(f"[missions] created {mission_id} name={req.name!r}", flush=True)
    return mission_to_response(mission_id, rec)


@router.get("/api/missions/{mission_id}", response_model=MissionResponse)
def get_mission(mission_id: str):
    """Fetch a mission's identity, lifecycle, and solution summary."""
    rec = get_mission_record(mission_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"mission '{mission_id}' not found")
    return mission_to_response(mission_id, rec)


@router.post("/api/missions/{mission_id}/end", response_model=MissionResponse)
def end_mission(mission_id: str):
    """Explicitly end a mission."""
    rec = end_mission_record(mission_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"mission '{mission_id}' not found")
    print(f"[missions] ended {mission_id}", flush=True)
    try:
        from server.agents.agent_session import end_session

        if end_session(mission_id):
            print(f"[missions] cleared agent session for {mission_id}", flush=True)
    except Exception as e:
        print(f"[missions] agent session clear skipped: {e}", flush=True)
    return mission_to_response(mission_id, rec)
