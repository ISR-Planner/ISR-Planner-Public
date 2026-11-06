"""Tests for the reasoning agent's learning loop (RAG over past agent_runs).

Covers: the request + mission-shape text used for both storing and searching
runs, the outcome recorded for each solved run, and the similar-missions
context injected into a fresh conversation's prompt. Supabase/OpenAI are
mocked — these test logic, not live infrastructure.
"""

from unittest.mock import MagicMock, patch

from server.agents import reasoning_agent
from server.database.mission_ledger import create_agent_run
from server.database.mission_retrieval import (
    build_retrieval_text,
    describe_mission_shape,
    format_similar_runs_for_prompt,
)

ENV = {
    "targets": [
        {"id": "T1", "priority": 5},
        {"id": "T2", "priority": 10},
        {"id": "T3", "priority": 1},
    ],
    "airports": [{"id": "A1"}, {"id": "A2"}, {"id": "TK1", "is_tanker": True}],
    "sams": [{"id": "S0"}],
}
CONFIGS = {"1": {"enabled": True}, "2": {"enabled": True}, "3": {"enabled": False}}


# ---- mission shape / retrieval text ----


def test_describe_mission_shape():
    assert describe_mission_shape(ENV, CONFIGS) == (
        "Mission: 3 targets (priority 1-10), 2 airports, 1 tankers, 1 SAMs, 2 drones"
    )


def test_describe_mission_shape_empty_env():
    assert describe_mission_shape({}, {}) == ""
    assert describe_mission_shape(None) == ""


def test_build_retrieval_text_includes_request_and_shape():
    text = build_retrieval_text("Solve", ENV, CONFIGS)
    assert text.startswith("Request: Solve\n")
    assert "3 targets" in text


def test_build_retrieval_text_without_env_is_just_request():
    assert build_retrieval_text("  Solve  ") == "Solve"


# ---- formatting ----


def test_format_counts_only_drone_keys_and_shows_reaction():
    runs = [
        {
            "request_text": "Solve",
            "similarity": 0.9,
            "total_points": 40,
            "summary": {"reaction": "corrected"},
            "hard_constraints": {"first": "T9"},
        }
    ]
    text = format_similar_runs_for_prompt(runs)
    assert "drone(s)" not in text  # "reaction" is not a drone
    assert "operator corrected" in text
    assert 'constraints {"first":"T9"}' in text


# ---- create_agent_run embedding_text ----


def test_create_agent_run_embeds_embedding_text_when_given():
    mock_client = MagicMock()
    result = MagicMock()
    result.data = [{"id": "run-1"}]
    mock_client.table.return_value.insert.return_value.execute.return_value = result

    with (
        patch(
            "server.database.mission_ledger.get_supabase_client",
            return_value=mock_client,
        ),
        patch(
            "server.database.mission_retrieval.embed_text", return_value=[0.1]
        ) as mock_embed,
    ):
        create_agent_run(
            request_text="Solve", embedding_text="Request: Solve\nMission: x"
        )

    mock_embed.assert_called_once_with("Request: Solve\nMission: x")
    inserted = mock_client.table.return_value.insert.call_args[0][0]
    assert inserted["request_text"] == "Solve"


# ---- outcome ----


def test_run_outcome_totals_and_validity():
    routes = {
        "1": {"route": ["A1", "T1", "T2", "A1"], "distance": 50.0, "fuel_budget": 100},
        "2": {
            "route": ["A2", "T3", "A2"],
            "distance": 30.0,
            "fuel_budget": 100,
            "points": 7,
        },
    }
    out = reasoning_agent._run_outcome(routes, ENV)
    assert out["total_points"] == 15 + 7  # D1 from priorities, D2 from its own points
    assert out["total_fuel_used"] == 80.0
    assert out["is_valid"] is True
    assert out["routes"]["1"]["route"] == ["A1", "T1", "T2", "A1"]


def test_run_outcome_over_budget_is_invalid():
    routes = {"1": {"route": ["A1", "T1", "A1"], "distance": 120.0, "fuel_budget": 100}}
    assert reasoning_agent._run_outcome(routes, ENV)["is_valid"] is False


