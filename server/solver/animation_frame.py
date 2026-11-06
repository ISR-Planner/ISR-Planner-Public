"""Authoritative animation frame — ONE source of truth for the playback distance.

The frontend animation advances a single scalar `distanceTraveled` (km) along a
per-trajectory-point `cumulativeDistances` array and draws the drone by lerping
along `trajectory`. Historically the frontend BUILT that frame itself, in five
sequential mutations (matrix profile -> densify -> loiter rebuild -> airport
bubble-scan -> refuel-crossing leg split), each by a different method. They
disagreed: speed jumped between segments, per-leg fuel was wrong on routes that
revisit an airport, waypoint_indices went stale after densification, and stats
counted animation-detected visits instead of the planned route.

This module computes the frame ONCE, server-side, so every consumer (position,
fuel, visits, leg boundaries, stats) reads the same matrix-framed numbers:

  build_animation_frame(route, trajectory, waypoint_indices, leg_distances)
    -> {trajectory, cumulative_distances, waypoint_indices, total_distance,
        leg_breaks}

  * trajectory: DENSIFIED to ~200 points (uniform km spacing) so playback is
    smooth and the visual speed is the same across segments. Every original
    vertex (= a route waypoint, incl. targets) is preserved exactly.
  * cumulative_distances: km at each densified point, in the AUTHORITATIVE leg
    frame (the per-leg SAM-aware distances), aligned 1:1 with trajectory.
  * waypoint_indices: route-index -> densified-trajectory-index, rebuilt in
    LOCKSTEP with the densified trajectory (never stale).
  * leg_breaks: cumulative km at each refuel point (from waypoint_indices), so
    the player schedules refuel pauses + per-leg fuel without bubble-matching.
  * wait_distances: optional per-waypoint distance-equivalent wait fuel. These
    shift the waypoint anchor and all downstream anchors so playback reaches
    synchronized targets at the scheduled time.

Geometry is haversine over lat/lng (the trajectory is lat/lng at this point).
Per-leg lengths come from `leg_distances` (the solver's SAM-aware per-leg
values); within a leg, points are spaced by their haversine fraction SCALED so
the leg's drawn length equals the solver leg distance. No straight-line vs
matrix disagreement: the anchors ARE the solver numbers.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

# ~200-point target density for the densified trajectory (smooth playback).
_DENSIFY_TARGET_POINTS = 200


def _haversine_km(a: List[float], b: List[float]) -> float:
    R = 6371.0088
    p1 = math.radians(a[0])
    p2 = math.radians(b[0])
    dp = math.radians(b[0] - a[0])
    dl = math.radians(b[1] - a[1])
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def _geodesic_interpolate(
    lat1: float, lng1: float, lat2: float, lng2: float, t: float
) -> List[float]:
    """Interpolate along the great circle (geodesic) between two points.

    Args:
        lat1, lng1: Start point in degrees
        lat2, lng2: End point in degrees
        t: Interpolation fraction (0 = start, 1 = end)

    Returns:
        [lat, lng] of the intermediate point on the great circle
    """
    phi1 = math.radians(lat1)
    lam1 = math.radians(lng1)
    phi2 = math.radians(lat2)
    lam2 = math.radians(lng2)

    # Angular distance between points
    d = 2 * math.asin(
        math.sqrt(
            math.sin((phi2 - phi1) / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin((lam2 - lam1) / 2) ** 2
        )
    )

    if d < 1e-10:
        return [lat1, lng1]  # Points are essentially the same

    # Spherical interpolation (slerp)
    a = math.sin((1 - t) * d) / math.sin(d)
    b = math.sin(t * d) / math.sin(d)

    x = a * math.cos(phi1) * math.cos(lam1) + b * math.cos(phi2) * math.cos(lam2)
    y = a * math.cos(phi1) * math.sin(lam1) + b * math.cos(phi2) * math.sin(lam2)
    z = a * math.sin(phi1) + b * math.sin(phi2)

    lat = math.degrees(math.atan2(z, math.sqrt(x * x + y * y)))
    lng = math.degrees(math.atan2(y, x))
    return [lat, lng]


def _wp_traj_index(waypoint_indices: Dict[Any, int], route_idx: int) -> Optional[int]:
    """waypoint_indices may be keyed by int or str (JSON). Look up either."""
    if waypoint_indices is None:
        return None
    if route_idx in waypoint_indices:
        return int(waypoint_indices[route_idx])
    s = str(route_idx)
    if s in waypoint_indices:
        return int(waypoint_indices[s])
    return None


def _required_wp_traj_index(waypoint_indices: Dict[Any, int], route_idx: int) -> int:
    traj_idx = _wp_traj_index(waypoint_indices, route_idx)
    if traj_idx is None:
        raise ValueError(f"missing trajectory index for route waypoint {route_idx}")
    return traj_idx


def _anchor_cum_distances(
    route: List[str],
    waypoint_indices: Dict[Any, int],
    leg_distances: Optional[List[float]],
    trajectory: List[List[float]],
    refuel_indices: Optional[List[int]] = None,
    wait_distances: Optional[Dict[str, float]] = None,
) -> Optional[List[float]]:
    """Cumulative km at each route waypoint (the anchors of the frame).

    Prefer the solver's per-leg `leg_distances` (SAM-aware, authoritative). A
    "leg" here is the span between consecutive route waypoints that the solver
    measured. When leg_distances isn't usable (count mismatch / absent), fall
    back to haversine between the waypoints' trajectory anchors so the frame is
    still monotonic and complete (degrade, never crash).

    Returns a list aligned with `route` (one cum value per waypoint), or None if
    anchors can't be resolved.
    """
    n = len(route)
    if n < 2:
        return None
    waits: Dict[str, float] = {}
    for key, value in (wait_distances or {}).items():
        try:
            wait = float(value)
        except (TypeError, ValueError):
            continue
        if wait > 0:
            waits[str(key)] = wait

    # Resolve each waypoint's trajectory index; all must be present + ordered.
    anchor_traj_idx: List[Optional[int]] = [
        _wp_traj_index(waypoint_indices, i) for i in range(n)
    ]
    anchor_indices: List[int] = []
    for traj_idx in anchor_traj_idx:
        if traj_idx is None:
            return None
        anchor_indices.append(traj_idx)

    # Geometric length of each waypoint span, measured ALONG the trajectory
    # points between the two waypoint anchors (so SAM detours are included — a
    # straight anchor-to-anchor haversine would undercount a curved leg).
    geo_span = [0.0]
    for i in range(1, n):
        a, b = anchor_indices[i - 1], anchor_indices[i]
        d = 0.0
        for j in range(a + 1, b + 1):
            d += _haversine_km(trajectory[j - 1], trajectory[j])
        geo_span.append(d)  # geo_span[i] = length of span (i-1 -> i)

    refuel_set = sorted(int(r) for r in (refuel_indices or []) if 0 < int(r) < n - 1)

    if leg_distances and refuel_set and len(leg_distances) == len(refuel_set) + 1:
        # Per-LEG distances (refuel-to-refuel): leg boundaries are the refuel
        # waypoints. Within each leg, distribute the per-leg solver distance over
        # its spans by their geometric (SAM-aware) fraction, so each waypoint's
        # cumulative value is exact AND each leg sums to the solver's number.
        bounds = [0] + refuel_set + [n - 1]
        cum = [0.0] * n
        running = 0.0
        for li in range(len(bounds) - 1):
            a, b = bounds[li], bounds[li + 1]
            leg_total = float(leg_distances[li])
            geo_leg = sum(geo_span[a + 1 : b + 1]) or 1.0
            scale = leg_total / geo_leg
            for i in range(a + 1, b + 1):
                running += geo_span[i] * scale
                running += waits.get(str(route[i]), 0.0)
                cum[i] = running
        return cum

    if leg_distances and len(leg_distances) == n - 1:
        # Per-SPAN distances — use directly.
        cum = [0.0]
        for i, d in enumerate(leg_distances, start=1):
            cum.append(cum[-1] + float(d) + waits.get(str(route[i]), 0.0))
        return cum

    # Fallback: geometric (SAM-aware along-trajectory) span lengths.
    cum = [0.0]
    for i in range(1, n):
        cum.append(cum[-1] + geo_span[i] + waits.get(str(route[i]), 0.0))
    return cum


def build_animation_frame(
    route: List[str],
    trajectory: List[List[float]],
    waypoint_indices: Dict[Any, int],
    leg_distances: Optional[List[float]] = None,
    refuel_indices: Optional[List[int]] = None,
    wait_distances: Optional[Dict[str, float]] = None,
    densify_points: int = _DENSIFY_TARGET_POINTS,
    hold_distances: Optional[Dict[int, float]] = None,
) -> Dict[str, Any]:
    """Build the authoritative, densified animation frame for one route.

    Args:
        route: ordered waypoint ids.
        trajectory: SAM-aware [lat,lng] points (sparse — vertices + SAM bends).
        waypoint_indices: route-index -> trajectory-index (for the sparse traj).
        leg_distances: solver's per-waypoint-span SAM-aware distances (optional).
        refuel_indices: route indices where the drone refuels (optional).
        wait_distances: distance-equivalent waits keyed by waypoint id (optional).
            Spread over the approach to that waypoint (time-on-target sync
            never hovers).
        densify_points: minimum point count for the densified trajectory.
        hold_distances: distance-equivalent HOLDS keyed by route index
            (loiter): the drone stays at that waypoint while the distance
            advances — the waypoint's point appears twice, the second one
            `hold` km later. leg_distances must then be travel only.

    Returns dict:
        trajectory: densified [lat,lng] points.
        cumulative_distances: km per densified point (matrix/leg frame).
        waypoint_indices: route-index -> DENSIFIED trajectory-index (lockstep).
        total_distance: cumulative_distances[-1].
        leg_breaks: km at each refuel waypoint.
    """
    traj = [list(p) for p in (trajectory or [])]
    n_traj = len(traj)
    if n_traj < 2:
        return {
            "trajectory": traj,
            "cumulative_distances": [0.0] * n_traj,
            "waypoint_indices": dict(waypoint_indices or {}),
            "total_distance": 0.0,
            "leg_breaks": [],
        }

    # 1) Anchor cumulative distances at each route waypoint (solver leg frame).
    anchor_cum = _anchor_cum_distances(
        route, waypoint_indices, leg_distances, traj, refuel_indices, wait_distances
    )

    # Sparse-trajectory cumulative distance at every point, anchored at the
    # waypoints and haversine-interpolated (scaled) within each span. This mirrors
    # buildTrajectoryCumDistFromProfile (latlng_utils.js) but server-side.
    sparse_cum = [0.0] * n_traj
    if anchor_cum is not None:
        anchors = [
            (_required_wp_traj_index(waypoint_indices, i), anchor_cum[i])
            for i in range(len(route))
        ]
        # Fill before the first anchor.
        for i in range(0, anchors[0][0]):
            sparse_cum[i] = anchors[0][1]
        for k in range(len(anchors) - 1):
            ai, ac = anchors[k]
            bi, bc = anchors[k + 1]
            span = bc - ac
            raw = 0.0
            raw_cum = [0.0]
            for i in range(ai + 1, bi + 1):
                raw += _haversine_km(traj[i - 1], traj[i])
                raw_cum.append(raw)
            scale = (span / raw) if raw > 0 else 0.0
            r = 0
            for i in range(ai, bi + 1):
                sparse_cum[i] = ac + raw_cum[r] * scale
                r += 1
        # Fill after the last anchor.
        last_i, last_c = anchors[-1]
        for i in range(last_i + 1, n_traj):
            sparse_cum[i] = last_c
    else:
        # No anchors — pure haversine.
        for i in range(1, n_traj):
            sparse_cum[i] = sparse_cum[i - 1] + _haversine_km(traj[i - 1], traj[i])

    total = sparse_cum[-1]

    # 2) Densify: insert points so spacing is ~uniform (smooth, segment-stable
    #    speed), preserving every original vertex (targets/airports) exactly and
    #    remapping waypoint_indices to the densified indices in lockstep.
    step_km = total / max(densify_points, n_traj) if total > 0 else 0.0
    dense: List[List[float]] = [traj[0]]
    dense_cum: List[float] = [sparse_cum[0]]
    # old-trajectory-index -> new densified index (built as we go).
    old_to_new: Dict[int, int] = {0: 0}

    for i in range(1, n_traj):
        seg_start = sparse_cum[i - 1]
        seg_dist = sparse_cum[i] - seg_start
        if step_km > 0 and seg_dist > step_km:
            steps = int(seg_dist // step_km)
            for s in range(1, steps):
                t = (s * step_km) / seg_dist
                # Use geodesic interpolation for accurate great-circle paths
                pt = _geodesic_interpolate(
                    traj[i - 1][0], traj[i - 1][1], traj[i][0], traj[i][1], t
                )
                dense.append(pt)
                dense_cum.append(seg_start + s * step_km)
        # Always keep the original vertex i.
        dense.append(traj[i])
        dense_cum.append(sparse_cum[i])
        old_to_new[i] = len(dense) - 1

    # Remap waypoint_indices to densified indices (lockstep — never stale).
    new_wpi: Dict[int, int] = {}
    for k in range(len(route)):
        old_ti = _wp_traj_index(waypoint_indices, k)
        if old_ti is not None and old_ti in old_to_new:
            new_wpi[k] = old_to_new[old_ti]

    # 2b) Holds (loiter): repeat the waypoint's point, the distance advancing by
    #     the hold while the drone stays put; later points shift by it.
    holds = sorted(
        (int(k), float(v))
        for k, v in (hold_distances or {}).items()
        if float(v or 0) > 0
    )
    for k, hold in reversed(holds):
        p = new_wpi.get(k)
        if p is None:
            continue
        dense.insert(p + 1, list(dense[p]))
        dense_cum.insert(p + 1, dense_cum[p])
        for i in range(p + 1, len(dense_cum)):
            dense_cum[i] += hold
        for kk, idx in new_wpi.items():
            if idx > p:
                new_wpi[kk] = idx + 1

    # 3) Leg breaks = cumulative km at each refuel waypoint (densified frame).
    leg_breaks: List[float] = []
    for ridx in refuel_indices or []:
        if ridx in new_wpi:
            leg_breaks.append(dense_cum[new_wpi[ridx]])

    return {
        "trajectory": dense,
        "cumulative_distances": dense_cum,
        "waypoint_indices": new_wpi,
        "total_distance": dense_cum[-1] if dense_cum else 0.0,
        "leg_breaks": leg_breaks,
    }


def loiter_holds(
    route: List[str], env: Dict[str, Any], cfg: Optional[Dict[str, Any]]
) -> Dict[int, float]:
    """{route index: loiter fuel (km)} for each target the drone loiters at."""
    from .optimizers.common import compute_route_loiter_fuel

    targets = {str(t.get("id")): t for t in (env or {}).get("targets", []) or []}
    holds: Dict[int, float] = {}
    for i, wp in enumerate(route or []):
        fuel = compute_route_loiter_fuel([wp], cfg or {}, targets)
        if fuel > 0:
            holds[i] = fuel
    return holds


def travel_leg_distances(
    leg_distances: Optional[List[float]],
    route: List[str],
    refuel_indices: Optional[List[int]],
    holds: Dict[int, float],
) -> Optional[List[float]]:
    """The solver's per-span / per-leg FUEL distances with the loiter holds
    taken out (the solver counts a target's loiter on the hop that reaches it),
    so the frame measures flying only and loiter appears once, as a hold."""
    if not leg_distances or not holds:
        return leg_distances
    n = len(route)
    legs = [float(d) for d in leg_distances]
    if len(legs) == n - 1:  # per span: span k = route[k] -> route[k+1]
        return [d - holds.get(k + 1, 0.0) for k, d in enumerate(legs)]
    refuels = sorted(int(r) for r in (refuel_indices or []) if 0 < int(r) < n - 1)
    if len(legs) == len(refuels) + 1:  # per leg, refuel to refuel
        bounds = [0] + refuels + [n - 1]
        return [
            legs[j]
            - sum(holds.get(i, 0.0) for i in range(bounds[j] + 1, bounds[j + 1] + 1))
            for j in range(len(legs))
        ]
    return leg_distances
