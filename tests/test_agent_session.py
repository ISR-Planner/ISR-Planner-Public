"""Agent mission-session memory: the reasoning agent continues a mission's
thread across turns and clears it only on End Mission."""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from server.agents.agent_session import (
    clear_all_sessions,
    end_session,
    get_or_create_session,
    get_session,
    save_session,
)


def setup_function(_):
    clear_all_sessions()


def test_none_mission_id_is_sessionless():
    assert get_session(None) is None
    assert get_or_create_session(None) is None
    # save with no id is a silent no-op, not a crash
    save_session(None, [HumanMessage(content="hi")], {})
    assert get_session(None) is None


def test_fresh_session_then_continue():
    # First turn: no session yet.
    assert get_session("m1") is None
    sess = get_or_create_session("m1")
    assert sess is not None
    assert sess.is_fresh  # nothing saved yet

    # Save the first turn's thread + solution.
    thread = [
        SystemMessage(content="sys"),
        HumanMessage(content="plan it"),
        AIMessage(content="done"),
    ]
    save_session("m1", thread, {"1": {"route": ["A1", "T1", "A1"], "points": 5}})

    # Second turn: the session continues — thread + routes carried.
    sess2 = get_or_create_session("m1")
    assert not sess2.is_fresh
    assert sess2.turns == 1
    assert len(sess2.messages) == 3
    assert sess2.solved_routes["1"]["points"] == 5


def test_turns_increment_and_thread_grows():
    save_session("m1", [HumanMessage(content="t1")], {})
    save_session("m1", [HumanMessage(content="t1"), HumanMessage(content="t2")], {})
    sess = get_session("m1")
    assert sess.turns == 2
    assert len(sess.messages) == 2


def test_last_turn_metadata_is_saved_when_provided():
    save_session(
        "m1",
        [HumanMessage(content="solve")],
        {"1": {"route": ["A1", "T1", "A1"]}},
        {"user_request": "solve", "route_changed": True},
    )

    sess = get_session("m1")

    assert sess.last_turn == {"user_request": "solve", "route_changed": True}


def test_end_session_clears_thread():
    save_session("m1", [HumanMessage(content="hi")], {"1": {"route": []}})
    assert get_session("m1") is not None
    assert end_session("m1") is True
    assert get_session("m1") is None
    # Ending a missing session is False, not a crash.
    assert end_session("m1") is False


def test_sessions_are_isolated_by_mission_id():
    save_session("m1", [HumanMessage(content="a")], {"1": {"points": 1}})
    save_session("m2", [HumanMessage(content="b")], {"2": {"points": 2}})
    assert get_session("m1").solved_routes["1"]["points"] == 1
    assert get_session("m2").solved_routes["2"]["points"] == 2
    end_session("m1")
    assert get_session("m1") is None
    assert get_session("m2") is not None  # m2 untouched