def test_run_outcome_without_budget_is_unknown():
    routes = {"1": {"route": ["A1", "T1", "A1"], "distance": 10.0}}
    assert reasoning_agent._run_outcome(routes, ENV)["is_valid"] is None


# ---- similar-missions context ----


def test_similar_context_skips_runs_without_outcome():
    runs = [
        {"request_text": "list targets", "similarity": 0.9, "total_points": None},
        {
            "request_text": "Solve",
            "similarity": 0.8,
            "total_points": 30,
            "summary": {"reaction": "accepted"},
        },
    ]
    with patch(
        "server.database.mission_retrieval.find_similar_runs", return_value=runs
    ) as mock_find:
        text = reasoning_agent._similar_missions_context("Solve", ENV, CONFIGS)

    query = mock_find.call_args[0][0]
    assert "Mission: 3 targets" in query  # searched with request + mission shape
    assert "list targets" not in text
    assert "30 points" in text
    assert "operator accepted" in text


def test_similar_context_empty_when_nothing_usable():
    with patch("server.database.mission_retrieval.find_similar_runs", return_value=[]):
        assert reasoning_agent._similar_missions_context("Solve", ENV, CONFIGS) == ""


def test_similar_context_never_raises():
    with patch(
        "server.database.mission_retrieval.find_similar_runs",
        side_effect=RuntimeError("boom"),
    ):
        assert reasoning_agent._similar_missions_context("Solve", ENV, CONFIGS) == ""


# ---- agent solves checkpoint requests exactly like Run Planner ----


def _fake_planner_response():
    resp = MagicMock()
    resp.success = True
    resp.routes = {
        "1": {"route": ["C1-1", "T3", "A2"], "distance": 10.0, "fuel_budget": 100}
    }
    resp.allocations = {"1": ["T3"]}
    return resp


def test_solve_full_mission_forwards_checkpoint_fields():
    repair = {"1": {"route": ["C1-1", "T3", "A2"]}}
    reasoning_agent.set_mission_context(
        {"targets": [{"id": "T3", "priority": 5}], "airports": [{"id": "A2"}]},
        {"1": {"enabled": True, "start_airport": "C1-1", "end_airport": "A2"}},
        checkpoint_replan={
            "is_checkpoint_replan": True,
            "visited_targets": ["T1", "T2"],
            "repair_routes": repair,
        },
    )
    with patch(
        "server.api.solve_routes.run_solve_with_allocation",
        return_value=_fake_planner_response(),
    ) as mock_solve:
        reasoning_agent.solve_full_mission.invoke({})

    req = mock_solve.call_args[0][0]
    assert req.is_checkpoint_replan is True
    assert req.visited_targets == ["T1", "T2"]
    assert req.repair_routes == repair


def test_solve_full_mission_fresh_solve_has_no_checkpoint_fields():
    reasoning_agent.set_mission_context(
        {"targets": [{"id": "T3", "priority": 5}], "airports": [{"id": "A2"}]},
        {"1": {"enabled": True, "start_airport": "A2", "end_airport": "A2"}},
    )
    with patch(
        "server.api.solve_routes.run_solve_with_allocation",
        return_value=_fake_planner_response(),
    ) as mock_solve:
        reasoning_agent.solve_full_mission.invoke({})

    req = mock_solve.call_args[0][0]
    assert req.is_checkpoint_replan is False
    assert req.visited_targets == []
    assert req.repair_routes is None


# ---- agent reply carries the SAM wrap polygons, like Run Planner's ----

POLY = [[[32.0, -90.0], [32.1, -90.0], [32.1, -90.1]]]


def test_solve_full_mission_remembers_wrapped_polygons():
    reasoning_agent.set_mission_context(
        {"targets": [{"id": "T3", "priority": 5}], "airports": [{"id": "A2"}]},
        {"1": {"enabled": True, "start_airport": "A2", "end_airport": "A2"}},
    )
    resp = _fake_planner_response()
    resp.wrapped_polygons = POLY
    with patch("server.api.solve_routes.run_solve_with_allocation", return_value=resp):
        reasoning_agent.solve_full_mission.invoke({})
    assert reasoning_agent._mission_context["wrapped_polygons"] == POLY


