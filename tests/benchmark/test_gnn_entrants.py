"""Tests for GNN/solver entrants and the drone-count scaling sweep.

Covers the harness changes that let one batch compare the GNN against the
deterministic solver control across a ladder of drone counts.
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


def _agent_block(name, points=10, latency=1.0, llm_calls=0):
    return {
        "agent_type": name,
        "latency_s": latency,
        "llm_calls": llm_calls,
        "total_points": points,
        "total_fuel_km": 100.0,
        "total_budget_km": 250.0,
        "fuel_efficiency": 0.4,
        "targets_visited": 5,
        "targets_total": 5,
        "target_coverage": 1.0,
        "valid_solution": True,
        "drone_metrics": {},
        "response_text": "",
    }


# --- entrant selection -----------------------------------------------------


def test_optional_entrants_are_normalized_when_requested():
    entrants = ["gnn", "gnn_decoder", "solver_control"]
    response = {n: _agent_block(n) for n in entrants}

    blocks = ba._all_agents_from_response(response, None, 1.0, 0, entrants)

    assert sorted(blocks) == sorted(entrants)
    assert all(blocks[n]["success"] for n in entrants)


def test_requested_entrant_missing_from_response_is_a_failure():
    """A silently absent entrant must be recorded, not dropped from the batch."""
    response = {"gnn": _agent_block("gnn")}

    blocks = ba._all_agents_from_response(
        response, None, 1.0, 0, ["gnn", "solver_control"]
    )

    assert blocks["gnn"]["success"] is True
    assert blocks["solver_control"]["success"] is False
    assert "missing agent block" in blocks["solver_control"]["error"]["message"]


def test_whole_call_failure_marks_every_requested_entrant():
    err = {"kind": "network", "message": "boom", "retries_used": 2, "http_status": None}

    blocks = ba._all_agents_from_response(None, err, 5.0, 2, ["gnn", "solver_control"])

    assert not any(b["success"] for b in blocks.values())
    assert all(b["error"]["kind"] == "network" for b in blocks.values())


def test_run_record_carries_optional_entrants():
    blocks = ba._all_agents_from_response(
        {n: _agent_block(n) for n in ("gnn", "solver_control")},
        None,
        1.0,
        0,
        ["gnn", "solver_control"],
    )

    record = ba._build_run_record(
        7,
        0,
        {"drone_configs": {}},
        {"regen_attempts": 0},
        "prompt",
        "now",
        blocks,
    )

    assert record["schema_version"] == 2
    assert record["gnn"]["success"] is True
    assert record["solver_control"]["success"] is True


# --- scaling sweep ---------------------------------------------------------


class _Args:
    def __init__(self, **kw):
        self.drones = kw.get("drones", 3)
        self.targets = kw.get("targets", 8)
        self.drone_sweep_values = kw.get("drone_sweep_values", [])
        self.target_sweep_values = kw.get("target_sweep_values", [])


def test_scale_points_default_to_a_single_point():
    points = ba._scale_points(_Args(drones=4, targets=9))
    assert points == [(4, 9)]


def test_scale_points_broadcast_single_target_value():
    points = ba._scale_points(
        _Args(drone_sweep_values=[1, 2, 100], target_sweep_values=[6])
    )
    assert points == [(1, 6), (2, 6), (100, 6)]


def test_scale_points_pair_positionally():
    points = ba._scale_points(
        _Args(drone_sweep_values=[1, 10], target_sweep_values=[5, 50])
    )
    assert points == [(1, 5), (10, 50)]


def test_aggregate_by_scale_point_groups_and_reports_failures():
    """A planner that fails at a scale point must show a reduced success rate."""

    def _rec(drones, gnn_ok, solver_ok):
        return {
            "scale_point": {"drones": drones, "targets": 8},
            "gnn": {**_agent_block("gnn"), "success": gnn_ok, "error": None},
            "solver_control": {
                **_agent_block("solver_control"),
                "success": solver_ok,
                "error": None,
            },
        }

    records = [
        _rec(1, True, True),
        _rec(1, True, True),
        # At 100 drones the exact solver falls over but the GNN still returns.
        _rec(100, True, False),
        _rec(100, True, False),
    ]

    points = ba._aggregate_by_scale_point(records)

    assert [p["drones"] for p in points] == [1, 100]
    small, large = points
    assert small["agents"]["solver_control"]["success_rate"] == 1.0
    assert large["agents"]["solver_control"]["success_rate"] == 0.0
    assert large["agents"]["gnn"]["success_rate"] == 1.0


def test_scaling_section_renders_only_when_a_sweep_ran():
    assert ba._render_scaling_section({"by_scale_point": []}) == []

    lines = ba._render_scaling_section(
        {
            "by_scale_point": [
                {
                    "drones": 1,
                    "targets": 8,
                    "runs": 2,
                    "agents": {
                        "gnn": ba._per_agent_aggregate(
                            [
                                {
                                    "gnn": {
                                        **_agent_block("gnn"),
                                        "success": True,
                                        "error": None,
                                    }
                                }
                            ],
                            "gnn",
                        )
                    },
                }
            ]
        }
    )
    assert any("Scaling sweep" in ln for ln in lines)


# --- CLI validation --------------------------------------------------------


def test_no_llm_agents_without_any_other_entrant_is_rejected():
    with pytest.raises(SystemExit):
        ba.parse_args(["--runs", "1", "--no-llm-agents"])


def test_no_llm_agents_with_gnn_is_accepted():
    args = ba.parse_args(["--runs", "1", "--no-llm-agents", "--gnn"])
    assert ba.selected_entrants(args) == ["gnn"]
    assert ba.compare_options_for(args)["include_llm_agents"] is False


def test_default_invocation_keeps_the_llm_trio():
    args = ba.parse_args(["--runs", "1"])
    assert ba.selected_entrants(args) == ["reasoning", "langgraph_v4", "crewai"]


def test_drone_sweep_rejects_non_integers():
    with pytest.raises(SystemExit):
        ba.parse_args(["--runs", "1", "--drone-sweep", "1,two,3"])
