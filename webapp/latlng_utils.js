// Lat/lng <-> local-km projection helpers and geodesic distance.
// All distances are kilometers. Equirectangular projection around a
// caller-supplied reference lat/lng — accurate for mission-scale areas.

const EARTH_RADIUS_KM = 6371.0088;
const KM_PER_DEG_LAT = 111.32;

function kmPerDegLng(refLat) {
  return KM_PER_DEG_LAT * Math.cos(refLat * Math.PI / 180);
}

function latLngToLocalXY(lat, lng, refLat, refLng) {
  const xKm = (lng - refLng) * kmPerDegLng(refLat);
  const yKm = (lat - refLat) * KM_PER_DEG_LAT;
  return [xKm, yKm];
}

function localXYToLatLng(xKm, yKm, refLat, refLng) {
  const lat = refLat + yKm / KM_PER_DEG_LAT;
  const lng = refLng + xKm / kmPerDegLng(refLat);
  return [lat, lng];
}

function haversineKm(lat1, lng1, lat2, lng2) {
  const phi1 = lat1 * Math.PI / 180;
  const phi2 = lat2 * Math.PI / 180;
  const dphi = (lat2 - lat1) * Math.PI / 180;
  const dlam = (lng2 - lng1) * Math.PI / 180;
  const a = Math.sin(dphi / 2) ** 2 + Math.cos(phi1) * Math.cos(phi2) * Math.sin(dlam / 2) ** 2;
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.sqrt(a));
}

// Flight geometry follows the same spherical great-circle interpolation as
// server/solver/animation_frame.py. Map, 3D, playback and checkpoint cuts must
// share this curve; linear latitude/longitude interpolation leaves it between
// vertices, even when both calculations agree exactly at every waypoint.
function interpolateFlightPosition(from, to, fraction) {
  if (fraction <= 0) return [from[0], from[1]];
  if (fraction >= 1) return [to[0], to[1]];
  const rad = Math.PI / 180;
  const phi1 = from[0] * rad, lam1 = from[1] * rad;
  const phi2 = to[0] * rad, lam2 = to[1] * rad;
  const h = Math.sin((phi2 - phi1) / 2) ** 2 +
    Math.cos(phi1) * Math.cos(phi2) * Math.sin((lam2 - lam1) / 2) ** 2;
  const angle = 2 * Math.asin(Math.sqrt(Math.max(0, Math.min(1, h))));
  // Duplicate vertices encode a stationary hold in the authoritative frame.
  if (angle < 1e-10) return [from[0], from[1]];
  const a = Math.sin((1 - fraction) * angle) / Math.sin(angle);
  const b = Math.sin(fraction * angle) / Math.sin(angle);
  const x = a * Math.cos(phi1) * Math.cos(lam1) + b * Math.cos(phi2) * Math.cos(lam2);
  const y = a * Math.cos(phi1) * Math.sin(lam1) + b * Math.cos(phi2) * Math.sin(lam2);
  const z = a * Math.sin(phi1) + b * Math.sin(phi2);
  return [Math.atan2(z, Math.hypot(x, y)) / rad, Math.atan2(y, x) / rad];
}

// Inverse of the shared flight curve: nearest point on the finite short arc,
// including its endpoints. Used to locate saved positions/cuts in a distance
// frame without replacing the great circle with a line in latitude/longitude.
function closestFlightPosition(from, to, position) {
  const rad = Math.PI / 180;
  const vector = point => {
    const latitude = point[0] * rad, longitude = point[1] * rad;
    return [Math.cos(latitude) * Math.cos(longitude),
      Math.cos(latitude) * Math.sin(longitude), Math.sin(latitude)];
  };
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const angle = (a, b) => Math.atan2(Math.hypot(...cross(a, b)), dot(a, b));
  const start = vector(from), end = vector(to), query = vector(position);
  const arcAngle = angle(start, end);
  const candidate = fraction => {
    const point = interpolateFlightPosition(from, to, fraction);
    return {fraction, point, distanceKm: angle(query, vector(point)) * EARTH_RADIUS_KM};
  };
  let best = candidate(0);
  // Match forward interpolation's stationary interval (loiter/hold vertices).
  if (arcAngle < 1e-10) return best;
  const endpoint = candidate(1);
  if (endpoint.distanceKm < best.distanceKm) best = endpoint;

  const normal = cross(start, end), normalLength = Math.hypot(...normal);
  // Antipodal endpoints do not define a unique great circle.
  if (normalLength < 1e-12) return best;
  const unitNormal = normal.map(value => value / normalLength);
  const perpendicular = dot(query, unitNormal);
  const projected = query.map((value, axis) => value - perpendicular * unitNormal[axis]);
  const projectedLength = Math.hypot(...projected);
  // At a pole of this great circle every point is equally distant.
  if (projectedLength < 1e-12) return best;
  const nearest = projected.map(value => value / projectedLength);
  const along = Math.atan2(dot(cross(start, nearest), unitNormal), dot(start, nearest));
  if (along >= -1e-12 && along <= arcAngle + 1e-12) {
    const projectedCandidate = candidate(Math.max(0, Math.min(1, along / arcAngle)));
    if (projectedCandidate.distanceKm < best.distanceKm) best = projectedCandidate;
  }
  return best;
}

