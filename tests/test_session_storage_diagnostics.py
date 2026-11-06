"""Storage failure contracts with a scripted client and synthetic error details."""

from types import SimpleNamespace
from typing import Any

import pytest

from server.database import session_storage as storage

SECRET = "synthetic-storage-credential"
PRIVATE_DETAIL = "synthetic-conversation-content"
ERROR = f"SDK rejected https://storage.invalid/{SECRET}: {PRIVATE_DETAIL}"


class ScriptedClient:
    def __init__(self, *results):
        self.results = list(results)
        self.queries = []

    def table(self, name):
        client = self
        steps = [("table", (name,), {})]
        self.queries.append(steps)

        class Query:
            def __getattr__(self, method):
                def step(*args, **kwargs):
                    steps.append((method, args, kwargs))
                    if method == "execute":
                        assert client.results, "Unexpected database query"
                        result = client.results.pop(0)
                        if isinstance(result, Exception):
                            raise result
                        return result
                    return self

                return step

        return Query()


# Every storage operation's existing failure result, plus the fixed summary error.
CASES: list[tuple[str, tuple[Any, ...], Any, str]] = [
    ("create_session", ({"source": "neutral"},), None, "creating session"),
    ("get_session", ("neutral-session",), None, "getting session"),
    (
        "update_session_metadata",
        ("neutral-session", {"new": True}),
        False,
        "updating session",
    ),
    ("delete_session", ("neutral-session",), False, "deleting session"),
    (
        "add_conversation_message",
        ("neutral-session", "user", "neutral content"),
        None,
        "adding conversation",
    ),
    (
        "get_conversation_history",
        ("neutral-session",),
        [],
        "getting conversation history",
    ),
    ("get_recent_messages", ("neutral-session",), [], "getting recent messages"),
    (
        "get_conversation_summary",
        ("neutral-session",),
        {"available": False, "error": "Conversation summary is unavailable."},
        "getting conversation summary",
    ),
    ("clear_conversation", ("neutral-session",), 0, "clearing conversation"),
    (
        "save_mission_plan",
        ("neutral-session", {}, 0, 0, 0),
        None,
        "saving mission plan",
    ),
    ("get_mission_plans", ("neutral-session",), [], "getting mission plans"),
]


@pytest.fixture(autouse=True)
def no_real_client(monkeypatch):
    def unexpected():
        pytest.fail("Unexpected real storage client access")

    monkeypatch.setattr(storage, "get_supabase_client", unexpected)


@pytest.mark.parametrize(
    "name,args,expected,operation", CASES, ids=[case[0] for case in CASES]
)
def test_storage_errors_preserve_fallbacks_without_exposing_details(
    monkeypatch, capsys, name, args, expected, operation
):
    client = ScriptedClient(RuntimeError(ERROR))
    monkeypatch.setattr(storage, "get_supabase_client", lambda: client)
    if name == "update_session_metadata":
        # Reach the update failure instead of letting the nested read fail first.
        client.results.insert(0, SimpleNamespace(data=[{"metadata": {"old": True}}]))
    result = getattr(storage, name)(*args)
    assert result == expected
    captured = capsys.readouterr()
    assert operation in captured.out
    for text in (captured.out, captured.err, repr(result)):
        for private in (SECRET, PRIVATE_DETAIL, "storage.invalid", "SDK rejected"):
            assert private not in text
    assert not client.results


@pytest.mark.parametrize(
    "name,args,expected,_operation", CASES, ids=[case[0] for case in CASES]
)
def test_unavailable_storage_skips_queries(
    monkeypatch, name, args, expected, _operation
):
    monkeypatch.setattr(storage, "get_supabase_client", lambda: None)
    if name == "get_conversation_summary":
        expected = {"available": False}
    assert getattr(storage, name)(*args) == expected


def test_session_create_read_update_and_delete_keep_data_contracts(monkeypatch):
    session = {"id": "neutral-session", "metadata": {"keep": 1, "replace": "old"}}
    client = ScriptedClient(
        SimpleNamespace(data=[session]),
        SimpleNamespace(data=[session]),
        SimpleNamespace(data=[session]),
        SimpleNamespace(data=[]),
        SimpleNamespace(data=[]),
    )
    monkeypatch.setattr(storage, "get_supabase_client", lambda: client)
    assert storage.create_session({"source": "neutral"}) == "neutral-session"
    assert storage.get_session("neutral-session") == session
    assert (
        storage.update_session_metadata("neutral-session", {"replace": "new"}) is True
    )
    assert client.queries[3][1] == (
        "update",
        ({"metadata": {"keep": 1, "replace": "new"}},),
        {},
    )
    assert storage.delete_session("neutral-session") is True
    assert not client.results


def test_conversation_queries_preserve_order_pagination_and_summary(monkeypatch):
    older = {"id": 1, "role": "user", "content": "neutral first", "created_at": "first"}
    newer = {
        "id": 2,
        "role": "assistant",
        "content": "neutral next",
        "created_at": "next",
    }
    client = ScriptedClient(
        SimpleNamespace(data=[{"id": 3}]),
        SimpleNamespace(data=[older, newer]),
        SimpleNamespace(data=[older]),
        SimpleNamespace(data=[newer, older]),
        SimpleNamespace(data=[older, newer], count=2),
        SimpleNamespace(data=[], count=2),
        SimpleNamespace(data=[]),
    )
    monkeypatch.setattr(storage, "get_supabase_client", lambda: client)
    assert (
        storage.add_conversation_message("neutral-session", "user", "neutral content")
        == 3
    )
    history = storage.get_conversation_history("neutral-session", limit=2, offset=3)
    assert [message["id"] for message in history] == [1, 2]
    assert history[0]["timestamp"] == "first"
    assert history[0]["metadata"] == {}
    assert ("range", (3, 4), {}) in client.queries[1]
    assert len(storage.get_conversation_history("neutral-session", limit=1)) == 1
    assert ("limit", (1,), {}) in client.queries[2]
    assert [
        message["id"] for message in storage.get_recent_messages("neutral-session", 2)
    ] == [1, 2]
    assert storage.get_conversation_summary("neutral-session") == {
        "available": True,
        "total_messages": 2,
        "by_role": {"user": 1, "assistant": 1},
    }
    assert storage.clear_conversation("neutral-session") == 2
    assert not client.results


def test_plan_storage_passes_payload_through_without_changing_results(monkeypatch):
    payload = {"neutral": "record"}
    row = {"id": 4, "plan_data": payload}
    client = ScriptedClient(SimpleNamespace(data=[row]), SimpleNamespace(data=[row]))
    monkeypatch.setattr(storage, "get_supabase_client", lambda: client)
    assert storage.save_mission_plan("neutral-session", payload, 0, 0, 0) == 4
    assert client.queries[0][1][1][0]["plan_data"] is payload
    assert storage.get_mission_plans("neutral-session", limit=2) == [row]
    assert ("eq", ("session_id", "neutral-session"), {}) in client.queries[1]
    assert not client.results
