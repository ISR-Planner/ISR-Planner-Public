"""Airport/refuel compatibility rules shared by solver orchestration."""

from typing import Any, Dict

# Fixed cost (in km-equivalents) for stopping at an airport to refuel.
# Models the time the drone spends on the ground: fuel transfer, taxi, takeoff.
# Added to refuel solutions so refueling has a non-zero opportunity cost.
REFUEL_STOP_COST_KM = 1.0

# Runway compatibility. A drone with min-runway requirement D can use an
# airport with runway-length A iff A >= D. Ordering: S < M < L.
# Drones default S (smallest, fits all airports). Airports default L (longest,
# accepts all drones), keeping legacy environments compatible.
_RUNWAY_RANK = {"S": 1, "M": 2, "L": 3}


def _airport_accepts_drone(airport_runway: str, drone_runway: str) -> bool:
    a = _RUNWAY_RANK.get((airport_runway or "L").upper(), _RUNWAY_RANK["L"])
    d = _RUNWAY_RANK.get((drone_runway or "S").upper(), _RUNWAY_RANK["S"])
    return a >= d


def is_real_airport(a: Dict[str, Any]) -> bool:
    """Return True only for permanent landing fields a drone may use."""
    aid = str(a.get("id", ""))
    if a.get("is_tanker") or a.get("is_synthetic") or a.get("is_checkpoint"):
        return False
    if aid.endswith("_START") or aid.startswith("C") or aid.startswith("TK"):
        return False
    return True