def test_planner_wrapper_remembers_wrapped_polygons_and_keeps_signature():
    import inspect

    reasoning_agent.set_mission_context({}, {})
    with patch.object(
        reasoning_agent,
        "_planner_solve_mission_with_allocation",
        return_value={"routes": {}, "wrapped_polygons": POLY},
    ):
        reasoning_agent.solve_mission_with_allocation(env={}, drone_configs={})
    assert reasoning_agent._mission_context["wrapped_polygons"] == POLY
    # _solve_supports_constraints inspects the real planner's signature through the wrapper
    params = inspect.signature(reasoning_agent.solve_mission_with_allocation).parameters
    assert "env" in params


def test_new_turn_clears_previous_wrapped_polygons():
    reasoning_agent._mission_context["wrapped_polygons"] = POLY
    reasoning_agent.set_mission_context({}, {})
    assert reasoning_agent._mission_context.get("wrapped_polygons") is None


# ---- mid-mission turn keeps only routes from the current start ----


def test_routes_from_current_starts_drops_stale_origins():
    routes = {
        "1": {"route": ["C3-1", "T8", "A2"]},  # planned from the current cut → keep
        "2": {"route": ["A2", "T2", "A1"]},  # planned from the airport → stale
    }
    configs = {"1": {"start_airport": "C3-1"}, "2": {"start_airport": "D2_START"}}
    assert reasoning_agent._routes_from_current_starts(routes, configs) == {
        "1": {"route": ["C3-1", "T8", "A2"]}
    }


# ---- route edits: the sequence-bar operation, for the agent (real evaluator) ----

CUT_ENV = {
    "airports": [
        {"id": "A1", "lat": 32.20, "lng": -90.20},
        {"id": "A2", "lat": 32.20, "lng": -90.00},
    ],
    "targets": [
        {"id": "T1", "lat": 32.30, "lng": -90.10, "priority": 7},
        {"id": "T2", "lat": 32.12, "lng": -90.05, "priority": 3},
    ],
    "sams": [{"center": {"lat": 32.20, "lng": -90.10}, "range_km": 5}],
    "checkpoints": [
        {"id": "C2-1", "lat": 32.28, "lng": -90.18},
        {"id": "C2-2", "lat": 32.15, "lng": -90.02},
    ],
}
CUT_CONFIGS = {
    "1": {
        "enabled": True,
        "start_airport": "C2-1",
        "end_airport": "A2",
        "fuel_budget": 150,
        "start_fuel": 120,
    },
    "2": {
        "enabled": True,
        "start_airport": "C2-2",
        "end_airport": "A1",
        "fuel_budget": 150,
        "start_fuel": 110,
    },
}


def _cut_context(current_routes=None):
    reasoning_agent.set_mission_context(
        CUT_ENV,
        {k: dict(v) for k, v in CUT_CONFIGS.items()},
        current_routes=current_routes
        or {"1": ["C2-1", "T1", "A2"], "2": ["C2-2", "T2", "A1"]},
    )
    reasoning_agent.clear_solved_routes()


def test_abort_is_a_route_edit_from_each_diamond():
    _cut_context()
    for did in ("1", "2"):
        out = reasoning_agent.set_route.invoke({"drone_id": did, "waypoints": ["A1"]})
        assert out.startswith("COMMITTED"), out
    routes = reasoning_agent.get_solved_routes()
    assert routes["1"]["route"] == ["C2-1", "A1"]
    assert routes["2"]["route"] == ["C2-2", "A1"]
    assert routes["1"]["points"] == 0
    # environment untouched: both targets still in the mission
    assert [
        t["id"] for t in reasoning_agent._mission_context["environment"]["targets"]
    ] == ["T1", "T2"]


def test_set_route_accepts_drone_prefix_and_ignores_repeated_start():
    _cut_context()
    reasoning_agent.set_route.invoke(
        {"drone_id": "D1", "waypoints": ["C2-1", "T1", "A2"]}
    )
    assert reasoning_agent.get_solved_routes()["1"]["route"] == ["C2-1", "T1", "A2"]


