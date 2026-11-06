"""Flight profile: the altitude of every point along a drone's path (phase C).

docs/architecture/ALTITUDE_MODEL.md §3. Computed once, on the animation frame
(the densified path the animation and the 3D view use), so every consumer reads
the same altitudes.

The drone wants to fly at its cruise altitude, but some points fix its altitude:
  - take-off: the start airport's ground elevation,
  - landing: the end airport's ground elevation,
  - an airport refuel: that airport's ground (it lands),
  - a tanker refuel: the tanker's holding altitude.
It climbs/descends only at its climb angle g = tan(angle). So at distance s:

  altitude(s) = cruise, held within  a_j - g|s - s_j| <= altitude <= a_j + g|s - s_j|
                for every fixed point j (altitude a_j at distance s_j).

That one rule gives the climb-out, the descent to land, the change to and from
a tanker's altitude, and a peak below cruise on a leg too short to reach it.
Fuel stays horizontal distance (decided); altitude is drawn, never planned on.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

from .altitude import drone_altitude_km, sam_ground_range_km, slice_radius_km

DEFAULT_CLIMB_ANGLE_DEG = 12.0

# A drone may use a tanker only within this much of its cruise altitude: it must
# change altitude to meet the tanker and back, which takes dh / tan(angle) of
# horizontal distance each way (2 km at 12 deg = 9.4 km) — longer changes don't
# fit these missions' legs, and every km of change is time at an altitude the
# route was not planned for (SAM exposure).
MAX_TANKER_ALTITUDE_CHANGE_KM = 2.0

_CHECKPOINT = re.compile(r"^C\d+-\d+$")


def _angle(cfg: Optional[Dict[str, Any]], key: str, default: float) -> float:
    try:
        v = float((cfg or {}).get(key) or 0)
    except (TypeError, ValueError):
        v = 0.0
    return min(max(v, 1.0), 89.0) if v > 0 else default


def climb_angle_deg(cfg: Optional[Dict[str, Any]]) -> float:
    return _angle(cfg, "climb_angle_deg", DEFAULT_CLIMB_ANGLE_DEG)


def descent_angle_deg(cfg: Optional[Dict[str, Any]]) -> float:
    """The drone's descent angle; the climb angle when it has none of its own."""
    return _angle(cfg, "descent_angle_deg", climb_angle_deg(cfg))


def _is_tanker(a: Dict[str, Any]) -> bool:
    return a.get("type") == "tanker" or bool(a.get("is_tanker"))


def tanker_usable(
    tanker: Dict[str, Any], cfg: Optional[Dict[str, Any]], env: Dict[str, Any]
) -> bool:
    """A drone can refuel at a tanker within MAX_TANKER_ALTITUDE_CHANGE_KM of its
    cruise altitude and not above its ceiling. A tanker without an altitude is
    taken to be at cruise."""
    if tanker.get("alt_km") is None:
        return True
    ceiling = (cfg or {}).get("ceiling_km")
    if ceiling and float(tanker["alt_km"]) > float(ceiling) + 1e-9:
        return False  # the drone can't climb that high
    dh = abs(float(tanker["alt_km"]) - drone_altitude_km(cfg, env))
    return dh <= MAX_TANKER_ALTITUDE_CHANGE_KM + 1e-9


def _fixed_points(
    route: List[str],
    env: Dict[str, Any],
    cfg: Dict[str, Any],
    refuel_indices: Optional[List[int]],
) -> Dict[int, Tuple[float, str]]:
    """route index -> (altitude km, why) for points whose altitude is fixed."""
    airports = {str(a.get("id")): a for a in env.get("airports", []) or []}
    cruise = drone_altitude_km(cfg, env)
    fixed: Dict[int, Tuple[float, str]] = {}

    def ground(wid: str) -> Optional[float]:
        a = airports.get(str(wid))
        if a is None or _is_tanker(a):
            return None
        return float(a.get("alt_km") or 0.0)

    if route:
        # A route starting at a cut node / synthetic start is already airborne.
        first = str(route[0])
        g = ground(first)
        if g is not None and not _CHECKPOINT.match(first):
            fixed[0] = (g, f"take-off at {first}")
        last = str(route[-1])
        g = ground(last)
        if g is not None and len(route) > 1:
            fixed[len(route) - 1] = (g, f"landing at {last}")
    if refuel_indices is None:
        # An explicit route (operator / agent edit): an airport or tanker between
        # its ends is a refuel stop.
        refuel_indices = [
            i for i in range(1, len(route) - 1) if str(route[i]) in airports
        ]
    for i in refuel_indices:
        if not 0 < i < len(route) - 1:
            continue
        wid = str(route[i])
        a = airports.get(wid)
        if a is None:
            continue
        if _is_tanker(a):
            alt = float(a["alt_km"]) if a.get("alt_km") is not None else cruise
            fixed[i] = (alt, f"refuel at tanker {wid}")
        else:
            fixed[i] = (float(a.get("alt_km") or 0.0), f"refuel at {wid}")
    return fixed


def _wp_index(waypoint_indices: Dict[Any, int], k: int) -> Optional[int]:
    v = waypoint_indices.get(k, waypoint_indices.get(str(k)))
    return None if v is None else int(v)


