#!/usr/bin/env python3
"""
Opt-in live-provider smoke tests for ISR Multi-Agent v4 (Reasoning Agents).

Pytest requires ISR_RUN_LIVE_AGENT_TESTS=1; standalone execution requires --live.
Both paths use the same fixture and inherit explicitly exported provider settings.
Private dotenv files are never loaded by this runner.

Tests:
1. Question answering (drone configs, targets, etc.)
2. Basic solve request
3. Command-based request
"""

import os

import pytest

# These scenarios require live provider responses; they are not offline coverage.
pytestmark = pytest.mark.live_agent

# Simple test environment
TEST_ENV = {
    "airports": [
        {"id": "A1", "label": "A1", "x": 0, "y": 0},
        {"id": "A2", "label": "A2", "x": 100, "y": 0},
    ],
    "targets": [
        {"id": "T1", "label": "T1", "x": 30, "y": 40, "priority": 8},
        {"id": "T2", "label": "T2", "x": 60, "y": 20, "priority": 5},
        {"id": "T3", "label": "T3", "x": 80, "y": 60, "priority": 10},
        {"id": "T4", "label": "T4", "x": 20, "y": 70, "priority": 3},
    ],
    "sams": [
        {"pos": [50, 40], "range": 15},
    ],
}

TEST_CONFIGS = {
    "1": {
        "enabled": True,
        "fuelBudget": 200,
        "homeAirport": "A1",
        "accessibleTargets": [],  # ALL
    },
    "2": {
        "enabled": True,
        "fuelBudget": 150,
        "homeAirport": "A2",
        "accessibleTargets": ["T2", "T3"],  # Limited
    },
}


@pytest.fixture(autouse=True)
def isolate_live_agent_configuration(monkeypatch):
    """Gate live calls before agent imports and disable dotenv/database access."""
    if os.getenv("ISR_RUN_LIVE_AGENT_TESTS") != "1":
        pytest.skip(
            "Live agent smoke tests require ISR_RUN_LIVE_AGENT_TESTS=1; "
            "not part of offline coverage."
        )
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)

    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)

    from server.database import supabase_client

    monkeypatch.setattr(supabase_client, "_supabase_client", None)


def test_question():
    """Test question answering."""
    print("\n" + "=" * 60)
    print("TEST 1: Question Answering")
    print("=" * 60)

    from server.agents.isr_agent_multi_v4 import run_multi_agent_v4

    result = run_multi_agent_v4(
        user_message="What drone configuration are you using?",
        environment=TEST_ENV,
        drone_configs=TEST_CONFIGS,
    )

    print("\nRESPONSE:")
    print(result.get("response", "No response"))
    print("\n" + "-" * 40)

    assert isinstance(result, dict)
    assert result.get("response")


def test_solve():
    """Test basic solve request."""
    print("\n" + "=" * 60)
    print("TEST 2: Solve Request")
    print("=" * 60)

    from server.agents.isr_agent_multi_v4 import run_multi_agent_v4

    result = run_multi_agent_v4(
        user_message="Solve - plan optimal mission",
        environment=TEST_ENV,
        drone_configs=TEST_CONFIGS,
    )

    print("\nRESPONSE:")
    print(result.get("response", "No response"))

    print("\nROUTES:")
    for did, route_data in result.get("routes", {}).items():
        print(f"  D{did}: {route_data}")

    print("\nSUGGESTIONS:")
    for s in result.get("suggestions", []):
        print(f"  - {s}")

    print("\n" + "-" * 40)

    assert isinstance(result, dict)
    assert result.get("response")


def test_command():
    """Test command-based request."""
    print("\n" + "=" * 60)
    print("TEST 3: Command Request")
    print("=" * 60)

    from server.agents.isr_agent_multi_v4 import run_multi_agent_v4

    result = run_multi_agent_v4(
        user_message="D1 must visit T1 and T4. D2 handles T2 and T3.",
        environment=TEST_ENV,
        drone_configs=TEST_CONFIGS,
    )

    print("\nRESPONSE:")
    print(result.get("response", "No response"))

    print("\nROUTES:")
    for did, route_data in result.get("routes", {}).items():
        print(f"  D{did}: {route_data}")

    print("\n" + "-" * 40)

    assert isinstance(result, dict)
    assert result.get("response")


def main():
    """Use pytest in standalone mode so its isolation fixture is never bypassed."""
    import argparse
    from pathlib import Path
    from unittest.mock import patch

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Allow live provider calls using exported settings.",
    )
    args = parser.parse_args()
    if not args.live:
        parser.error(
            "Live provider calls require --live. No dotenv file will be loaded."
        )
    with patch.dict(os.environ, {"ISR_RUN_LIVE_AGENT_TESTS": "1"}):
        return pytest.main([str(Path(__file__).resolve()), "-v", "-s"])


if __name__ == "__main__":
    raise SystemExit(main())
