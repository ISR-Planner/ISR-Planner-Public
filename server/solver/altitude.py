"""Altitude helpers: each drone's cruise altitude and the SAM slice it sees.

All altitudes are km above sea level (docs/architecture/ALTITUDE_MODEL.md).
A SAM is a sphere of radius `range` centred on its site (elevation `alt_km`).
A drone flying level at altitude h sees it as a circle of radius
sqrt(range^2 - (h - alt_km)^2), and not at all once |h - alt_km| >= range.
"""

import copy
import math
from typing import Any, Dict, Optional


def _positive(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def mission_altitude_km(env: Dict[str, Any]) -> float:
    """The mission default cruise altitude (0 = not set: ground-level SAMs)."""
    return _positive((env or {}).get("cruise_altitude_km")) or 0.0


def drone_altitude_km(cfg: Optional[Dict[str, Any]], env: Dict[str, Any]) -> float:
    """A drone's cruise altitude: its own value (0 = ground level), else — for
    configs from before altitude was per drone — the env's mission value."""
    own = (cfg or {}).get("cruise_altitude_km")
    if own is not None and str(own).strip() != "":
        try:
            return max(float(own), 0.0)
        except (TypeError, ValueError):
            pass
    return mission_altitude_km(env)


def primary_altitude_km(drone_configs: Dict[str, Any], env: Dict[str, Any]) -> float:
    """The altitude most enabled drones fly (ties: the lowest). The mission
    matrix is built there, so a mission with one altitude builds one matrix."""
    counts: Dict[float, int] = {}
    for cfg in (drone_configs or {}).values():
        if (cfg or {}).get("enabled") is False:
            continue
        key = altitude_key(drone_altitude_km(cfg, env))
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return mission_altitude_km(env)
    return min(counts, key=lambda k: (-counts[k], k))


def with_primary_altitude(env: Dict[str, Any], drone_configs: Dict[str, Any]):
    """(env, drone_configs) with the env's mission altitude set to the primary
    drone altitude. Drones without their own value first get the env's old
    mission value, so no drone's altitude changes. Unchanged inputs are
    returned as they are."""
    primary = primary_altitude_km(drone_configs, env)
    if altitude_key(primary) == altitude_key(mission_altitude_km(env)):
        return env, drone_configs
    old = mission_altitude_km(env)
    configs = {}
    for did, cfg in (drone_configs or {}).items():
        cfg = dict(cfg or {})
        own = cfg.get("cruise_altitude_km")
        if own is None or str(own).strip() == "":
            cfg["cruise_altitude_km"] = old
        configs[did] = cfg
    return {**env, "cruise_altitude_km": primary}, configs


def altitude_key(altitude_km: float) -> float:
    """Drones whose altitudes round to the same key share one SAM slice/matrix."""
    return round(float(altitude_km or 0.0), 3)


def sam_ground_range_km(sam: Dict[str, Any]) -> Optional[float]:
    r = sam.get("ground_range_km", sam.get("range_km", sam.get("range")))
    return None if r is None else float(r)


def slice_radius_km(
    ground_range_km: float, sam_alt_km: float, altitude_km: float
) -> float:
    """Radius of a SAM's threat sphere at altitude_km (0 = unset: full range)."""
    if altitude_km <= 0:
        return ground_range_km
    dh = altitude_km - float(sam_alt_km or 0.0)
    return math.sqrt(max(ground_range_km * ground_range_km - dh * dh, 0.0))


def slice_sams_at_altitude(env: Dict[str, Any], altitude_km: float) -> Dict[str, Any]:
    """Copy of env whose SAM ranges are their slices at altitude_km.

    Each SAM keeps its full range in `ground_range_km`, so an env can be
    re-sliced at another altitude (per drone) and slicing twice never
    shrinks twice. An env that was never sliced, at altitude 0, is returned
    as is.
    """
    sams = (env or {}).get("sams", [])
    if altitude_km <= 0 and not any("ground_range_km" in s for s in sams):
        return env
    env = copy.deepcopy(env)
    for sam in env.get("sams", []):
        r0 = sam_ground_range_km(sam)
        if r0 is None:
            continue
        sam["ground_range_km"] = r0
        r = slice_radius_km(r0, sam.get("alt_km", 0.0), altitude_km)
        if "range_km" in sam:
            sam["range_km"] = r
        if "range" in sam or "range_km" not in sam:
            sam["range"] = r
    env["sliced_at_altitude_km"] = altitude_key(altitude_km)
    return env


def env_for_drone(env: Dict[str, Any], cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """env with SAMs sliced at this drone's cruise altitude."""
    return slice_sams_at_altitude(env, drone_altitude_km(cfg, env))


# ---------------------------------------------------------------------------
# One distance matrix per altitude
# ---------------------------------------------------------------------------
#
# The mission matrix (built at the mission altitude) is the one every caller
# already passes around. When drones fly other altitudes, it also carries
#   by_altitude:     {altitude key: matrix built with SAMs sliced there}
#   drone_altitudes: {drone id: altitude key}
# and matrix_for_drone() hands each drone its own. With every drone at the
# mission altitude neither field exists, so nothing changes.


def _drone_key(drone_altitudes: Dict[str, str], drone_id: Any) -> Optional[str]:
    did = str(drone_id)
    if did in drone_altitudes:
        return drone_altitudes[did]
    # Virtual legs / sorties ("1_L2", "D1") fly their physical drone's altitude.
    base = did.split("_")[0]
    if base[:1].upper() == "D":
        base = base[1:]
    return drone_altitudes.get(base)


def matrix_for_drone(
    dist_data: Optional[Dict[str, Any]],
    drone_id: Any,
    altitude_km: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """The distance matrix for this drone's cruise altitude.

    With altitude_km (the drone's altitude as it is now), a matrix built for
    another altitude is never returned: None means "no matrix for this
    altitude" (e.g. the altitude changed since the last solve).
    """
    if not dist_data:
        return dist_data
    by_alt = dist_data.get("by_altitude") or {}
    if altitude_km is not None:
        key = str(altitude_key(altitude_km))
        if key in by_alt:
            return by_alt[key]
        built_at = dist_data.get("altitude_key")
        return dist_data if built_at is None or built_at == key else None
    if drone_id is None or not by_alt:
        return dist_data
    drone_key = _drone_key(dist_data.get("drone_altitudes") or {}, drone_id)
    if drone_key is None:
        return dist_data
    return by_alt.get(drone_key, dist_data)


def drone_altitude_keys(
    drone_configs: Dict[str, Any], env: Dict[str, Any]
) -> Dict[str, str]:
    """{drone id: altitude key} for every enabled drone."""
    return {
        str(did): str(altitude_key(drone_altitude_km(cfg, env)))
        for did, cfg in (drone_configs or {}).items()
        if (cfg or {}).get("enabled") is not False
    }


def attach_altitude_matrices(
    mission_matrix: Dict[str, Any],
    env: Dict[str, Any],
    drone_configs: Dict[str, Any],
    build_matrix,
) -> Dict[str, Any]:
    """Give mission_matrix a matrix for every other altitude a drone flies.

    build_matrix(env) builds a SAM-aware matrix for env at
    env["cruise_altitude_km"]. Returns mission_matrix unchanged when every
    drone flies the mission altitude.
    """
    mission_key = str(altitude_key(mission_altitude_km(env)))
    mission_matrix["altitude_key"] = mission_key
    drone_keys = drone_altitude_keys(drone_configs, env)
    others = sorted({k for k in drone_keys.values() if k != mission_key}, key=float)
    if not others:
        return mission_matrix
    by_alt = {mission_key: mission_matrix}
    for key in others:
        by_alt[key] = build_matrix({**env, "cruise_altitude_km": float(key)})
        by_alt[key]["altitude_key"] = key
    result = dict(mission_matrix)
    result["altitude_key"] = mission_key
    result["by_altitude"] = by_alt
    result["drone_altitudes"] = drone_keys
    return result


def altitude_signature(drone_configs: Dict[str, Any], env: Dict[str, Any]) -> str:
    """Changes whenever any drone's altitude changes (for matrix caching)."""
    keys = drone_altitude_keys(drone_configs, env)
    return ",".join(f"{d}={k}" for d, k in sorted(keys.items()))


def drone_config_for(drone_configs: Dict[str, Any], drone_id: Any) -> Dict[str, Any]:
    """A drone's config; virtual legs ("1_L2") fall back to their physical drone."""
    configs = drone_configs or {}
    did = str(drone_id)
    if did in configs:
        return configs[did] or {}
    base = did.split("_")[0]
    if base[:1].upper() == "D":
        base = base[1:]
    return configs.get(base) or {}


def sams_for_drone(
    env: Dict[str, Any], drone_configs: Dict[str, Any], drone_id: Any
) -> list:
    """env's SAMs sliced at this drone's cruise altitude."""
    cfg = drone_config_for(drone_configs, drone_id)
    return env_for_drone(env, cfg).get("sams", [])


# ---------------------------------------------------------------------------
# Warnings (docs/architecture/ALTITUDE_MODEL.md §5)
# ---------------------------------------------------------------------------


def altitude_warnings(
    env: Dict[str, Any],
    drone_configs: Dict[str, Any],
    routes: Dict[str, Any],
) -> list:
    """A drone whose cruise altitude is below the ground at a place it visits.

    Only for drones with an altitude set (> 0); ground heights are the items'
    alt_km (terrain, filled by the browser). Tankers are in the air, skipped.
    """
    ground: Dict[str, float] = {}
    for a in (env or {}).get("airports", []):
        if (
            a.get("type") != "tanker"
            and not a.get("is_tanker")
            and a.get("alt_km") is not None
        ):
            ground[str(a.get("id"))] = float(a["alt_km"])
    for t in (env or {}).get("targets", []):
        if t.get("alt_km") is not None:
            ground[str(t.get("id"))] = float(t["alt_km"])

    warnings = []
    for did in sorted((routes or {}).keys(), key=str):
        cfg = drone_config_for(drone_configs, did)
        if cfg.get("enabled") is False:
            continue
        h = drone_altitude_km(cfg, env)
        ceiling = cfg.get("ceiling_km")
        if ceiling and h > float(ceiling) + 1e-9:
            name = cfg.get("platform_id") or "its aircraft"
            warnings.append(
                f"D{did} cruises at {h:g} km, above {name}'s {float(ceiling):g} km ceiling"
            )
        if h <= 0:
            continue
        route = (routes[did] or {}).get("route") or []
        below = [
            f"{wp} ({ground[str(wp)]:.2f} km)"
            for wp in dict.fromkeys(str(w) for w in route)
            if str(wp) in ground and ground[str(wp)] > h
        ]
        if below:
            warnings.append(
                f"D{did} cruises at {h:.2f} km, below the ground at " + ", ".join(below)
            )
        # Flight-profile warnings (climbs that can't finish, SAMs reached while
        # climbing/descending) — server/solver/flight_profile.py.
        warnings.extend(
            ((routes[did] or {}).get("anim_frame") or {}).get("profile_warnings") or []
        )
    return warnings
