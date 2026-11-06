"""Tests for server/database/mission_retrieval.py (RAG Phase 2: retrieval).

All Supabase/OpenAI calls are mocked — these test the module's LOGIC (fail-soft
behavior, RPC call shape, formatting), not live infrastructure. See the
2026-09-17 session notes in project memory for the live round-trip test that
still needs running once real credentials are available (local .env's
SUPABASE_URL/OPENAI_API_KEY were both broken during this session).
"""

from unittest.mock import MagicMock, patch

from server.database.mission_retrieval import (
    EMBEDDING_DIMENSIONS,
    embed_text,
    find_similar_runs,
    format_similar_runs_for_prompt,
)

# ---- embed_text ----


def test_embed_text_empty_string_returns_none():
    assert embed_text("") is None
    assert embed_text("   ") is None


def test_embed_text_no_api_key_returns_none(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert embed_text("visit T5 first") is None


def test_embed_text_success(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-test-key")
    fake_embedding = [0.1] * EMBEDDING_DIMENSIONS

    mock_response = MagicMock()
    mock_response.data = [MagicMock(embedding=fake_embedding)]
    mock_client = MagicMock()
    mock_client.embeddings.create.return_value = mock_response

    with patch("openai.OpenAI", return_value=mock_client):
        result = embed_text("visit T5 first")

    assert result == fake_embedding
    mock_client.embeddings.create.assert_called_once()


def test_embed_text_api_error_returns_none(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-test-key")
    mock_client = MagicMock()
    mock_client.embeddings.create.side_effect = RuntimeError("API down")

    with patch("openai.OpenAI", return_value=mock_client):
        result = embed_text("visit T5 first")

    assert result is None


# ---- find_similar_runs ----


def test_find_similar_runs_no_supabase_client_returns_empty():
    with patch(
        "server.database.mission_retrieval.get_supabase_client", return_value=None
    ):
        result = find_similar_runs("visit T5 first")
    assert result == []


def test_find_similar_runs_no_embedding_returns_empty():
    with patch(
        "server.database.mission_retrieval.get_supabase_client",
        return_value=MagicMock(),
    ):
        with patch("server.database.mission_retrieval.embed_text", return_value=None):
            result = find_similar_runs("visit T5 first")
    assert result == []


def test_find_similar_runs_calls_rpc_with_correct_shape():
    fake_embedding = [0.2] * EMBEDDING_DIMENSIONS
    mock_rpc_result = MagicMock()
    mock_rpc_result.data = [
        {
            "id": "abc-123",
            "request_text": "visit T3 first",
            "similarity": 0.87,
            "total_points": 40,
        }
    ]
    mock_client = MagicMock()
    mock_client.rpc.return_value.execute.return_value = mock_rpc_result

    with patch(
        "server.database.mission_retrieval.get_supabase_client",
        return_value=mock_client,
    ):
        with patch(
            "server.database.mission_retrieval.embed_text",
            return_value=fake_embedding,
        ):
            result = find_similar_runs("visit T5 first", match_count=5)

    assert result == mock_rpc_result.data
    mock_client.rpc.assert_called_once_with(
        "match_agent_runs",
        {
            "query_embedding": fake_embedding,
            "match_count": 5,
            "min_similarity": 0.5,
        },
    )


def test_find_similar_runs_rpc_failure_returns_empty_not_raises():
    # Most likely real-world cause: migration 002 not applied yet (missing
    # function/column) — must degrade gracefully, never break the caller.
    mock_client = MagicMock()
    mock_client.rpc.return_value.execute.side_effect = RuntimeError(
        "function match_agent_runs does not exist"
    )

    with patch(
        "server.database.mission_retrieval.get_supabase_client",
        return_value=mock_client,
    ):
        with patch(
            "server.database.mission_retrieval.embed_text",
            return_value=[0.1] * EMBEDDING_DIMENSIONS,
        ):
            result = find_similar_runs("visit T5 first")

    assert result == []


# ---- format_similar_runs_for_prompt ----


def test_format_empty_list_returns_empty_string():
    assert format_similar_runs_for_prompt([]) == ""


def test_format_single_run():
    runs = [
        {
            "request_text": "visit T3 first",
            "similarity": 0.87,
            "total_points": 40,
            "total_fuel_used": 55.2,
            "is_valid": True,
            "summary": {"1": {}, "2": {}},
        }
    ]
    text = format_similar_runs_for_prompt(runs)
    assert "visit T3 first" in text
    assert "87%" in text
    assert "40 points" in text
    assert "55.2 fuel used" in text
    assert "2 drone(s)" in text


def test_format_invalid_run_flagged():
    runs = [
        {
            "request_text": "impossible request",
            "similarity": 0.6,
            "is_valid": False,
        }
    ]
    text = format_similar_runs_for_prompt(runs)
    assert "INVALID/infeasible" in text


def test_format_runs_with_no_request_text_are_skipped():
    runs = [{"request_text": "", "similarity": 0.9}]
    assert format_similar_runs_for_prompt(runs) == ""
