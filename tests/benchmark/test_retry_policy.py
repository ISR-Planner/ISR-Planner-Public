"""Tests for error classification and one-retry-on-transient policy.

Covers FR-009, FR-009a, FR-009b, FR-009c.
No network calls; monkey-patched session.post.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "benchmark_agents.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("benchmark_agents", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ba = _load_module()


def _mock_response(status: int, body: dict | None = None, headers: dict | None = None):
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    r.headers = headers or {}
    r.text = "" if body is None else str(body)
    r.json = MagicMock(
        return_value=body if body is not None else {},
        side_effect=None if body is not None else ValueError("no body"),
    )
    return r


_VALID_BODY = {
    "reasoning": {"agent_type": "reasoning"},
    "langgraph_v4": {"agent_type": "langgraph_v4"},
    "crewai": {"agent_type": "crewai"},
}


def test_classify_error_all_kinds():
    assert ba.classify_error(requests.exceptions.Timeout()) == "timeout"
    assert ba.classify_error(requests.exceptions.ReadTimeout()) == "timeout"
    assert ba.classify_error(requests.exceptions.ConnectionError()) == "network"
    assert ba.classify_error(_mock_response(500)) == "http_5xx"
    assert ba.classify_error(_mock_response(502)) == "http_5xx"
    assert ba.classify_error(_mock_response(429)) == "http_429"
    assert ba.classify_error(_mock_response(404)) == "http_4xx"
    assert ba.classify_error(_mock_response(400)) == "http_4xx"


def test_is_transient_excludes_timeout():
    """FR-009c: timeouts MUST NOT retry."""
    assert ba.is_transient("network")
    assert ba.is_transient("http_5xx")
    assert ba.is_transient("http_429")
    assert not ba.is_transient("timeout")
    assert not ba.is_transient("http_4xx")
    assert not ba.is_transient("parse")
    assert not ba.is_transient("validation")
    assert not ba.is_transient("unknown")


def test_retry_succeeds_on_transient_network():
    session = MagicMock()
    session.post = MagicMock(
        side_effect=[
            requests.exceptions.ConnectionError("first"),
            _mock_response(200, _VALID_BODY),
        ]
    )
    result, err, retries, _ = ba._invoke_compare_with_retry(
        session,
        "http://x/api",
        {},
        max_retries=1,
    )
    assert result == _VALID_BODY
    assert err is None
    assert retries == 1


def test_retry_gives_up_after_one_attempt():
    session = MagicMock()
    session.post = MagicMock(
        side_effect=[
            requests.exceptions.ConnectionError("first"),
            requests.exceptions.ConnectionError("second"),
        ]
    )
    result, err, retries, _ = ba._invoke_compare_with_retry(
        session,
        "http://x/api",
        {},
        max_retries=1,
    )
    assert result is None
    assert err["kind"] == "network"
    assert err["retries_used"] == 1


def test_timeout_never_retries(monkeypatch):
    session = MagicMock()
    session.post = MagicMock(
        side_effect=[
            requests.exceptions.ReadTimeout("slow"),
            _mock_response(
                200, _VALID_BODY
            ),  # would succeed on retry — must NOT be called
        ]
    )
    result, err, retries, _ = ba._invoke_compare_with_retry(
        session,
        "http://x/api",
        {},
        max_retries=1,
    )
    assert result is None
    assert err["kind"] == "timeout"
    assert err["retries_used"] == 0
    assert session.post.call_count == 1  # second attempt skipped


def test_http_5xx_retries_once():
    session = MagicMock()
    session.post = MagicMock(
        side_effect=[
            _mock_response(503),
            _mock_response(200, _VALID_BODY),
        ]
    )
    result, err, retries, _ = ba._invoke_compare_with_retry(
        session,
        "http://x/api",
        {},
        max_retries=1,
    )
    assert result == _VALID_BODY
    assert retries == 1


def test_http_429_respects_retry_after(monkeypatch):
    slept = []
    monkeypatch.setattr(ba.time, "sleep", lambda s: slept.append(s))
    session = MagicMock()
    session.post = MagicMock(
        side_effect=[
            _mock_response(429, headers={"Retry-After": "1.5"}),
            _mock_response(200, _VALID_BODY),
        ]
    )
    result, _, _, _ = ba._invoke_compare_with_retry(
        session,
        "http://x/api",
        {},
        max_retries=1,
    )
    assert result == _VALID_BODY
    assert 1.5 in slept


def test_http_4xx_never_retries():
    session = MagicMock()
    session.post = MagicMock(side_effect=[_mock_response(400)])
    result, err, retries, _ = ba._invoke_compare_with_retry(
        session,
        "http://x/api",
        {},
        max_retries=1,
    )
    assert result is None
    assert err["kind"] == "http_4xx"
    assert retries == 0
    assert session.post.call_count == 1


def test_parse_error_never_retries():
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 200
    resp.headers = {}
    resp.text = "not json"
    resp.json = MagicMock(side_effect=ValueError("bad json"))
    session = MagicMock()
    session.post = MagicMock(return_value=resp)
    result, err, retries, _ = ba._invoke_compare_with_retry(
        session,
        "http://x/api",
        {},
        max_retries=1,
    )
    assert err["kind"] == "parse"
    assert retries == 0


def test_validation_error_never_retries():
    # 200 with a body missing required `reasoning` key
    session = MagicMock()
    session.post = MagicMock(
        return_value=_mock_response(
            200,
            {"langgraph_v4": {"agent_type": "langgraph_v4"}},
        )
    )
    result, err, retries, _ = ba._invoke_compare_with_retry(
        session,
        "http://x/api",
        {},
        max_retries=1,
    )
    assert err["kind"] == "validation"
    assert retries == 0
