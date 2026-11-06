"""Shared helpers for reading drone_config dicts.

Centralizes the key aliases accepted across the codebase so every read site
agrees on the rules and missing-budget bugs fail loudly instead of silently
defaulting to a placeholder value.
"""

from __future__ import annotations

from typing import Any, Mapping

# Accepted aliases, in order of preference. A drone_config that carries more
# than one is expected to have consistent values across them; the first found
# wins. These three are the only spellings in use anywhere in the codebase
# (frontend, backend tests, benchmark harness) — see the caller survey.
FUEL_BUDGET_KEYS: tuple[str, ...] = ("fuelBudget", "fuel_budget", "fuel_budget_km")


def resolve_fuel_budget(cfg: Mapping[str, Any], drone_id: str | None = None) -> float:
    """Return the fuel budget from a drone_config dict, or raise ValueError.

    Raises instead of silently defaulting because no legitimate caller omits
    the fuel budget; a missing key always indicates a bug upstream (wrong key
    spelling, forgotten field, etc.). Loud failures beat corrupt results.
    """
    for key in FUEL_BUDGET_KEYS:
        value = cfg.get(key)
        if value is not None:
            return float(value)
    who = f"drone '{drone_id}'" if drone_id is not None else "drone_config"
    raise ValueError(
        f"{who}: no fuel budget found. "
        f"Expected one of {list(FUEL_BUDGET_KEYS)}; got keys: {sorted(cfg.keys())}"
    )
