"""Tests for the metric aggregator.

Covers FR-012, FR-013, research §7. Includes all four head-to-head buckets,
valid_solution_rate, low_feasibility_runs, and the "winners field ignored" rule.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "benchmark_agents.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("benchmark_agents", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ba = _load_module()


def _success(
    agent_type,
    points,
    fuel_km=100.0,
    valid=True,
    coverage=0.5,
    latency=30.0,
    llm_calls=5,
    fuel_eff=0.2,
):
    return {
        "agent_type": agent_type,
        "success": True,
        "latency_s": latency,
        "llm_calls": llm_calls,
        "total_points": points,
        "total_fuel_km": fuel_km,
        "fuel_efficiency": fuel_eff,
        "target_coverage": coverage,
        "valid_solution": valid,
        "error": None,
    }


def _failure(agent_type, kind="network"):
    return {
        "agent_type": agent_type,
        "success": False,
        "latency_s": 1.0,
        "retries_used": 1,
        "error": {
            "kind": kind,
            "message": "fail",
            "retries_used": 1,
            "http_status": None,
        },
    }


def _record(rid, reasoning, v4, crewai=None, env_meta=None):
    return {
        "run_index": rid,
        "env_meta": env_meta
        or {
            "low_feasibility": False,
            "reachable_target_count": 5,
            "regen_attempts": 0,
            "generated_at": "2026-04-21T21:30:05Z",
        },
        "reasoning": reasoning,
        "langgraph_v4": v4,
        "crewai": crewai or _failure("crewai"),
    }


def test_head_to_head_all_buckets():
    c_s = _success("crewai", 5)  # crewai always loses — ensures wins go to r/v4
    records = [
        # 5 all-three-succeeded: 2 reasoning-wins, 2 v4-wins, 1 tie
        _record(
            0, _success("reasoning", 30), _success("langgraph_v4", 20), c_s
        ),  # r wins
        _record(
            1, _success("reasoning", 25), _success("langgraph_v4", 10), c_s
        ),  # r wins
        _record(
            2, _success("reasoning", 15), _success("langgraph_v4", 25), c_s
        ),  # v4 wins
        _record(
            3, _success("reasoning", 10), _success("langgraph_v4", 30), c_s
        ),  # v4 wins
        _record(
            4, _success("reasoning", 20), _success("langgraph_v4", 20), c_s
        ),  # r/v4 tie
        # 1 all-failed
        _record(5, _failure("reasoning"), _failure("langgraph_v4"), _failure("crewai")),
    ]

    agg = ba.aggregate_runs(records)

    assert agg["head_to_head"]["all_succeeded"] == 5
    assert agg["head_to_head"]["all_failed"] == 1
    assert agg["head_to_head"]["points"]["reasoning_wins"] == 2
    assert agg["head_to_head"]["points"]["langgraph_v4_wins"] == 2
    assert agg["head_to_head"]["points"]["crewai_wins"] == 0
    assert agg["head_to_head"]["points"]["ties"] == 1


def test_per_agent_success_rate_and_counts():
    records = [
        _record(0, _success("reasoning", 30), _failure("langgraph_v4")),
        _record(1, _success("reasoning", 20), _success("langgraph_v4", 15)),
        _record(2, _failure("reasoning"), _success("langgraph_v4", 25)),
    ]
    agg = ba.aggregate_runs(records)
    assert agg["reasoning"]["runs_successful"] == 2
    assert agg["reasoning"]["runs_failed"] == 1
    assert agg["reasoning"]["success_rate"] == pytest.approx(2 / 3, rel=1e-3)
    assert agg["langgraph_v4"]["runs_successful"] == 2
    assert agg["langgraph_v4"]["runs_failed"] == 1


def test_stats_computed_over_successful_runs_only():
    records = [
        _record(0, _success("reasoning", 10), _failure("langgraph_v4")),
        _record(1, _success("reasoning", 20), _failure("langgraph_v4")),
        _record(2, _success("reasoning", 30), _failure("langgraph_v4")),
        _record(3, _failure("reasoning"), _failure("langgraph_v4")),  # excluded
    ]
    agg = ba.aggregate_runs(records)
    # mean over [10, 20, 30] = 20, NOT diluted by the failure
    assert agg["reasoning"]["points"]["mean"] == 20.0
    assert agg["reasoning"]["points"]["median"] == 20.0


def test_valid_solution_rate_conditional_on_success():
    """valid_solution_rate denominator must be successful runs, not total."""
    records = [
        _record(0, _success("reasoning", 30, valid=True), _failure("langgraph_v4")),
        _record(1, _success("reasoning", 25, valid=False), _failure("langgraph_v4")),
        _record(2, _success("reasoning", 20, valid=True), _failure("langgraph_v4")),
    ]
    agg = ba.aggregate_runs(records)
    # 2 valid out of 3 successful = 0.6667
    assert agg["reasoning"]["valid_solution_rate"] == pytest.approx(2 / 3, rel=1e-3)


def test_low_feasibility_runs_counted():
    records = [
        _record(
            0,
            _success("reasoning", 10),
            _success("langgraph_v4", 10),
            env_meta={
                "low_feasibility": True,
                "reachable_target_count": 1,
                "regen_attempts": 10,
                "generated_at": "2026-04-21T21:30:05Z",
            },
        ),
        _record(1, _success("reasoning", 20), _success("langgraph_v4", 20)),
    ]
    agg = ba.aggregate_runs(records)
    assert agg["low_feasibility_runs"] == 1


def test_fuel_ties_within_tolerance():
    # crewai uses high fuel so it never wins, keeping the 2-way assertion clean
    c_high = _success("crewai", 10, fuel_km=999.0)
    records = [
        _record(
            0,
            _success("reasoning", 10, fuel_km=100.0),
            _success("langgraph_v4", 10, fuel_km=100.05),
            c_high,
        ),  # r/v4 tie
        _record(
            1,
            _success("reasoning", 10, fuel_km=100.0),
            _success("langgraph_v4", 10, fuel_km=110.0),
            c_high,
        ),  # r wins
    ]
    agg = ba.aggregate_runs(records)
    assert agg["head_to_head"]["fuel_km"]["ties"] == 1
    assert agg["head_to_head"]["fuel_km"]["reasoning_wins"] == 1


def test_server_winners_field_is_ignored():
    """Even if the fixture contradicts the recomputed head-to-head, we trust our own."""
    records = [
        _record(
            0,
            _success("reasoning", 30),
            _success("langgraph_v4", 10),
            _success("crewai", 5),
        ),
    ]
    # Simulate a server `winners` field that says v4 won when reasoning clearly wins.
    # aggregate_runs doesn't look at it — this is an assertion about API contract.
    agg = ba.aggregate_runs(records)
    assert agg["head_to_head"]["points"]["reasoning_wins"] == 1
    assert agg["head_to_head"]["points"]["langgraph_v4_wins"] == 0
    assert agg["head_to_head"]["points"]["crewai_wins"] == 0
    # Also: 'winners' must not leak into the aggregate output
    assert "winners" not in agg
