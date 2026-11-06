"""Synthetic failure checks for storage adapters, with no SDK or network access."""

import copy
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from server.agents import agent_memory as memory
from server.database import mission_ledger as ledger
from server.database import mission_retrieval as retrieval

PRIVATE = "synthetic-private-diagnostic-marker"
ERROR = f"SDK echoed credential and record at https://storage.invalid/{PRIVATE}"

LEDGER_CASES: list[tuple[str, tuple[Any, ...], dict[str, Any], Any]] = [
    ("create_agent_run", (), {}, None),
    ("update_agent_run", ("neutral",), {"total_points": 0}, False),
    ("get_agent_run", ("neutral",), {}, None),
    ("create_agent_trace", ("neutral", {}), {}, None),
    ("update_agent_trace", ("neutral", {}), {}, False),
    ("get_agent_trace", ("neutral",), {}, None),
    ("log_optimizer_step", ("neutral", 0, "neutral", {}, {}, {}), {}, None),
    ("get_optimizer_steps", ("neutral",), {}, []),
    ("get_optimizer_stats", (), {}, {"total": 0, "accepted": 0, "rejected": 0}),
    ("create_policy_rule", ("neutral", {}), {}, None),
    ("get_active_policy_rules", (), {}, []),
    ("deactivate_policy_rule", ("neutral",), {}, False),
    ("update_policy_rule", ("neutral", {"title": "neutral"}), {}, False),
    ("get_cached_matrix", ("neutral", "neutral"), {}, None),
    ("cache_matrix", ("neutral", "neutral", "neutral", [[0]], ["neutral"]), {}, None),
    ("get_matrix_by_id", ("neutral",), {}, None),
    ("cache_sam_paths", ("neutral", "neutral", "neutral", {}), {}, None),
    ("get_cached_sam_paths", ("neutral", "neutral"), {}, None),
]
MEMORY_CASES: list[tuple[str, tuple[Any, ...], Any]] = [
    ("_load_memory_supabase", (), []),
    ("_add_memory_supabase", ("neutral content", "neutral"), None),
    ("_clear_memory_supabase", (), 0),
    ("_delete_memory_supabase", (1,), False),
]


@pytest.fixture(autouse=True)
def block_real_services(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Unexpected SDK/client/embedding access")

    monkeypatch.setattr(ledger, "get_supabase_client", unexpected)
    monkeypatch.setattr(retrieval, "get_supabase_client", unexpected)
    monkeypatch.setattr(memory, "_get_supabase", unexpected)
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=unexpected))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-embedding-key")
    monkeypatch.setattr(memory, "_save_memory_file", unexpected)


def assert_safe(result, capsys):
    captured = capsys.readouterr()
    assert captured.out, "Expected an operation-specific failure diagnostic"
    for text in (repr(result), captured.out, captured.err):
        assert PRIVATE not in text
        assert "storage.invalid" not in text
        assert "SDK echoed" not in text


class FailingClient:
    def __init__(self):
        self.calls = 0

    def table(self, _name):
        self.calls += 1
        raise RuntimeError(ERROR)


@pytest.mark.parametrize(
    "name,args,kwargs,expected", LEDGER_CASES, ids=[case[0] for case in LEDGER_CASES]
)
def test_ledger_failure_results_and_safe_diagnostics(
    monkeypatch, capsys, name, args, kwargs, expected
):
    client = FailingClient()
    monkeypatch.setattr(ledger, "get_supabase_client", lambda: client)
    result = getattr(ledger, name)(*args, **kwargs)
    assert result == expected
    assert client.calls == 1
    assert_safe(result, capsys)


@pytest.mark.parametrize(
    "name,args,expected", MEMORY_CASES, ids=[case[0] for case in MEMORY_CASES]
)
def test_memory_failure_results_and_safe_diagnostics(
    monkeypatch, capsys, name, args, expected
):
    client = FailingClient()
    monkeypatch.setattr(memory, "_get_supabase", lambda: client)
    result = getattr(memory, name)(*args)
    assert result == expected
    assert client.calls == 1
    assert_safe(result, capsys)


@pytest.mark.parametrize("retry_succeeds", [True, False])
def test_embedding_insert_retry_keeps_payload_policy_without_echoing_errors(
    monkeypatch, capsys, retry_succeeds
):
    records = []

    class Insert:
        def execute(self):
            if len(records) == 1 or not retry_succeeds:
                raise RuntimeError(ERROR)
            return SimpleNamespace(data=[{"id": "neutral-run"}])

    class Client:
        def table(self, name):
            assert name == "agent_runs"
            return self

        def insert(self, record):
            records.append(copy.deepcopy(record))
            return Insert()

    monkeypatch.setattr(ledger, "get_supabase_client", lambda: Client())
    monkeypatch.setattr(retrieval, "embed_text", lambda _text: [0.1])
    result = ledger.create_agent_run(request_text="neutral input")
    assert result == ("neutral-run" if retry_succeeds else None)
    assert len(records) == 2
    assert records[0]["request_embedding"] == [0.1]
    assert records[1] == {
        key: value for key, value in records[0].items() if key != "request_embedding"
    }
    assert_safe(result, capsys)


@pytest.mark.parametrize("constructor_fails", [True, False])
def test_embedding_failures_remain_unavailable_and_safe(
    monkeypatch, capsys, constructor_fails
):
    def fail(*args, **kwargs):
        raise RuntimeError(ERROR)

    client = SimpleNamespace(embeddings=SimpleNamespace(create=fail))
    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=fail if constructor_fails else lambda **_: client),
    )
    result = retrieval.embed_text("neutral input")
    assert result is None
    assert_safe(result, capsys)


def test_retrieval_rpc_failure_keeps_empty_result_and_request_shape(
    monkeypatch, capsys
):
    calls = []

    class Client:
        def rpc(self, name, payload):
            calls.append((name, payload))
            return self

        def execute(self):
            raise RuntimeError(ERROR)

    monkeypatch.setattr(retrieval, "get_supabase_client", lambda: Client())
    monkeypatch.setattr(retrieval, "embed_text", lambda _text: [0.1])
    result = retrieval.find_similar_runs(
        "neutral input", match_count=2, min_similarity=0.7
    )
    assert result == []
    assert calls == [
        (
            "match_agent_runs",
            {"query_embedding": [0.1], "match_count": 2, "min_similarity": 0.7},
        )
    ]
    assert_safe(result, capsys)


def test_memory_write_failure_preserves_existing_json_fallback(
    monkeypatch, capsys, tmp_path
):
    # Exercise file fallback only in a disposable directory, never the user's memory file.
    monkeypatch.setattr(memory, "_get_supabase", lambda: FailingClient())
    monkeypatch.setattr(memory, "MEMORY_FILE", tmp_path / "neutral-memory.json")
    saved = []
    monkeypatch.setattr(memory, "_save_memory_file", lambda rows: saved.extend(rows))
    result = memory.add_memory("neutral content", "neutral")
    assert saved == [result]
    assert result["content"] == "neutral content"
    assert result["category"] == "neutral"
    assert_safe(result, capsys)


def test_memory_read_failure_does_not_add_a_new_file_fallback(monkeypatch, capsys):
    def unexpected():
        pytest.fail("Read errors do not trigger the write-only file fallback")

    monkeypatch.setattr(memory, "_get_supabase", lambda: FailingClient())
    monkeypatch.setattr(memory, "_load_memory_file", unexpected)
    result = memory.load_memory()
    assert result == []
    assert_safe(result, capsys)
