"""Tests for the feasibility filter and the env-acceptance regeneration loop.

Covers FR-005b.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "benchmark_agents.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("benchmark_agents", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ba = _load_module()


def _env(airports, targets, sams, drones):
    return {
        "airports": airports,
        "targets": targets,
        "sams": sams,
        "drone_configs": drones,
    }


def test_all_targets_reachable_passes():
    # Airport at (32.0, -90.0); targets within ~5 km; generous fuel budget.
    env = _env(
        airports=[{"id": "A1", "lat": 32.0, "lng": -90.0}],
        targets=[
            {"id": "T1", "lat": 32.02, "lng": -90.0, "priority": 5, "type": "a"},
            {"id": "T2", "lat": 32.0, "lng": -90.02, "priority": 5, "type": "a"},
            {"id": "T3", "lat": 32.04, "lng": -90.0, "priority": 5, "type": "a"},
        ],
        sams=[],
        drones={
            "1": {
                "enabled": True,
                "fuel_budget_km": 200.0,
                "speed_kmh": 120.0,
                "start_airport": "A1",
                "end_airport": "-",
            },
        },
    )
    count = ba.reachable_target_count(env)
    assert count == 3


def test_fuel_constrained_rejects():
    # One drone, tiny budget. Targets far enough that 2 * haversine exceeds budget.
    env = _env(
        airports=[{"id": "A1", "lat": 32.0, "lng": -90.0}],
        targets=[
            {"id": "T1", "lat": 33.0, "lng": -90.0, "priority": 5, "type": "a"},
            {"id": "T2", "lat": 32.0, "lng": -91.0, "priority": 5, "type": "a"},
        ],
        sams=[],
        drones={
            "1": {
                "enabled": True,
                "fuel_budget_km": 50.0,
                "speed_kmh": 120.0,
                "start_airport": "A1",
                "end_airport": "-",
            },
        },
    )
    count = ba.reachable_target_count(env)
    assert count == 0
    assert count < ba.MIN_REACHABLE_TARGETS


def test_sam_blocks_direct_path():
    # Airport at origin, target across a SAM in the middle of the path.
    # haversine path is clear fuel-wise; SAM range is wide enough to block it.
    env = _env(
        airports=[{"id": "A1", "lat": 32.0, "lng": -90.0}],
        targets=[
            {"id": "T1", "lat": 32.10, "lng": -90.0, "priority": 5, "type": "a"},
        ],
        sams=[
            # Centered on the straight line midway between A1 and T1
            {"center": {"lat": 32.05, "lng": -90.0}, "range_km": 20.0},
        ],
        drones={
            "1": {
                "enabled": True,
                "fuel_budget_km": 500.0,
                "speed_kmh": 120.0,
                "start_airport": "A1",
                "end_airport": "-",
            },
        },
    )
    # Fuel-only would pass; SAM stage must block.
    assert ba.reachable_target_count(env) == 0


def test_sam_avoided_when_off_path():
    # Target off to the side; SAM not on the straight-line path.
    env = _env(
        airports=[{"id": "A1", "lat": 32.0, "lng": -90.0}],
        targets=[
            {"id": "T1", "lat": 32.10, "lng": -90.0, "priority": 5, "type": "a"},
        ],
        sams=[
            # SAM well to the west; should not intersect the north-bound path
            {"center": {"lat": 32.05, "lng": -91.0}, "range_km": 10.0},
        ],
        drones={
            "1": {
                "enabled": True,
                "fuel_budget_km": 500.0,
                "speed_kmh": 120.0,
                "start_airport": "A1",
                "end_airport": "-",
            },
        },
    )
    assert ba.reachable_target_count(env) == 1


def test_accept_loop_first_attempt_succeeds():
    params = {
        "airports": ba.DEFAULT_AIRPORTS,
        "targets": ba.DEFAULT_TARGETS,
        "sams": ba.DEFAULT_SAMS,
        "drones": ba.DEFAULT_DRONES,
        "bbox": ba.DEFAULT_BBOX,
        "fuel_budget_km": ba.DEFAULT_FUEL_BUDGET_KM,
    }
    env, meta = ba.accept_environment(batch_seed=1, run_index=0, params=params)
    assert "airports" in env and "targets" in env
    assert meta["regen_attempts"] >= 0
    assert isinstance(meta["low_feasibility"], bool)
    assert "reachable_target_count" in meta
    assert meta["generated_at"].endswith("Z")


def test_accept_loop_exhausts_then_flags():
    """Force all regeneration attempts to fail by setting tiny fuel budget.
    After exhausting, the env must still be accepted with low_feasibility=True."""
    params = {
        "airports": 1,
        "targets": 3,
        "sams": 0,
        "drones": 1,
        # Tiny bbox forces large distances with tight fuel budget.
        "bbox": (32.0, 33.0, -91.0, -90.0),
        "fuel_budget_km": 1.0,  # impossibly small
    }
    env, meta = ba.accept_environment(batch_seed=42, run_index=0, params=params)
    assert meta["low_feasibility"] is True
    assert meta["regen_attempts"] == ba.MAX_REGEN_ATTEMPTS_PER_SLOT
    assert meta["reachable_target_count"] < ba.MIN_REACHABLE_TARGETS
    # env still returned (not None)
    assert len(env["targets"]) == 3