// ---------------------------------------------------------------------------
// SINGLE SOURCE OF TRUTH for "has the drone visited this target?" (§9, §8.1)
//
// A trajectory is point-to-point lines between route waypoints; a target IS a
// route vertex. "Visited" therefore means the drone has REACHED that vertex —
// NOT merely that the path passed near the target. One tight tolerance, used
// everywhere. Replaces 5 scattered checks that used 50/100/150/500 m thresholds
// (the 500 m one falsely crossed targets long before arrival).
// ---------------------------------------------------------------------------
const TARGET_HIT_KM = 0.05; // 50 m: arrival-at-vertex tolerance

/**
 * Has `drone` reached `target`'s vertex on its trajectory?
 *
 * @param {Object} target            - { lat, lng }
 * @param {Array}  trajectory        - [[lat,lng], ...] the drone's path
 * @param {Array}  cumulativeDist    - cumulative km at each trajectory point (same length)
 * @param {number} currentDistanceKm - distance the drone has travelled so far
 * @returns {boolean} true iff the closest trajectory vertex to the target is
 *          within TARGET_HIT_KM AND the drone has travelled to/past that vertex.
 */
function isTargetVisited(target, trajectory, cumulativeDist, currentDistanceKm, hitToleranceKm) {
  if (!target || typeof target.lat !== "number" || typeof target.lng !== "number") return false;
  if (!Array.isArray(trajectory) || trajectory.length === 0) return false;

  // Tolerance for "the path reaches this target": TARGET_HIT_KM (50 m) for a
  // plain vertex target, OR the detection RADIUS when set. With a detection
  // radius, the taut-string relaxes the target's waypoint to a point up to r km
  // off its center — so the trajectory's closest point to the CENTER is ~r away,
  // not ~0. Checking against 50 m would then never fire. We instead accept the
  // closest trajectory vertex if it's within the radius, and detect the visit
  // the instant the drone REACHES that vertex's cumulative distance. This is
  // deterministic (keyed on distance, not a per-frame proximity sample that can
  // skip the tangent point), which is why radius targets were missing crosses.
  const tol = (typeof hitToleranceKm === "number" && hitToleranceKm > 0)
    ? hitToleranceKm
    : TARGET_HIT_KM;

  let minDist = Infinity;
  let closestIdx = -1;
  for (let idx = 0; idx < trajectory.length; idx++) {
    const pt = trajectory[idx];
    const d = haversineKm(pt[0], pt[1], target.lat, target.lng);
    if (d < minDist) {
      minDist = d;
      closestIdx = idx;
    }
  }
  if (closestIdx < 0 || minDist >= tol) return false;

  // Must have actually travelled to/past the target's (relaxed) vertex.
  const vertexDist = Array.isArray(cumulativeDist) ? cumulativeDist[closestIdx] : undefined;
  if (typeof vertexDist !== "number") return false;
  return Number(currentDistanceKm) >= vertexDist;
}

// ---------------------------------------------------------------------------
// SINGLE SOURCE OF TRUTH for fuel accounting (§5 canonical fuel model).
//
// A trip is 1 leg (no refuel) or N legs (refuel). Each leg has a FULL TANK
// (fuel_capacity) available; refuel tops up exactly what the prior leg burned.
//   - fuel USED   = Σ leg_used (SAM-aware leg distances)
//   - BUDGET      = tank × N         (Option A: one full tank per leg)
//   - P/F         = points / used
//   - feasibility = each leg_used ≤ tank, independently
// Used by the stats box (and intended for validator/solver display). The
// §8.2/§8.3 bugs + the broken "6+0 / 25" stats came from this being computed
// inconsistently in three places.
// ---------------------------------------------------------------------------

