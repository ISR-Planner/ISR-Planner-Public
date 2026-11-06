from fastapi import APIRouter

from server.agents.mission_executive import TickRequest, get_executive

from .models import ExecutiveTickRequest, ExecutiveTickResponse

router = APIRouter()


@router.post("/api/executive/tick", response_model=ExecutiveTickResponse)
def executive_tick(req: ExecutiveTickRequest):
    """
    Mission Executive tick endpoint.

    Called by UI to:
    - Report current state (positions, progress)
    - Send events (commands, edits)
    - Receive next action to take
    """
    executive = get_executive()

    tick_request = TickRequest(
        mission_id=req.mission_id,
        ui_state=req.ui_state,
        events=req.events,
        env=req.env,
        drone_configs=req.drone_configs,
    )

    response = executive.tick(tick_request)

    return ExecutiveTickResponse(
        action=response.action.value,
        mission_id=response.mission_id,
        draft_plan=response.draft_plan,
        joined_plan=response.joined_plan,
        visited_targets=response.visited_targets,
        markers=response.markers,
        message=response.message,
    )