def _haversine_km(a, b) -> float:
    R = 6371.0088
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def attach_flight_profile(
    frame: Dict[str, Any],
    route: List[str],
    env: Dict[str, Any],
    cfg: Optional[Dict[str, Any]],
    refuel_indices: Optional[List[int]] = None,
    drone_id: Any = "",
) -> List[str]:
    """Add frame["altitudes"] (km, one per frame point) and
    frame["profile_warnings"]; returns the warnings.

    The frame is build_animation_frame()'s output (lat/lng trajectory,
    cumulative_distances, waypoint_indices).
    """
    cfg = cfg or {}
    traj = frame.get("trajectory") or []
    s = frame.get("cumulative_distances") or []
    wpi = frame.get("waypoint_indices") or {}
    warnings: List[str] = []
    if len(traj) < 2 or len(s) != len(traj):
        frame["altitudes"] = [drone_altitude_km(cfg, env)] * len(traj)
        frame["profile_warnings"] = warnings
        return warnings

    cruise = drone_altitude_km(cfg, env)
    up = math.tan(math.radians(climb_angle_deg(cfg)))
    down = math.tan(math.radians(descent_angle_deg(cfg)))
    # Climbs and descents are measured on GROUND distance along the path — not
    # on the playback distance, which also advances during a loiter hold and
    # stretches for a time-on-target delay.
    ground = [0.0]
    for i in range(1, len(traj)):
        ground.append(ground[-1] + _haversine_km(traj[i - 1], traj[i]))
    anchors = []  # (ground km, altitude, why)
    for k, (alt, why) in sorted(_fixed_points(route, env, cfg, refuel_indices).items()):
        idx = _wp_index(wpi, k)
        if idx is not None and 0 <= idx < len(ground):
            anchors.append((ground[idx], alt, why))

    if cruise <= 0 and anchors:
        # 0 = "ground level": stay at the airports' height, never below it.
        cruise = max(a for _, a, _ in anchors)

    alts: List[float] = []
    conflict = False
    for si in ground:
        # Leaving a fixed point (si >= sj) the drone climbs at `up` / descends at
        # `down`; arriving (si < sj) it must be descending at `down` from above
        # or climbing at `up` from below.
        lo = max(
            (a - (down if si >= sj else up) * abs(si - sj) for sj, a, _ in anchors),
            default=-math.inf,
        )
        hi = min(
            (a + (up if si >= sj else down) * abs(si - sj) for sj, a, _ in anchors),
            default=math.inf,
        )
        if lo > hi + 1e-9:  # can't meet both fixed points at this angle
            conflict = True
            alts.append((lo + hi) / 2)
        else:
            alts.append(min(max(cruise, lo), hi))
    frame["altitudes"] = alts
    label = f"D{drone_id}" if drone_id not in (None, "") else "Drone"

    # Warnings -------------------------------------------------------------
    angles = f"{climb_angle_deg(cfg):g}° climb / {descent_angle_deg(cfg):g}° descent"
    if conflict:
        warnings.append(
            f"{label} can't change altitude in time at its {angles} angles "
            "between two of its take-off / refuel / landing points"
        )
    # A stretch between fixed points that never reaches cruise (short leg).
    for (s0, a0, w0), (s1, a1, w1) in zip(anchors, anchors[1:]):
        peak = max(
            (alt for si, alt in zip(ground, alts) if s0 <= si <= s1), default=cruise
        )
        if min(a0, a1) < cruise - 1e-6 and peak < cruise - 0.05:
            warnings.append(
                f"{label} only reaches {peak:.2f} km (cruise {cruise:g} km) "
                f"between {w0} and {w1}: the leg is too short at its {angles} angles"
            )
    # Below cruise (climbing/descending), a SAM's slice is different: warn when
    # a transition point is inside one (the route was planned at cruise).
    hit = set()
    for sam in env.get("sams", []) or []:
        c = sam.get("center") or {}
        if "lat" not in c or "lng" not in c:
            continue
        r0 = sam_ground_range_km(sam)
        if not r0:
            continue
        e = float(sam.get("alt_km") or 0.0)
        name = sam.get("id") or f"SAM at {c['lat']:.3f},{c['lng']:.3f}"
        planned = slice_radius_km(r0, e, cruise)
        for p, alt in zip(traj, alts):
            if abs(alt - cruise) < 1e-6:
                continue
            d = _haversine_km(p, (c["lat"], c["lng"]))
            if (
                d < slice_radius_km(r0, e, max(alt, 1e-6)) - 1e-6
                and d >= planned - 1e-6
            ):
                hit.add(str(name))
                break
    for name in sorted(hit):
        warnings.append(
            f"{label} passes within reach of {name} while climbing or descending "
            "(the route was planned at its cruise altitude)"
        )
    frame["profile_warnings"] = warnings
    return warnings


def without_unreachable_tankers(
    env: Dict[str, Any], drone_configs: Dict[str, Any]
) -> Dict[str, Any]:
    """drone_configs where each drone may only refuel at tankers within
    MAX_TANKER_ALTITUDE_CHANGE_KM of its cruise altitude (others are dropped
    from refuel_options / refuel_stops / refuel_airport). Unchanged configs
    are returned as they are."""
    tankers = {
        str(a.get("id")): a
        for a in (env or {}).get("airports", []) or []
        if _is_tanker(a)
    }
    if not tankers:
        return drone_configs
    out, changed = {}, False
    for did, cfg in (drone_configs or {}).items():
        cfg = cfg or {}
        far = {tid for tid, t in tankers.items() if not tanker_usable(t, cfg, env)}
        if not far:
            out[did] = cfg
            continue
        new = dict(cfg)
        for key in ("refuel_options", "refuel_stops"):
            if isinstance(cfg.get(key), list):
                new[key] = [s for s in cfg[key] if str(s) not in far]
        if str(cfg.get("refuel_airport") or "") in far:
            new["refuel_airport"] = ""
        changed = changed or new != cfg
        out[did] = new
    return out if changed else drone_configs