/**
 * Extract the per-leg used-distances for a route.
 * Refuel routes today carry leg1_distance/leg2_distance (N=2). When multi-refuel
 * lands, a `leg_distances` array will be preferred. Non-refuel = single leg.
 * @returns {number[]} leg distances (length = number of legs)
 */
function getLegDistances(routeInfo) {
  if (!routeInfo) return [0];
  if (Array.isArray(routeInfo.leg_distances) && routeInfo.leg_distances.length) {
    return routeInfo.leg_distances.map(Number);
  }
  if (routeInfo.refuel_airport) {
    return [
      Number(routeInfo.leg1_distance || 0),
      Number(routeInfo.leg2_distance || 0),
    ];
  }
  return [Number(routeInfo.distance || 0)];
}

/**
 * Canonical fuel stats for one drone (§5 model).
 * @param {Object} routeInfo - route record (route, distance, refuel_airport, leg*_distance)
 * @param {number} tank      - full tank = fuel_capacity for this drone
 * @param {number} points    - planned points for this drone
 * @returns {{legs:number[], used:number, budget:number, numLegs:number, pf:number, feasible:boolean, display:string}}
 */
function computeFuelStats(routeInfo, tank, points) {
  const legs = getLegDistances(routeInfo);
  const numLegs = legs.length;
  const used = legs.reduce((s, d) => s + (Number(d) || 0), 0);
  const budget = Number(tank) * numLegs;                 // Option A: N × full tank
  const pf = used > 0 ? Number(points) / used : 0;
  const feasible = legs.every((d) => Number(d) <= Number(tank) + 1e-6);
  const display =
    numLegs > 1
      ? `⛽${legs.map((d) => Math.round(d)).join("+")} / ${Math.round(budget)}`
      : `${Math.round(used)} / ${Math.round(budget)}`;
  return { legs, used, budget, numLegs, pf, feasible, display };
}

// ---------------------------------------------------------------------------
// SINGLE GROUND TRUTH for route distances (§10 design spec).
//
// The SAM-aware distance matrix is the ONLY source of leg distances. A route's
// "distance profile" is the cumulative SAM-aware distance at each ROUTE WAYPOINT.
// Everything that needs a distance — segment length, refuel-point distance, fuel
// remaining, the drone's animated position, segment-switch boundaries — reads
// THIS profile. No consumer recomputes its own cumulative distance, and nothing
// uses straight-line/haversine for leg distances (that violated §5 #1 and caused
// the §8.5–§8.9 cut/resume drift: refuel pause past the airport, fuel=0, etc.).
//
// Position is still drawn along the fine lat/lng trajectory, but each
// inter-waypoint span is SCALED so its length equals the matrix leg distance —
// "profile anchors at waypoints; trajectory fills between" (agreed model).
// ---------------------------------------------------------------------------

/** Index of a waypoint label in the matrix labels (-1 if absent). */
function matrixIndexOf(label, labels) {
  return Array.isArray(labels) ? labels.indexOf(label) : -1;
}

/** SAM-aware distance between two waypoint labels, from the matrix. */
function samLegDistance(fromLabel, toLabel, matrixObj) {
  if (!matrixObj) return NaN;
  const labels = matrixObj.labels || matrixObj.matrix_labels;
  const m = matrixObj.matrix || matrixObj.distance_matrix;
  const i = matrixIndexOf(fromLabel, labels);
  const j = matrixIndexOf(toLabel, labels);
  if (i < 0 || j < 0 || !m || !m[i]) return NaN;
  return Number(m[i][j]);
}

/**
 * Build the authoritative route distance profile from the SAM-aware matrix.
 *
 * @param {string[]} route      - ordered waypoint labels, e.g. ["C1-1","T3","A1","T7","A2"]
 * @param {Object}   matrixObj  - { matrix:[[...]], labels:[...] } (SAM-aware)
 * @param {Object}  [opts]
 * @param {Object}  [opts.waypointIndices] - map route-index → trajectory-index (from solver)
 * @param {Array}   [opts.trajectory]      - fine lat/lng points (for position interpolation)
 * @param {string}  [opts.refuelAirport]   - refuel waypoint label, if any
 * @returns {{
 *   waypoints: {wp:string, cumDist:number, trajIdx:number|null}[],
 *   total: number,
 *   refuelDist: number|null,
 *   legBreaks: number[],         // cumDist at each refuel waypoint (leg boundaries)
 *   ok: boolean                  // false if any leg distance was missing from the matrix
 * }}
 */