def test_insert_in_route_keeps_the_rest_of_the_route():
    _cut_context()
    reasoning_agent.insert_in_route.invoke({"drone_id": "1", "target_id": "T2"})
    route = reasoning_agent.get_solved_routes()["1"]["route"]
    assert route[0] == "C2-1" and route[-1] == "A2"
    assert set(route) == {"C2-1", "T1", "T2", "A2"}


def test_insert_in_route_after_a_given_waypoint():
    _cut_context()
    reasoning_agent.insert_in_route.invoke(
        {"drone_id": "1", "target_id": "T2", "after": "C2-1"}
    )
    assert reasoning_agent.get_solved_routes()["1"]["route"] == [
        "C2-1",
        "T2",
        "T1",
        "A2",
    ]


def test_remove_from_route_drops_only_that_target():
    _cut_context()
    reasoning_agent.remove_from_route.invoke({"drone_id": "1", "target_id": "T1"})
    assert reasoning_agent.get_solved_routes()["1"]["route"] == ["C2-1", "A2"]


def test_edits_build_on_this_turns_earlier_edit():
    _cut_context()
    reasoning_agent.remove_from_route.invoke({"drone_id": "1", "target_id": "T1"})
    reasoning_agent.insert_in_route.invoke({"drone_id": "1", "target_id": "T2"})
    assert reasoning_agent.get_solved_routes()["1"]["route"] == ["C2-1", "T2", "A2"]


def test_route_edit_refuses_disabled_drone():
    _cut_context()
    reasoning_agent.set_drone.invoke({"drone_id": "2", "enabled": False})
    out = reasoning_agent.set_route.invoke({"drone_id": "2", "waypoints": ["A1"]})
    assert out.startswith("Error")


def test_only_changed_drones_are_returned_and_described():
    _cut_context()
    # a route carried from an earlier turn (not changed this turn)
    reasoning_agent.set_solved_routes({"2": {"route": ["C2-2", "T2", "A1"]}})
    reasoning_agent.set_route.invoke({"drone_id": "1", "waypoints": ["A1"]})
    changed = reasoning_agent._routes_changed_this_turn(
        reasoning_agent.get_solved_routes()
    )
    assert list(changed) == ["1"]
    text = reasoning_agent._committed_plan_text(changed)
    assert "D1: C2 → A1" in text
    assert "Unchanged: D2" in text


def test_set_drone_changes_are_returned_to_the_ui():
    _cut_context()
    assert "drone_configs" not in reasoning_agent._turn_extras()
    reasoning_agent.set_drone.invoke({"drone_id": "1", "end_airport": "a1"})
    extras = reasoning_agent._turn_extras()
    assert extras["drone_configs"]["1"]["end_airport"] == "A1"


# ---- a changed mission invalidates the conversation's remembered plan ----

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage  # noqa: E402

from server.agents import agent_session  # noqa: E402

CHANGE_ENV = {
    "airports": [
        {"id": "A1", "lat": 32.2, "lng": -90.2},
        {"id": "A2", "lat": 32.3, "lng": -90.2},
        {"id": "A3", "lat": 32.3, "lng": -90.0},
    ],
    "targets": [{"id": "T1", "lat": 32.25, "lng": -90.1, "priority": 5}],
    "sams": [],
}


def _cfgs(ends):
    return {
        str(i + 1): {
            "enabled": True,
            "fuel_budget": 150,
            "start_airport": f"A{i + 1}",
            "end_airport": e,
        }
        for i, e in enumerate(ends)
    }


class _SilentLLM:
    """Answers without calling any tool — like the agent that said 'already solved'."""

    def __init__(self):
        self.seen = []

    def invoke(self, messages):
        self.seen.append(list(messages))
        return AIMessage(content="The mission has already been solved.")


def _turn(monkeypatch, mission_id, ends, request="Solve", on_canvas=None):
    llm = _SilentLLM()
    monkeypatch.setattr(reasoning_agent, "create_reasoning_agent", lambda: (llm, []))
    monkeypatch.setattr(reasoning_agent, "_store_mission_command", lambda *_: None)
    result = reasoning_agent.run_reasoning_agent(
        request,
        CHANGE_ENV,
        _cfgs(ends),
        mission_id=mission_id,
        current_routes=on_canvas,
    )
    return llm, result


