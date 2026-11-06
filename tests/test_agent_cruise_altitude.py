"""Real end-to-end test: an agent tool (commit_ordered_plan, reasoning_agent.py)
honors cruise_altitude_km, proving the fix in calculate_sam_aware_matrix()
reaches an actual agent solve, not just the matrix function in isolation.

Before this fix, agent-driven solves (both reasoning_agent.py and v4's
route_optimizer_node) silently ignored cruise_altitude_km — only the manual
"Run Planner" button's API path (server/api/solve_routes.py) applied it.
"""

import contextlib
import io
import json
from pathlib import Path

from server.agents import reasoning_agent

DEMO_ENV_PATH = (
    Path(__file__).resolve().parents[1] / "demo" / "isr_env_DEMO_compact.json"
)


def _load_demo():
    raw = json.loads(DEMO_ENV_PATH.read_text())
    return raw["env"], raw["drone_configs"]


def _commit(env, drone_configs, drone_id="1"):
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        reasoning_agent.set_mission_context(env, drone_configs)
        return reasoning_agent.commit_ordered_plan.invoke(
            {"drone_id": drone_id, "constraints": {}}
        )


def test_agent_ignores_altitude_at_ground_level():
    # At altitude 0 (or absent), this demo env's SAM (range_km=8) forces a
    # costly detour that busts D1's fuel budget — matches the known baseline
    # from earlier cruise-altitude work (route can't complete, stays at A1).
    env, drone_configs = _load_demo()
    env = {**env, "cruise_altitude_km": 0.0}
    summary = _commit(env, drone_configs)
    routes = reasoning_agent.get_solved_routes()
    route = routes.get("1", {}).get("route", [])
    assert route in ([], ["A1"]), (
        f"Expected D1 to fail/stay at A1 with the full-size SAM at ground level, "
        f"got route={route}, summary={summary}"
    )


def test_agent_honors_altitude_and_clears_sam():
    # At an altitude above the SAM's range, the SAM is fully cleared — D1
    # should now be able to complete the mission on the same fuel budget.
    # This is the actual proof that commit_ordered_plan's real solve call
    # (via calculate_sam_aware_matrix, NOT a mock) sees the shrunk radius.
    env, drone_configs = _load_demo()
    sam_range = env["sams"][0]["range_km"]
    env = {**env, "cruise_altitude_km": sam_range + 1.0}

    _commit(env, drone_configs)
    routes = reasoning_agent.get_solved_routes()
    route = routes.get("1", {}).get("route", [])
    assert len(route) > 2, (
        f"Expected D1 to complete the mission once altitude clears the SAM, "
        f"got route={route}"
    )