function buildRouteDistanceProfile(route, matrixObj, opts) {
  opts = opts || {};
  const wpIdx = opts.waypointIndices || {};
  const refuelAirport = opts.refuelAirport;
  const waypoints = [];
  let cum = 0;
  let ok = true;

  for (let i = 0; i < (route ? route.length : 0); i++) {
    if (i > 0) {
      const d = samLegDistance(route[i - 1], route[i], matrixObj);
      if (!isFinite(d)) {
        // Matrix missing this leg — record but flag; callers decide fallback.
        ok = false;
      } else {
        cum += d;
      }
    }
    const trajIdx =
      wpIdx && (wpIdx[i] !== undefined) ? Number(wpIdx[i]) : null;
    waypoints.push({ wp: route[i], cumDist: cum, trajIdx });
  }

  // Refuel point distance = profile value at the refuel waypoint (first match
  // after index 0 so a refuel airport that equals the start isn't picked).
  let refuelDist = null;
  if (refuelAirport && refuelAirport !== "-" && refuelAirport !== "") {
    for (let i = 1; i < waypoints.length; i++) {
      if (waypoints[i].wp === refuelAirport) {
        refuelDist = waypoints[i].cumDist;
        break;
      }
    }
  }

  // Leg boundaries (cumDist where the drone refuels). With single refuel this is
  // [refuelDist]; generalizes to N refuels.
  const legBreaks = refuelDist != null ? [refuelDist] : [];

  return { waypoints, total: cum, refuelDist, legBreaks, ok };
}

/**
 * Per-trajectory-point cumulative distance in the PROFILE (matrix) frame.
 *
 * "Profile anchors at waypoints; trajectory fills between" (§10): the cumulative
 * distance AT each route waypoint equals the matrix-derived profile cumDist; the
 * fine trajectory points between two waypoints are spaced by their haversine
 * fraction of that span, SCALED so the span length equals the matrix leg
 * distance. Result: a per-point cumulative array whose values at waypoint
 * indices are matrix-exact, while drawing stays smooth.
 *
 * @param {Array}  trajectory      - fine lat/lng points
 * @param {Object} profile         - from buildRouteDistanceProfile (has .waypoints with trajIdx + cumDist)
 * @returns {{cumDist:number[], total:number}} aligned 1:1 with trajectory points
 */
function buildTrajectoryCumDistFromProfile(trajectory, profile) {
  const n = Array.isArray(trajectory) ? trajectory.length : 0;
  const cumDist = new Array(n).fill(0);
  if (n === 0 || !profile || !profile.waypoints) {
    return { cumDist, total: 0 };
  }
  // Anchors = waypoints that have a valid trajIdx, in order.
  const anchors = profile.waypoints.filter(
    (w) => w.trajIdx !== null && w.trajIdx >= 0 && w.trajIdx < n
  );
  if (anchors.length < 2) {
    // No usable anchors — fall back to proportional haversine over the whole
    // trajectory scaled to the profile total (still matrix-framed at the ends).
    let raw = 0;
    const rawCum = [0];
    for (let i = 1; i < n; i++) {
      raw += haversineKm(trajectory[i - 1][0], trajectory[i - 1][1], trajectory[i][0], trajectory[i][1]);
      rawCum.push(raw);
    }
    const scale = raw > 0 ? profile.total / raw : 0;
    for (let i = 0; i < n; i++) cumDist[i] = rawCum[i] * scale;
    return { cumDist, total: profile.total };
  }
  // Fill each span [anchor k .. anchor k+1] proportionally, scaled to the matrix
  // leg distance (= cumDist difference between the two anchors).
  for (let k = 0; k < anchors.length - 1; k++) {
    const a = anchors[k];
    const b = anchors[k + 1];
    const spanDist = b.cumDist - a.cumDist;
    // raw haversine lengths within the span
    let raw = 0;
    const rawCum = [0];
    for (let i = a.trajIdx + 1; i <= b.trajIdx; i++) {
      raw += haversineKm(trajectory[i - 1][0], trajectory[i - 1][1], trajectory[i][0], trajectory[i][1]);
      rawCum.push(raw);
    }
    const scale = raw > 0 ? spanDist / raw : 0;
    for (let i = a.trajIdx, r = 0; i <= b.trajIdx; i++, r++) {
      cumDist[i] = a.cumDist + rawCum[r] * scale;
    }
  }
  // Points before the first anchor / after the last clamp to the anchor values.
  for (let i = 0; i < anchors[0].trajIdx; i++) cumDist[i] = anchors[0].cumDist;
  const last = anchors[anchors.length - 1];
  for (let i = last.trajIdx + 1; i < n; i++) cumDist[i] = last.cumDist;
  return { cumDist, total: profile.total };
}

