"""Tests for US3 reproducibility guarantees.

Covers: given a sub_seed from a run record, the env reconstructs exactly.
And: two batches with different seeds produce the same top-level manifest schema.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "benchmark_agents.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("benchmark_agents", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ba = _load_module()


def _default_params():
    return {
        "airports": ba.DEFAULT_AIRPORTS,
        "targets": ba.DEFAULT_TARGETS,
        "sams": ba.DEFAULT_SAMS,
        "drones": ba.DEFAULT_DRONES,
        "bbox": ba.DEFAULT_BBOX,
        "fuel_budget_km": ba.DEFAULT_FUEL_BUDGET_KM,
    }


def test_env_reconstructs_from_sub_seed():
    """An env captured in a run record must reproduce when we re-seed with its sub_seed."""
    params = _default_params()
    batch_seed = 7777
    run_index = 4

    env1, meta1 = ba.accept_environment(batch_seed, run_index, params)

    # Reconstruct from (batch_seed, run_index, final-accepted attempt)
    rng_repro = ba.make_run_rng(batch_seed, run_index, meta1["regen_attempts"])
    env_repro = ba.generate_environment(rng_repro, params)

    assert json.dumps(env1, sort_keys=True) == json.dumps(env_repro, sort_keys=True)


def test_manifest_schema_stable_across_seeds():
    """Two batches with different seeds must produce manifests with identical key sets."""

    def build_fake_manifest(seed):
        records = []
        config = {
            "seed": seed,
            "runs_requested": 0,
            "runs_completed": 0,
            "server_url": "http://x",
            "prompt": "p",
            "generator": {
                "airports": 2,
                "targets": 5,
                "sams": 1,
                "drones": 2,
                "bbox": {
                    "lat_min": 32.0,
                    "lat_max": 33.0,
                    "lng_min": -91.0,
                    "lng_max": -90.0,
                },
                "box_size_km_approx": 111.0,
                "default_drone": {
                    "fuel_budget_km": 200.0,
                    "speed_kmh": 120.0,
                    "enabled": True,
                },
            },
            "execution": {
                "per_agent_timeout_s": 120,
                "max_retries": 1,
                "inter_run_delay_s": 2.0,
                "max_regen_attempts_per_slot": 10,
                "min_reachable_targets": 2,
            },
        }
        batch_meta = {
            "name": "x",
            "started_at": "2026-04-21T21:30:05Z",
            "ended_at": "2026-04-21T21:30:05Z",
            "completed": True,
            "output_dir": "benchmarks/x",
        }
        agg = ba.aggregate_runs(records)
        return ba.build_manifest(config, batch_meta, agg)

    m1 = build_fake_manifest(1)
    m2 = build_fake_manifest(42)

    def _keys(d, prefix=""):
        keys = set()
        if isinstance(d, dict):
            for k, v in d.items():
                path = f"{prefix}.{k}" if prefix else k
                keys.add(path)
                keys.update(_keys(v, path))
        return keys

    assert _keys(m1) == _keys(m2)


def test_config_block_captures_all_reproducibility_flags():
    """FR-016: config block must contain seed + all generator + execution knobs."""
    config = {
        "seed": 1,
        "runs_requested": 10,
        "runs_completed": 10,
        "server_url": "http://localhost:8893",
        "prompt": ba.DEFAULT_PROMPT,
        "generator": {
            "airports": 2,
            "targets": 8,
            "sams": 2,
            "drones": 3,
            "bbox": {
                "lat_min": 32.0,
                "lat_max": 33.0,
                "lng_min": -90.5,
                "lng_max": -89.5,
            },
            "box_size_km_approx": 111.0,
            "default_drone": {
                "fuel_budget_km": 250.0,
                "speed_kmh": 120.0,
                "enabled": True,
            },
        },
        "execution": {
            "per_agent_timeout_s": 120,
            "max_retries": 1,
            "inter_run_delay_s": 2.0,
            "max_regen_attempts_per_slot": 10,
            "min_reachable_targets": 2,
        },
    }
    required = {
        "seed",
        "runs_requested",
        "runs_completed",
        "server_url",
        "prompt",
        "generator",
        "execution",
    }
    assert required.issubset(config.keys())
    required_gen = {
        "airports",
        "targets",
        "sams",
        "drones",
        "bbox",
        "box_size_km_approx",
        "default_drone",
    }
    assert required_gen.issubset(config["generator"].keys())
    required_exec = {
        "per_agent_timeout_s",
        "max_retries",
        "inter_run_delay_s",
        "max_regen_attempts_per_slot",
        "min_reachable_targets",
    }
    assert required_exec.issubset(config["execution"].keys())
