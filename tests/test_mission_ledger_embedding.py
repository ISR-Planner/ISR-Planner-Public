"""Tests for mission_ledger.create_agent_run's embedding write path.

Confirms: request_text gets embedded and included in the insert when
possible, embedding failures don't block run creation, and an insert
that fails specifically because of the embedding column (migration 002
not applied) retries without it rather than losing the whole run.
"""

from unittest.mock import MagicMock, patch

from server.database.mission_ledger import create_agent_run


def _mock_insert_result(run_id="run-123"):
    result = MagicMock()
    result.data = [{"id": run_id}]
    return result


def test_create_agent_run_includes_embedding_when_available():
    fake_embedding = [0.1] * 1536
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.return_value = (
        _mock_insert_result()
    )

    with patch(
        "server.database.mission_ledger.get_supabase_client",
        return_value=mock_client,
    ):
        with patch(
            "server.database.mission_retrieval.embed_text",
            return_value=fake_embedding,
        ):
            run_id = create_agent_run(request_text="visit T5 first")

    assert run_id == "run-123"
    inserted_record = mock_client.table.return_value.insert.call_args[0][0]
    assert inserted_record["request_embedding"] == fake_embedding


def test_create_agent_run_no_request_text_skips_embedding():
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.return_value = (
        _mock_insert_result()
    )

    with patch(
        "server.database.mission_ledger.get_supabase_client",
        return_value=mock_client,
    ):
        run_id = create_agent_run(request_text=None)

    assert run_id == "run-123"
    inserted_record = mock_client.table.return_value.insert.call_args[0][0]
    assert "request_embedding" not in inserted_record


def test_create_agent_run_embedding_failure_still_creates_run():
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.return_value = (
        _mock_insert_result()
    )

    with patch(
        "server.database.mission_ledger.get_supabase_client",
        return_value=mock_client,
    ):
        with patch("server.database.mission_retrieval.embed_text", return_value=None):
            run_id = create_agent_run(request_text="visit T5 first")

    assert run_id == "run-123"
    inserted_record = mock_client.table.return_value.insert.call_args[0][0]
    assert "request_embedding" not in inserted_record


def test_create_agent_run_retries_without_embedding_on_insert_failure():
    # Simulates migration 002 not applied: first insert (with embedding)
    # fails, second insert (without it) succeeds.
    fake_embedding = [0.1] * 1536
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.side_effect = [
        RuntimeError('column "request_embedding" does not exist'),
        _mock_insert_result(),
    ]

    with patch(
        "server.database.mission_ledger.get_supabase_client",
        return_value=mock_client,
    ):
        with patch(
            "server.database.mission_retrieval.embed_text",
            return_value=fake_embedding,
        ):
            run_id = create_agent_run(request_text="visit T5 first")

    assert run_id == "run-123"
    assert mock_client.table.return_value.insert.call_count == 2
    # Second call's record must NOT contain request_embedding.
    second_call_record = mock_client.table.return_value.insert.call_args_list[1][0][0]
    assert "request_embedding" not in second_call_record


def test_create_agent_run_both_inserts_fail_returns_none():
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.side_effect = [
        RuntimeError('column "request_embedding" does not exist'),
        RuntimeError("connection refused"),
    ]

    with patch(
        "server.database.mission_ledger.get_supabase_client",
        return_value=mock_client,
    ):
        with patch(
            "server.database.mission_retrieval.embed_text",
            return_value=[0.1] * 1536,
        ):
            run_id = create_agent_run(request_text="visit T5 first")

    assert run_id is None
