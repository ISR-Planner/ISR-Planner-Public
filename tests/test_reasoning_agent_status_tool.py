from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from server.agents import reasoning_agent
from server.agents.agent_session import clear_all_sessions, get_session, save_session


def setup_function(_):
    clear_all_sessions()
    reasoning_agent.clear_solved_routes()


def test_current_mission_status_reports_committed_targets_and_missing():
    reasoning_agent.set_mission_context(
        {
            "targets": [
                {"id": "T1", "priority": 5},
                {"id": "T2", "priority": 10},
                {"id": "T3", "priority": 5},
            ],
            "airports": [{"id": "A1"}],
        },
        {"1": {"enabled": True, "fuel_budget": 30, "start_airport": "A1"}},
    )
    reasoning_agent.set_solved_routes(
        {
            "1": {
                "route": ["A1", "T2", "TK1", "T1", "A1"],
                "points": 15,
                "distance": 24.0,
                "fuel_budget": 15.0,
                "leg_distances": [10.0, 14.0],
                "refuel_indices": [2],
            }
        }
    )

    status = reasoning_agent.get_current_mission_status.invoke({})

    assert "D1: 2 target(s)" in status
    assert "first_target=T2" in status
    assert "refuel_stops=['TK1']" in status
    assert "feasible=YES (L1: 10.0/15.0, L2: 14.0/15.0)" in status
    assert "Mission total: 2/3 target(s)" in status
    assert "Unvisited targets: ['T3']" in status


def test_current_mission_status_reports_no_committed_route():
    reasoning_agent.set_mission_context(
        {"targets": [{"id": "T1", "priority": 5}], "airports": [{"id": "A1"}]},
        {"1": {"enabled": True}},
    )
    reasoning_agent.clear_solved_routes()

    status = reasoning_agent.get_current_mission_status.invoke({})

    assert "No committed agent route is stored" in status
    assert "zero targets" in status


def test_followup_question_loads_session_routes_and_does_not_change_solution(
    monkeypatch,
):
    env = {
        "targets": [
            {"id": "T1", "priority": 5},
            {"id": "T2", "priority": 10},
            {"id": "T3", "priority": 5},
        ],
        "airports": [{"id": "A1"}],
    }
    configs = {"1": {"enabled": True, "fuel_budget": 30, "start_airport": "A1"}}
    carried_route = {
        "1": {
            "route": ["A1", "T2", "TK1", "T1", "A1"],
            "points": 15,
            "distance": 24.0,
            "fuel_budget": 15.0,
            "leg_distances": [10.0, 14.0],
            "refuel_indices": [2],
        }
    }
    save_session(
        "mission-memory",
        [
            SystemMessage(content="system"),
            HumanMessage(content="Solve"),
            AIMessage(content="Solved"),
        ],
        carried_route,
        {
            "user_request": "Solve",
            "routes_changed_for_ui": True,
            "route_changed": True,
            "route_delta": {
                "added_targets": ["T1", "T2"],
                "removed_targets": [],
                "points_delta": 15,
                "fuel_delta": 24.0,
            },
        },
    )

    class FakeLLM:
        def __init__(self):
            self.calls = []

        def invoke(self, messages):
            self.calls.append(list(messages))
            if len(self.calls) == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "get_current_mission_status",
                            "args": {},
                            "id": "status_call",
                        }
                    ],
                )
            return AIMessage(content="D1 has visited 2 targets: T2 and T1.")

    fake_llm = FakeLLM()
    monkeypatch.setattr(
        reasoning_agent,
        "create_reasoning_agent",
        lambda: (fake_llm, [reasoning_agent.get_current_mission_status]),
    )

    result = reasoning_agent.run_reasoning_agent(
        "How many targets have you visited?",
        env,
        configs,
        mission_id="mission-memory",
        max_iterations=3,
    )

    assert result["success"] is True
    assert result["routes"] == carried_route
    assert result["routes_changed"] is False
    assert "2 targets" in result["response"]

    tool_messages = [m for m in fake_llm.calls[-1] if isinstance(m, ToolMessage)]
    assert tool_messages
    assert "D1: 2 target(s)" in tool_messages[-1].content
    assert "Unvisited targets: ['T3']" in tool_messages[-1].content
    assert "Previous completed command: Solve" in tool_messages[-1].content
    assert "Previous completed turn changed route: YES" in tool_messages[-1].content

    session = get_session("mission-memory")
    assert session is not None
    assert session.last_turn["user_request"] == "How many targets have you visited?"
    assert session.last_turn["routes_changed_for_ui"] is False
    assert session.last_turn["route_changed"] is False
    assert session.last_turn["route_delta"]["added_targets"] == []


def test_final_response_softens_uncertified_optimality_claims(monkeypatch):
    env = {"targets": [{"id": "T1", "priority": 5}], "airports": [{"id": "A1"}]}
    configs = {"1": {"enabled": True, "fuel_budget": 30, "start_airport": "A1"}}

    class FakeLLM:
        def invoke(self, _messages):
            return AIMessage(
                content=(
                    "This is the optimal route and no further improvements possible."
                )
            )

    monkeypatch.setattr(
        reasoning_agent,
        "create_reasoning_agent",
        lambda: (FakeLLM(), [reasoning_agent.get_current_mission_status]),
    )

    result = reasoning_agent.run_reasoning_agent(
        "Solve",
        env,
        configs,
        mission_id="mission-language",
        max_iterations=1,
    )

    assert "optimal route" not in result["response"].lower()
    assert "no further improvements possible" not in result["response"].lower()
    assert "best route found by the current planner pipeline" in result["response"]
    assert (
        "the current planner pipeline did not find further improvements"
        in result["response"]
    )


def test_final_response_rewrites_muddled_certification_claim(monkeypatch):
    env = {"targets": [{"id": "T1", "priority": 5}], "airports": [{"id": "A1"}]}
    configs = {"1": {"enabled": True, "fuel_budget": 30, "start_airport": "A1"}}

    class FakeLLM:
        def invoke(self, _messages):
            return AIMessage(
                content=(
                    "While it is not explicitly certified as best found by the "
                    "current planner pipeline, it is feasible."
                )
            )

    monkeypatch.setattr(
        reasoning_agent,
        "create_reasoning_agent",
        lambda: (FakeLLM(), [reasoning_agent.get_current_mission_status]),
    )

    result = reasoning_agent.run_reasoning_agent(
        "Is this optimal?",
        env,
        configs,
        mission_id="mission-certification-language",
        max_iterations=1,
    )

    assert "certified as best found" not in result["response"]
    assert "not globally certified" in result["response"]


def test_prompt_instructs_agent_to_avoid_false_proof_claims():
    prompt = reasoning_agent.REASONING_AGENT_PROMPT

    assert "Avoid False Proof Claims" in prompt
    assert "best route found by the current planner pipeline" in prompt
    assert "unless a tool explicitly returned a proof" in prompt
    assert "I cannot certify global optimality" in prompt
