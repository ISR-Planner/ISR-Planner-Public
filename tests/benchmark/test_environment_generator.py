"""Tests for the seeded environment generator.

Covers FR-001..FR-005, FR-005a per plan §FR → Test mapping.
No network calls; pure function behavior.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "benchmark_agents.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("benchmark_agents", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ba = _load_module()


def _default_params(**overrides):
    params = {
        "airports": ba.DEFAULT_AIRPORTS,
        "targets": ba.DEFAULT_TARGETS,
        "sams": ba.DEFAULT_SAMS,
        "drones": ba.DEFAULT_DRONES,
        "bbox": ba.DEFAULT_BBOX,
        "fuel_budget_km": ba.DEFAULT_FUEL_BUDGET_KM,
    }
    params.update(overrides)
    return params


def test_sub_seed_determinism():
    assert ba.derive_sub_seed(42, 0) == ba.derive_sub_seed(42, 0)
    assert ba.derive_sub_seed(42, 0, 0) == ba.derive_sub_seed(42, 0, 0)


def test_sub_seed_index_sensitivity():
    assert ba.derive_sub_seed(42, 0) != ba.derive_sub_seed(42, 1)
    assert ba.derive_sub_seed(42, 0, 0) != ba.derive_sub_seed(42, 0, 1)
    assert ba.derive_sub_seed(41, 0) != ba.derive_sub_seed(42, 0)


def test_respects_counts():
    params = _default_params(airports=3, targets=7, sams=1, drones=4)
    rng = ba.make_run_rng(1, 0)
    env = ba.generate_environment(rng, params)
    assert len(env["airports"]) == 3
    assert len(env["targets"]) == 7
    assert len(env["sams"]) == 1
    assert len(env["drone_configs"]) == 4


def test_field_constraints():
    params = _default_params()
    rng = ba.make_run_rng(99, 3)
    env = ba.generate_environment(rng, params)

    airport_id_re = re.compile(r"^A\d+$")
    target_id_re = re.compile(r"^T\d+$")
    airport_ids = set()
    for a in env["airports"]:
        assert airport_id_re.match(a["id"]), a
        assert a["id"] not in airport_ids
        airport_ids.add(a["id"])
        lat_min, lat_max, lng_min, lng_max = params["bbox"]
        assert lat_min <= a["lat"] <= lat_max
        assert lng_min <= a["lng"] <= lng_max

    tids = set()
    pmin, pmax = ba.TARGET_PRIORITY_RANGE
    for t in env["targets"]:
        assert target_id_re.match(t["id"])
        assert t["id"] not in tids
        tids.add(t["id"])
        assert pmin <= t["priority"] <= pmax
        assert t["type"] in ba.TARGET_TYPES

    rmin, rmax = ba.SAM_RANGE_KM_RANGE
    for s in env["sams"]:
        assert rmin <= s["range_km"] <= rmax

    for _, dc in env["drone_configs"].items():
        assert dc["enabled"] is True
        assert dc["start_airport"] in airport_ids
        assert dc["end_airport"] in ("-", *airport_ids)
        assert dc["fuel_budget_km"] == params["fuel_budget_km"]
        assert dc["speed_kmh"] == ba.DEFAULT_SPEED_KMH


def test_seed_reproducibility():
    """Same (batch_seed, run_index, attempt) => byte-for-byte identical env."""
    params = _default_params()
    rng1 = ba.make_run_rng(12345, 5)
    rng2 = ba.make_run_rng(12345, 5)
    env1 = ba.generate_environment(rng1, params)
    env2 = ba.generate_environment(rng2, params)
    assert json.dumps(env1, sort_keys=True) == json.dumps(env2, sort_keys=True)


def test_seed_differentiation():
    """Different seeds must produce materially different envs (F6 fix from analysis)."""
    params = _default_params()
    env_a = ba.generate_environment(ba.make_run_rng(1, 0), params)
    env_b = ba.generate_environment(ba.make_run_rng(2, 0), params)
    # At least one target position must differ.
    positions_a = [(t["lat"], t["lng"]) for t in env_a["targets"]]
    positions_b = [(t["lat"], t["lng"]) for t in env_b["targets"]]
    assert positions_a != positions_b