def _seed_planned_session(mission_id, ends):
    """A session whose last turn PLANNED with these end airports."""
    agent_session.end_session(mission_id)
    agent_session.save_session(
        mission_id,
        [
            SystemMessage(content="sys"),
            HumanMessage(content="Solve"),
            AIMessage(content="solved"),
        ],
        {"1": {"route": ["A1", "T1", "A1"], "distance": 10.0, "points": 5}},
        {"planned_inputs": reasoning_agent._mission_inputs(CHANGE_ENV, _cfgs(ends))},
    )


def test_changed_end_airports_are_announced_and_old_plan_dropped(monkeypatch):
    _seed_planned_session("m-change", ["-", "-", "-"])
    llm, result = _turn(monkeypatch, "m-change", ["A2", "A3", "A1"])
    last_human = [m for m in llm.seen[0] if isinstance(m, HumanMessage)][-1].content
    assert "MISSION CHANGED SINCE YOUR LAST PLAN" in last_human
    assert "D1 end airport ANY → A2" in last_human
    assert "D2 end airport ANY → A3" in last_human
    assert "D3 end airport ANY → A1" in last_human
    assert (
        reasoning_agent.get_solved_routes() == {}
    )  # no "already solved" plan to report
    assert result["routes_changed"] is False


def test_change_still_flagged_after_a_turn_that_did_not_plan(monkeypatch):
    _seed_planned_session("m-change2", ["-", "-", "-"])
    _turn(monkeypatch, "m-change2", ["A2", "A3", "A1"], request="What changed?")
    llm, _ = _turn(monkeypatch, "m-change2", ["A2", "A3", "A1"])
    last_human = [m for m in llm.seen[0] if isinstance(m, HumanMessage)][-1].content
    assert "MISSION CHANGED SINCE YOUR LAST PLAN" in last_human


def test_unchanged_mission_keeps_the_plan_and_adds_no_note(monkeypatch):
    _seed_planned_session("m-same", ["A2", "A3", "A1"])
    llm, _ = _turn(monkeypatch, "m-same", ["A2", "A3", "A1"])
    last_human = [m for m in llm.seen[0] if isinstance(m, HumanMessage)][-1].content
    assert last_human == "Solve"
    assert "1" in reasoning_agent.get_solved_routes()


def test_describe_input_changes_covers_targets_and_drones():
    old = reasoning_agent._mission_inputs(CHANGE_ENV, _cfgs(["-"]))
    env2 = {
        **CHANGE_ENV,
        "targets": CHANGE_ENV["targets"] + [{"id": "T2", "lat": 32.26, "lng": -90.1}],
    }
    cfg2 = _cfgs(["-"])
    cfg2["1"]["enabled"] = False
    changes = reasoning_agent._describe_input_changes(
        old, reasoning_agent._mission_inputs(env2, cfg2)
    )
    assert "D1 disabled" in changes
    assert "targets added: T2" in changes


# ---- a plan the agent keeps must be the plan on the canvas ----


def test_kept_plan_not_on_canvas_is_redisplayed(monkeypatch):
    # Same mission, but the operator discarded the draft: the canvas is empty.
    _seed_planned_session("m-discarded", ["A2", "A3", "A1"])
    _, result = _turn(monkeypatch, "m-discarded", ["A2", "A3", "A1"], on_canvas={})
    assert result["routes_changed"] is True  # the UI draws it as a draft
    assert result["routes"]["1"]["route"] == ["A1", "T1", "A1"]
    assert "re-displayed on the map" in result["response"]


def test_kept_plan_already_on_canvas_changes_nothing(monkeypatch):
    _seed_planned_session("m-shown", ["A2", "A3", "A1"])
    _, result = _turn(
        monkeypatch,
        "m-shown",
        ["A2", "A3", "A1"],
        request="Why did D2 not fly?",
        on_canvas={"1": ["A1", "T1", "A1"]},
    )
    assert result["routes_changed"] is False
    assert "Committed plan" not in result["response"]
