"""Drone configuration normalization helpers."""

from typing import Any, Dict


def _normalize_fuel_budget_keys(drone_configs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Translate fuel_budget_km / fuelBudget aliases to canonical fuel_budget.

    Mutates shallow copies so callers' original dicts are left untouched.
    """
    out: Dict[str, Any] = {}
    for did, cfg in drone_configs.items():
        if not isinstance(cfg, dict):
            out[did] = cfg
            continue
        normalized = dict(cfg)
        if "fuel_budget" not in normalized:
            if "fuel_budget_km" in normalized:
                normalized["fuel_budget"] = normalized["fuel_budget_km"]
            elif "fuelBudget" in normalized:
                normalized["fuel_budget"] = normalized["fuelBudget"]
        out[did] = normalized
    return out
