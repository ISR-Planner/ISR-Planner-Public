"""Assess weather changes against remaining flight paths and missed targets."""

import json
from datetime import datetime

from path_planning_core.boundary_navigation import (
    _is_point_on_polygon_boundary,
    _point_in_polygon,
)
from path_planning_core.latlng_utils import (
    latlng_to_local_xy,
    plane_dist,
    plane_seg_dist,
)
from path_planning_core.sam_wrapping import wrap_sams

from .projection import _project_env_to_local_km
from .weather import segment_enters
from .weather_geometry import project_weather_ring


def epoch(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def window_cells(cells, start, end):
    return [
        c
        for c in cells
        if c["rule"] == "No fly"
        and (
            start <= epoch(c["validAt"]) <= end
            if c.get("validAt")
            else epoch(c["validFrom"]) < end and epoch(c["validTo"]) > start
        )
    ]


def polygons(cells, lat, lng):
    result = []
    for cell in cells:
        parts = (
            [cell["geometry"]["coordinates"]]
            if cell["geometry"]["type"] == "Polygon"
            else cell["geometry"]["coordinates"]
        )
        for part in parts:
            polygon = project_weather_ring(part[0], lat, lng)
            if polygon:
                result.append(polygon)
    return result


def _leg_bends(points):
    """Remove duplicate holds and collinear playback samples, keeping real bends.

    The mission plane maps each great-circle leg to a straight line. A one
    millimetre tolerance removes floating-point densification noise, not weather
    avoidance arcs. Required stops are already separate legs supplied by the
    authoritative playback frame; never simplify across those stops.
    """
    bends = []
    for point in points:
        if bends and plane_dist(bends[-1], point) <= 1e-6:
            continue
        while len(bends) >= 2 and plane_seg_dist(bends[-1], bends[-2], point) <= 1e-6:
            bends.pop()
        bends.append(point)
    return bends


def _shortcut_pairs(count):
    """Local and progressively wider shortcuts, O(bends * log(bends)).

    Test nearby bend subpaths for partial weather releases and always consider
    the rest of the leg. The wider spans keep long arcs useful without a dense
    animation trajectory's all-pairs quadratic search.
    """
    spans = list(range(2, min(9, count)))
    span = 16
    while span < count:
        spans.append(span)
        span *= 2
    for start in range(count - 2):
        ends = {min(count - 1, start + span) for span in spans}
        ends.add(count - 1)
        for end in sorted(ends, reverse=True):
            yield start, end


def _released_detours(remaining_legs, removed, current, projected_env, lat, lng):
    if not remaining_legs or not removed:
        return []
    # Keep permanent SAM constraints as well as current weather. Full SAM
    # ranges are conservative when a per-drone altitude is absent from this
    # assessment request. Wrapped clusters also preserve the router's bridges
    # between overlapping circles.
    sams = []
    for sam in projected_env.get("sams", []):
        if sam.get("weather_ring"):
            continue
        pos = sam.get("pos", sam.get("position"))
        if pos is None and "x" in sam and "y" in sam:
            pos = [sam["x"], sam["y"]]
        if pos is not None:
            sams.append(
                {
                    "x": float(pos[0]),
                    "y": float(pos[1]),
                    "range": float(sam.get("range", sam.get("radius", 15))),
                    "avoidance_polygon": sam.get("avoidance_polygon"),
                }
            )
    persistent, _ = wrap_sams(sams)
    blockers = current + persistent
    released = []
    for did, legs in remaining_legs.items():
        found = False
        for leg in legs:
            points = _leg_bends([latlng_to_local_xy(p[0], p[1], lat, lng) for p in leg])
            distances = [0.0]
            for a, b in zip(points, points[1:]):
                distances.append(distances[-1] + plane_dist(a, b))
            for i, j in _shortcut_pairs(len(points)):
                a, b = points[i], points[j]
                old_distance = distances[j] - distances[i]
                saving = old_distance - plane_dist(a, b)
                # Do not interrupt for numerical noise or insignificant bends.
                if saving < max(0.1, old_distance * 1e-4):
                    continue
                if not any(segment_enters(a, b, poly) for poly in removed):
                    continue
                if any(segment_enters(a, b, poly) for poly in blockers):
                    continue
                if any(
                    sam["range"] > 0
                    and plane_seg_dist((sam["x"], sam["y"]), a, b) <= sam["range"]
                    for sam in sams
                ):
                    continue
                found = True
                break
            if found:
                released.append(str(did))
                break
    return released


def assess_impact(
    env,
    remaining_routes,
    missed_targets,
    previous,
    current,
    start,
    end,
    retrieved_at,
    remaining_legs=None,
):
    projected_env, lat, lng = _project_env_to_local_km(env)
    new = window_cells(current, start, end)

    def key(c):
        return json.dumps(c["geometry"], sort_keys=True)

    # Renewal of a known footprint is not a new obstacle: the accepted plan
    # already avoided it. Compare geometry rather than issue/expiry timestamps.
    old_keys = {key(c) for c in previous if c["rule"] == "No fly"}
    added = polygons([c for c in new if key(c) not in old_keys], lat, lng)
    reasons = []
    for did, path in remaining_routes.items():
        points = [latlng_to_local_xy(p[0], p[1], lat, lng) for p in path]
        if any(
            segment_enters(a, b, poly)
            for a, b in zip(points, points[1:])
            for poly in added
        ) or any(
            _point_in_polygon(p, poly) and not _is_point_on_polygon_boundary(p, poly)
            for p in points
            for poly in added
        ):
            reasons.append(
                f"New or changed No fly weather affects D{did} remaining route."
            )
    # Include expired polygons in the baseline: these may have caused the
    # original solve to exclude a target, but expiry alone is not clearance.
    baseline = polygons([c for c in previous if c["rule"] == "No fly"], lat, lng)
    current_polys = polygons(new, lat, lng)
    current_keys = {key(c) for c in new}
    removed = polygons(
        [c for c in previous if c["rule"] == "No fly" and key(c) not in current_keys],
        lat,
        lng,
    )
    released = _released_detours(
        {
            did: legs
            for did, legs in (remaining_legs or {}).items()
            if did in remaining_routes
        },
        removed,
        current_polys,
        projected_env,
        lat,
        lng,
    )
    recovered = []
    for t in missed_targets:
        p = latlng_to_local_xy(t["lat"], t["lng"], lat, lng)
        if any(_point_in_polygon(p, poly) for poly in baseline) and not any(
            _point_in_polygon(p, poly) for poly in current_polys
        ):
            recovered.append(t["id"])
    # Releasing a target is a reason to reconsider the plan even when playback
    # is ahead of observations. A reviewable draft is not confirmed clearance.
    confirmed_current = start <= epoch(retrieved_at) + 60
    notices = []
    if recovered:
        reasons.append(
            "Previously weather-blocked targets need reassessment: "
            + ", ".join(recovered)
        )
    if released:
        reasons.append(
            "Changed No fly weather permits a shorter remaining detour for "
            + ", ".join("D" + did for did in released)
            + "; reassess the route."
        )
    expired = polygons(
        [
            c
            for c in previous
            if c["rule"] == "No fly"
            and c.get("validTo")
            and epoch(c["validTo"]) <= start
        ],
        lat,
        lng,
    )
    route_clearance_unconfirmed = not confirmed_current and any(
        segment_enters(a, b, poly)
        for path in remaining_routes.values()
        for a, b in zip(
            [latlng_to_local_xy(p[0], p[1], lat, lng) for p in path],
            [latlng_to_local_xy(p[0], p[1], lat, lng) for p in path][1:],
        )
        for poly in expired
    )
    if route_clearance_unconfirmed:
        reasons.append(
            "No fly advisory expired along the remaining route; future clearance is unconfirmed."
        )
    if recovered and not confirmed_current:
        notices.append(
            "Mission playback is ahead of NOAA observations. "
            "Expired SIGMETs do not confirm target clearance; review weather before accepting this draft."
        )
    if released and not confirmed_current:
        notices.append(
            "Mission playback is ahead of NOAA observations. "
            "Expired or missing SIGMETs do not confirm route clearance; "
            "review weather before accepting this draft with a shorter route."
        )
    return {
        "affected": bool(reasons),
        "reasons": reasons,
        "canReplan": not route_clearance_unconfirmed,
        "notices": notices,
        "recoveredTargets": recovered,
        "releasedDetours": released,
        "requiresWeatherReview": bool(
            (recovered or released) and not confirmed_current
        ),
    }