// ---------------------------------------------------------------------------
// THE single animation cumulative-distance builder (§10 atomic frame).
//
// ALL animation code that builds a per-trajectory-point cumulative array MUST
// call this — there are ~4 such sites (init + segment switches). They previously
// each summed straight-line haversine independently, which (a) violated §5 #1 and
// (b) let different sites disagree on the frame (the Step 3 break: converting one
// builder while others stayed haversine → mixed frames → drone stuck at start).
//
// Frame = WHOLE-ROUTE CUMULATIVE in the SAM-aware MATRIX frame: matrix-exact at
// each route waypoint, haversine-interpolated (scaled) between. Because the cut
// distance and missionDistance are derived FROM distanceTraveled (which advances
// through this array), the matrix frame self-propagates to all consumers once
// every builder uses this function. Falls back to pure haversine only when the
// matrix/route/profile is unavailable (degrade, never crash). leg-generic via
// the profile's legBreaks[] (ready for multi-refuel / air-refuel).
//
// @returns {{cumDist:number[], total:number, profile:Object|null, usedMatrix:boolean}}
function computeAnimationCumDist(trajectory, route, matrixObj, opts) {
  opts = opts || {};
  const n = Array.isArray(trajectory) ? trajectory.length : 0;
  if (n < 2) return { cumDist: n === 1 ? [0] : [], total: 0, profile: null, usedMatrix: false };

  // Raw straight-line length of the ACTUAL trajectory points. Computed once and
  // used both as the haversine fallback and as the sanity check below.
  const rawCumDist = [0];
  let rawTotal = 0;
  for (let i = 1; i < n; i++) {
    rawTotal += haversineKm(trajectory[i - 1][0], trajectory[i - 1][1], trajectory[i][0], trajectory[i][1]);
    rawCumDist.push(rawTotal);
  }

  // §unify: the builder SELF-SELECTS the frame from the inputs — callers no longer
  // pass a forceHaversine flag. Use the SAM-aware matrix profile ONLY when the
  // route↔trajectory↔matrix line up; otherwise measure the trajectory directly.
  //
  // The mismatch case is a STITCHED whole-mission replay trajectory: its `route`
  // is the last-segment route only, so the profile covers a fraction of the real
  // path → too-short total → drone crawls to a stop. We detect that by comparing
  // the profile total against the trajectory's raw haversine length: a real
  // matrix profile is within a sane ratio of the raw length (SAM detours make it
  // LONGER, never much shorter), so a profile total far below raw means the route
  // doesn't describe this trajectory → fall back to haversine.
  if (matrixObj && Array.isArray(route) && route.length >= 2) {
    const profile = buildRouteDistanceProfile(route, matrixObj, {
      waypointIndices: opts.waypointIndices,
      trajectory,
      refuelAirport: opts.refuelAirport,
    });
    // PROFILE_MIN_RATIO: matrix profile must be ≥ this fraction of the raw length
    // to be trusted as describing this trajectory. rawTotal is the haversine length
    // of the SAM-AVOIDING trajectory points (already detoured), and the matrix
    // profile measures that SAME detoured path — so a legitimate profile is ≈ 1.0×
    // raw (≥ it, modulo float slack). A stitched full-mission-replay trajectory
    // gives a profile covering only the LAST segment's route → ratio well below 1
    // (observed 0.64 for a 2-segment replay: profile 12.1 vs raw 19.0). Threshold
    // 0.9 separates them: ~1.0 legit, <0.9 = route doesn't describe this trajectory.
    const PROFILE_MIN_RATIO = 0.9;
    const profileDescribesTraj =
      profile.ok && profile.total > 0 &&
      (rawTotal === 0 || profile.total >= PROFILE_MIN_RATIO * rawTotal);
    if (profileDescribesTraj) {
      const tc = buildTrajectoryCumDistFromProfile(trajectory, profile);
      return { cumDist: tc.cumDist, total: tc.total, profile, usedMatrix: true };
    }
  }

  // Fallback: straight-line haversine over the actual trajectory points (computed
  // above as rawCumDist/rawTotal). Used when no matrix/route, or when the route
  // doesn't describe this (stitched) trajectory.
  return { cumDist: rawCumDist, total: rawTotal, profile: null, usedMatrix: false };
}
