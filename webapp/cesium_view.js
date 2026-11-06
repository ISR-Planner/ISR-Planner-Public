// ----------------------------------------------------
// Cesium 3D mission view — static read-only viewer.
//
// Third view mode alongside Grid/Map (see toggleMapMode in isr.js). Renders
// the CURRENT mission state (airports, targets, SAMs, solved routes) as a
// Cesium scene. No editing, no animation sync yet — camera orbit only.
//
// Altitude: each drone's cruise_altitude_km (Asset tab) is used as a
// single, cosmetic cruise plane for that drone's route polyline. This
// does NOT reflect real per-leg altitude/climb physics (see the "climb
// through SAM zone near airports" limitation noted in project memory) — it
// is purely a visual "fly the route at height H" rendering choice.
// ----------------------------------------------------

const cesiumView = (() => {
  let viewer = null;
  const lifecycleSubscribers = new Set();

  // Synchronous observers run after successful init/render, in registration order.
  // Subscriptions belong to the page and survive viewer destruction/recreation.
  function subscribeLifecycle({ onInit, onRender } = {}) {
    if ((onInit !== undefined && typeof onInit !== "function") ||
        (onRender !== undefined && typeof onRender !== "function") ||
        (!onInit && !onRender)) {
      throw new TypeError("Lifecycle subscription requires onInit and/or onRender callbacks.");
    }
    const subscription = { onInit, onRender };
    lifecycleSubscribers.add(subscription);
    return () => lifecycleSubscribers.delete(subscription);
  }

  function notifyLifecycle(event, ...args) {
    for (const subscription of [...lifecycleSubscribers]) {
      if (lifecycleSubscribers.has(subscription)) subscription[event]?.(...args);
    }
  }

  function initialized(value) {
    notifyLifecycle("onInit", value);
    return value;
  }

  let ionTokenPromise = null;
  let routeCartesiansByDrone = {}; // did -> Cesium.Cartesian3[] for the fly-along camera
  let followedDroneId = null;
  let droneMarkers = {}; // did -> Cesium.Entity, live animation position markers
  let framedSig = null; // the airports/targets the camera was last framed on
  let droneLastPosition = {}; // did -> Cesium.Cartesian3, previous frame's position (for owned chase-cam heading)
  let droneLastForward = {}; // did -> Cesium.Cartesian3 (normalized), last known real heading (survives stationary pauses)
  let droneCamForward = {}; // did -> smoothed horizontal heading the chase-cam aims along
  let droneCamTime = {}; // did -> ms timestamp of the last chase-cam update

  const DEFAULT_CLIMB_ANGLE_DEG = 12; // fallback if the input is missing/invalid

  function currentClimbGradient(did) {
    // The drone's climb angle (Asset tab) as rise:run (pure physics: climb
    // distance = altitude / tan(angle), independent of route length — see
    // rampAltitudeAtDistance). Cosmetic only, no effect on solving/routing.
    const deg = parseFloat(state.droneConfigs?.[String(did)]?.climb_angle_deg);
    const clamped = Number.isFinite(deg) ? Math.min(Math.max(deg, 5), 80) : DEFAULT_CLIMB_ANGLE_DEG;
    return Math.tan((clamped * Math.PI) / 180);
  }

  // Same drone-id -> color map as the 2D canvas (webapp/isr.js, droneColors),
  // so a drone is the same color in Grid/Map/3D. Keyed by drone id, not
  // iteration order, since Object.entries/Object.keys order isn't a
  // meaningful drone identity.
  const DRONE_COLORS_BY_ID = {
    "1": "#3b82f6",
    "2": "#f97316",
    "3": "#22c55e",
    "4": "#ef4444",
    "5": "#a855f7",
  };

  // Same target-type -> color map as the 2D canvas (webapp/isr.js, the
  // `type === "b"/"c"/...` chain in the target-drawing loop).
  const TARGET_COLORS_BY_TYPE = {
    a: "#3b82f6",
    b: "#22c55e",
    c: "#f59e0b",
    d: "#ef4444",
    e: "#a855f7",
  };
  function targetColor(type) {
    // The target category's colour (isr.js categoryColor), same as the 2D map.
    const css = typeof categoryColor === "function"
      ? categoryColor(type)
      : TARGET_COLORS_BY_TYPE[(type || "a").toLowerCase()] || "#3b82f6";
    return Cesium.Color.fromCssColorString(css);
  }

  // Same SAM circle color as the 2D canvas (webapp/isr.js: fill
  // rgba(234,179,8,0.18), stroke #f59e0b — amber, not red).
  function samFillColor() {
    return Cesium.Color.fromCssColorString("#eab308").withAlpha(0.18);
  }
  function samOutlineColor() {
    return Cesium.Color.fromCssColorString("#f59e0b").withAlpha(0.7);
  }

  async function ensureIonToken() {
    if (ionTokenPromise) return ionTokenPromise;
    ionTokenPromise = fetch("/api/client_config")
      .then((r) => (r.ok ? r.json() : {}))
      .then((cfg) => cfg.cesium_ion_token || "")
      .catch(() => "");
    return ionTokenPromise;
  }

  function isActive() {
    return !!viewer;
  }

  // Ground elevation (km above sea level, WGS84 heights) from the SAME terrain
  // the 3D view draws, so 2D data and the 3D picture never disagree. Without a
  // Cesium token (or offline, or on any error) the 3D ground is flat, so 0.
  let worldTerrainPromise = null;
  async function worldTerrain() {
    if (typeof Cesium === "undefined") return null;
    const token = await ensureIonToken();
    if (!token) return null;
    Cesium.Ion.defaultAccessToken = token;
    if (!worldTerrainPromise) {
      worldTerrainPromise = Cesium.createWorldTerrainAsync().catch(() => null);
    }
    return worldTerrainPromise;
  }

  async function groundElevationsKm(points) {
    const zeros = points.map(() => 0);
    try {
      const provider = await worldTerrain();
      if (!provider || points.length === 0) return zeros;
      const cartos = points.map((p) => Cesium.Cartographic.fromDegrees(p.lng, p.lat));
      const sampled = await Cesium.sampleTerrainMostDetailed(provider, cartos);
      return sampled.map((c) => (Number.isFinite(c.height) ? c.height / 1000 : 0));
    } catch (_err) {
      return zeros;
    }
  }

  async function init(containerId) {
    if (viewer) return initialized(viewer);
    if (typeof Cesium === "undefined") {
      appendDebugLine("❌ Cesium 3D view: Cesium library not loaded");
      return initialized(null);
    }

    const token = await ensureIonToken();
    if (token) {
      Cesium.Ion.defaultAccessToken = token;
    }

    const viewerOptions = {
      baseLayerPicker: false,
      geocoder: false,
      homeButton: true,
      sceneModePicker: true,
      navigationHelpButton: true,
      animation: false,
      timeline: false,
      fullscreenButton: true,
      // ToT marker CallbackProperties keep pulsing even when flight is paused.
      requestRenderMode: false,
    };
    if (token) {
      viewerOptions.terrain = Cesium.Terrain.fromWorldTerrain();
    } else {
      // No token configured: bundled offline imagery, flat terrain. Still a
      // usable 3D view — just without real satellite imagery/elevation.
      viewerOptions.imageryProvider = new Cesium.TileMapServiceImageryProvider({
        url: Cesium.buildModuleUrl("Assets/Textures/NaturalEarthII"),
      });
      appendDebugLine("ℹ️ Cesium 3D view: no CESIUM_ION_TOKEN set, using offline imagery");
    }

    viewer = new Cesium.Viewer(containerId, viewerOptions);
    return initialized(viewer);
  }

  // Use the same public tile sources as the 2D map, independently of terrain.
  function setMapType(type) {
    if (!viewer) return;
    const satellite = type === "satellite";
    const provider = new Cesium.UrlTemplateImageryProvider({
      url: satellite
        ? "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
        : "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      subdomains: "abc",
      maximumLevel: 19,
      credit: satellite ? "Tiles © Esri" : "© OpenStreetMap contributors",
    });
    viewer.imageryLayers.removeAll();
    viewer.imageryLayers.addImageryProvider(provider);
  }

  function destroy() {
    if (viewer) {
      viewer.destroy();
      viewer = null;
    }
    droneMarkers = {};
    framedSig = null;
  }

  // What the camera frames: the mission's airports and targets.
  function sceneSignature() {
    const env = state.env || {};
    const pts = [...(env.airports || []), ...(env.targets || [])]
      .map((p) => `${p.id}:${Number(p.lat).toFixed(3)},${Number(p.lng).toFixed(3)}`);
    return pts.sort().join("|");
  }

  function droneColor(did) {
    return Cesium.Color.fromCssColorString(DRONE_COLORS_BY_ID[String(did)] || "#22c55e");
  }

  function trajectoryDistanceKm(a, b) {
    return haversineKm(a[0], a[1], b[0], b[1]);
  }

  // Use the same flight curve as playback, cuts and the Map route renderer.
  function geodesicInterpolate(lat1, lng1, lat2, lng2, t) {
    return interpolateFlightPosition([lat1, lng1], [lat2, lng2], t);
  }

  function densifyTrajectory(trajectory, maxSegmentKm = 1.0) {
    // The SAM-avoidance trajectory is only densely sampled where it BENDS
    // around a SAM — a direct, unobstructed leg (the common case) is just
    // [start, end], 2 points. With only 2 points, the altitude ramp has
    // nothing to interpolate a climb THROUGH: Cesium draws one straight 3D
    // line between two altitude values, which looks like "fly straight at
    // the destination's altitude," ignoring the climb angle entirely. Insert
    // extra points along the GEODESIC path so the ramp has real points to
    // bend through near the start/end of the route.
    const out = [trajectory[0]];
    for (let i = 1; i < trajectory.length; i++) {
      const a = trajectory[i - 1];
      const b = trajectory[i];
      const segKm = trajectoryDistanceKm(a, b);
      const steps = Math.max(1, Math.ceil(segKm / maxSegmentKm));
      for (let s = 1; s <= steps; s++) {
        const t = s / steps;
        // Use geodesic interpolation instead of linear lat/lng
        out.push(geodesicInterpolate(a[0], a[1], b[0], b[1], t));
      }
    }
    return out;
  }

  // A drone's cruise altitude in metres (Asset tab).
  function droneCruiseAltM(did) {
    const own = parseFloat(state.droneConfigs?.[String(did)]?.cruise_altitude_km);
    return Number.isFinite(own) && own > 0 ? own * 1000 : 0;
  }

  // The server's flight profile for this drone's route (Phase C): the
  // densified animation path + an altitude per point (take-off, cruise,
  // tanker/airport refuel, landing — server/solver/flight_profile.py). null
  // when the route has none (e.g. a stitched replay): then the old client-side
  // climb ramp is used.
  function flightProfile(did) {
    const af = state.routes?.[String(did)]?.anim_frame;
    if (!af || !Array.isArray(af.altitudes) || !Array.isArray(af.trajectory)) return null;
    if (af.altitudes.length !== af.trajectory.length || af.trajectory.length < 2) return null;
    return af;
  }

  // Altitude (m) at a distance along the profile, interpolated between points.
  function profileAltitudeM(af, distKm) {
    const s = af.cumulative_distances || [];
    const a = af.altitudes;
    if (!s.length) return a[0] * 1000;
    if (distKm <= s[0]) return a[0] * 1000;
    if (distKm >= s[s.length - 1]) return a[a.length - 1] * 1000;
    let lo = 0, hi = s.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (s[mid] <= distKm) lo = mid; else hi = mid;
    }
    const span = s[hi] - s[lo];
    const f = span > 0 ? (distKm - s[lo]) / span : 0;
    return (a[lo] + f * (a[hi] - a[lo])) * 1000;
  }

  // Re-interpolate a trajectory along geodesic paths while preserving altitudes
  function geodesicReinterpolate(trajectory, altitudes) {
    if (!trajectory || trajectory.length < 2) return { trajectory, altitudes };
    const newTraj = [trajectory[0]];
    const newAlts = [altitudes[0]];
    for (let i = 1; i < trajectory.length; i++) {
      const a = trajectory[i - 1];
      const b = trajectory[i];
      const altA = altitudes[i - 1];
      const altB = altitudes[i];
      const segKm = trajectoryDistanceKm(a, b);
      // Draw our shared flight curve directly, with <=1 km Cartesian chords.
      // Their curvature error is about 2 cm, without asking Cesium to replace
      // the spherical flight model with a different ellipsoidal geodesic.
      const steps = Math.max(1, Math.ceil(segKm));
      for (let s = 1; s <= steps; s++) {
        const t = s / steps;
        newTraj.push(geodesicInterpolate(a[0], a[1], b[0], b[1], t));
        newAlts.push(altA + (altB - altA) * t); // Linear altitude interpolation
      }
    }
    return { trajectory: newTraj, altitudes: newAlts };
  }

  function buildRoutePositions(trajectory, cruiseAltM, did) {
    const profile = flightProfile(did);
    if (profile) {
      // Re-interpolate server trajectory along geodesic paths
      const { trajectory: geoTraj, altitudes: geoAlts } =
        geodesicReinterpolate(profile.trajectory, profile.altitudes);
      const flat = [];
      geoTraj.forEach((p, i) => flat.push(p[1], p[0], geoAlts[i] * 1000));
      return flat;
    }
    // trajectory: array of [lat, lng] ground-track points from state.routes —
    // a SAM-avoidance path, densely sampled only where it bends around a SAM
    // (see densifyTrajectory for why a straight/direct leg needs its own
    // densification pass first). Assign altitude by CUMULATIVE distance
    // along the route (not "next point"), climbing from the start airport's
    // ground to cruise, holding at cruise, then descending to the end
    // airport's ground (rampAltitudeAtDistance).
    const flat = [];
    const push = (lat, lng, alt) => flat.push(lng, lat, alt);

    if (!trajectory || trajectory.length === 0) return flat;
    if (cruiseAltM <= 0 || trajectory.length < 2) {
      const groundM = routeEndAltitudesM(did, 0).startM;
      trajectory.forEach(([lat, lng]) => push(lat, lng, groundM));
      return flat;
    }

    const dense = densifyTrajectory(trajectory);
    const n = dense.length;
    const cumKm = [0];
    for (let i = 1; i < n; i++) {
      cumKm.push(cumKm[i - 1] + trajectoryDistanceKm(dense[i - 1], dense[i]));
    }
    const totalKm = cumKm[n - 1];

    for (let i = 0; i < n; i++) {
      const alt = rampAltitudeAtDistance(cumKm[i], totalKm - cumKm[i], totalKm, cruiseAltM, did);
      push(dense[i][0], dense[i][1], alt);
    }

    return flat;
  }

  // Altitude (m) where this drone's route starts and ends: an airport's
  // ground (alt_km) when the route begins/ends at one, otherwise cruise
  // (a route that starts at a mid-air cut is already up there).
  function routeEndAltitudesM(did, cruiseAltM) {
    const route = state.routes?.[String(did)]?.route || [];
    const airports = state.env?.airports || [];
    const groundM = (wp) => {
      const a = airports.find((ap) => ap.id === wp && ap.type !== "tanker" && ap.is_tanker !== true);
      return a ? (Number(a.alt_km) || 0) * 1000 : cruiseAltM;
    };
    return {
      startM: route.length ? groundM(route[0]) : 0,
      endM: route.length ? groundM(route[route.length - 1]) : 0,
    };
  }

  function rampAltitudeAtDistance(distFromStartKm, distFromEndKm, totalKm, cruiseAltM, did) {
    // Shared by the static route polyline (buildRoutePositions) and the live
    // animation marker (updateDronePosition) so both always agree on altitude
    // at a given point along the route — same climb/descent ramp either way.
    // Used only when the route has no server flight profile (e.g. a stitched
    // replay). Altitudes are above sea level: take off from the start
    // airport's ground, land on the end airport's ground.
    const { startM, endM } = routeEndAltitudesM(did, cruiseAltM);
    if (cruiseAltM <= 0) return startM;
    // Climb distance is purely physical: height gained / tan(angle) —
    // independent of total route length. The only real constraint is
    // totalKm/2 — climb and descent can't overlap.
    const grad = currentClimbGradient(did);
    const climbKm = Math.min(Math.max(cruiseAltM - startM, 0) / 1000 / grad, totalKm / 2);
    const descentKm = Math.min(Math.max(cruiseAltM - endM, 0) / 1000 / grad, totalKm / 2);
    if (distFromStartKm < climbKm) return startM + (cruiseAltM - startM) * (distFromStartKm / climbKm);
    if (distFromEndKm < descentKm) return endM + (cruiseAltM - endM) * (distFromEndKm / descentKm);
    return cruiseAltM;
  }

  // Rebuilds the scene. keepCamera keeps the drone markers, their heading
  // history and the camera. By default it is on whenever the camera was
  // already framed on the same airports and targets — a pause, a resume, a
  // stats redraw or a re-solve must not yank a zoomed or chase view back out
  // to the whole mission. The camera re-frames on first open and when a
  // different set of airports/targets is shown.
  function render(opts = {}) {
    renderScene(opts);
    notifyLifecycle("onRender", viewer, opts);
  }

  function renderScene(opts) {
    if (!viewer) return;
    const sig = sceneSignature();
    const keepCamera = opts.keepCamera != null ? !!opts.keepCamera : framedSig === sig;
    if (!keepCamera) framedSig = sig;
    if (keepCamera) {
      const markers = new Set(Object.values(droneMarkers));
      viewer.entities.values.slice().forEach((e) => {
        if (!markers.has(e)) viewer.entities.remove(e);
      });
    } else {
      viewer.entities.removeAll();
      droneMarkers = {}; // removeAll() invalidated any prior marker entity refs
      droneLastPosition = {}; // stale heading history from a previous mission/redraw
      droneLastForward = {};
      droneCamForward = {};
      droneCamTime = {};
    }

    const env = state.env || {};
    (env.airports || []).forEach((a) => {
      if (typeof a.lat !== "number" || typeof a.lng !== "number") return;

      // Tankers (env.airports entries with type:"tanker") are mid-air refuel
      // points, not ground infrastructure — render as an orange diamond at
      // cruise altitude (matches the 2D canvas's deliberate blue-square vs
      // orange-diamond distinction, and the drones' own cruise altitude,
      // since that's the height at which a drone would actually reach it).
      // Some older/imported env files use `is_tanker: true` instead of
      // `type: "tanker"` — accept either so stale-schema files still render
      // correctly here (isr.js's own 2D canvas only checks `type`, so a
      // legacy-schema file may ALSO mis-render in 2D — a separate, pre-
      // existing gap, not something introduced by the 3D view).
      if (a.type === "tanker" || a.is_tanker === true) {
        viewer.entities.add({
          name: a.id,
          // A tanker holds the altitude it is given (alt_km).
          position: Cesium.Cartesian3.fromDegrees(a.lng, a.lat, (a.alt_km || 0) * 1000),
          billboard: {
            image: makeDiamondIcon(),
            verticalOrigin: Cesium.VerticalOrigin.CENTER,
            scale: 1.0,
          },
          label: {
            text: a.id, font: "12px sans-serif", fillColor: Cesium.Color.WHITE,
            outlineColor: Cesium.Color.BLACK, outlineWidth: 2,
            style: Cesium.LabelStyle.FILL_AND_OUTLINE,
            verticalOrigin: Cesium.VerticalOrigin.BOTTOM, pixelOffset: new Cesium.Cartesian2(0, -18),
          },
        });
        return;
      }

      // Real airport: blue SQUARE (unrotated), matching the 2D canvas's
      // deliberate blue-square vs orange-diamond distinction.
      viewer.entities.add({
        name: a.id,
        // On its ground (alt_km = terrain elevation, the same number the
        // server's flight profile takes off from and lands on).
        position: Cesium.Cartesian3.fromDegrees(a.lng, a.lat, (a.alt_km || 0) * 1000),
        billboard: {
          image: makeSquareIcon(),
          verticalOrigin: Cesium.VerticalOrigin.CENTER,
          scale: 1.0,
        },
        label: {
          text: a.id, font: "12px sans-serif", fillColor: Cesium.Color.WHITE,
          outlineColor: Cesium.Color.BLACK, outlineWidth: 2,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM, pixelOffset: new Cesium.Cartesian2(0, -16),
        },
      });
    });

    (env.targets || []).forEach((t) => {
      if (typeof t.lat !== "number" || typeof t.lng !== "number") return;
      viewer.entities.add({
        name: t.id,
        position: Cesium.Cartesian3.fromDegrees(t.lng, t.lat, (t.alt_km || 0) * 1000),
        point: { pixelSize: 9, color: targetColor(t.type), outlineColor: Cesium.Color.BLACK, outlineWidth: 1.5 },
        label: {
          text: t.id, font: "11px sans-serif", fillColor: Cesium.Color.WHITE,
          outlineColor: Cesium.Color.BLACK, outlineWidth: 2,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM, pixelOffset: new Cesium.Cartesian2(0, -12),
        },
      });
      // Green X once visited — read live from state.visitedTargets (the same
      // list the 2D canvas draws from), so it appears the moment a drone
      // reaches the target, with no entity rebuild.
      viewer.entities.add({
        name: `${t.id} visited`,
        position: Cesium.Cartesian3.fromDegrees(t.lng, t.lat, (t.alt_km || 0) * 1000),
        billboard: {
          image: makeVisitedIcon(),
          verticalOrigin: Cesium.VerticalOrigin.CENTER,
          eyeOffset: new Cesium.Cartesian3(0, 0, -50), // in front of the target dot
          show: new Cesium.CallbackProperty(
            () => !!(state.visitedTargets && state.visitedTargets.includes(t.id)), false
          ),
        },
      });
    });

    renderTimeSyncMarkers(env.targets || []);

    (env.sams || []).forEach((s, i) => {
      const center = s.center || {};
      if (typeof center.lat !== "number" || typeof center.lng !== "number") return;
      const rM = (s.range_km || 0) * 1000;
      if (rM <= 0) return;
      viewer.entities.add({
        name: `SAM${i}`,
        // Centred on the SAM site (its ground elevation) — what the planner slices.
        position: Cesium.Cartesian3.fromDegrees(center.lng, center.lat, (s.alt_km || 0) * 1000),
        ellipsoid: {
          radii: new Cesium.Cartesian3(rM, rM, rM),
          maximumCone: Cesium.Math.PI_OVER_TWO, // upper hemisphere only
          material: samFillColor(),
          outline: true,
          outlineColor: samOutlineColor(),
        },
      });
    });

    routeCartesiansByDrone = {};
    Object.entries(state.routes || {}).forEach(([did, rd]) => {
      const traj = rd && rd.trajectory;
      if (!traj || traj.length < 2) return;
      const positions = buildRoutePositions(traj, droneCruiseAltM(did), did);
      if (positions.length < 6) return;
      const cartesians = Cesium.Cartesian3.fromDegreesArrayHeights(positions);
      routeCartesiansByDrone[did] = cartesians;
      viewer.entities.add({
        name: `D${did} route`,
        // Use the same Show control as Map while retaining route geometry
        // for the follow camera when a route is hidden.
        show: Boolean(state.trajectoryVisible?.[did]),
        polyline: {
          positions: cartesians,
          width: 3,
          material: droneColor(did),
          arcType: Cesium.ArcType.NONE,
          clampToGround: false,
        },
      });
    });

    // Already-flown path before the cuts (the 2D canvas's dimmed history line).
    const frozen = (typeof missionReplay !== "undefined" && missionReplay.getAllFrozenTrajectories)
      ? missionReplay.getAllFrozenTrajectories() : {};
    Object.entries(frozen || {}).forEach(([did, pts]) => {
      if (!Array.isArray(pts) || pts.length < 2) return;
      const altM = droneCruiseAltM(did);
      const flat = [];
      densifyTrajectory(pts).forEach(([lat, lng]) => flat.push(lng, lat, altM));
      viewer.entities.add({
        name: `D${did} flown`,
        show: state.trajectoryVisible?.[did] !== false,
        polyline: {
          positions: Cesium.Cartesian3.fromDegreesArrayHeights(flat),
          width: 2,
          material: droneColor(did).withAlpha(0.55),
          arcType: Cesium.ArcType.NONE,
          clampToGround: false,
        },
      });
    });

    // Cut diamonds (white; red outline for a drone lost at that cut), at the
    // drone's cruise altitude where it was cut.
    const markers = typeof collectCheckpointMarkers === "function" ? collectCheckpointMarkers() : new Map();
    markers.forEach(({ pos, label, isLost, did }) => {
      viewer.entities.add({
        name: `${label} D${did}`,
        position: Cesium.Cartesian3.fromDegrees(pos[1], pos[0], droneCruiseAltM(did)),
        billboard: {
          image: makeCutIcon(isLost),
          verticalOrigin: Cesium.VerticalOrigin.CENTER,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
        label: {
          text: label, font: "bold 11px sans-serif",
          fillColor: isLost ? Cesium.Color.fromCssColorString("#ef4444") : Cesium.Color.WHITE,
          outlineColor: Cesium.Color.BLACK, outlineWidth: 2,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          horizontalOrigin: Cesium.HorizontalOrigin.LEFT,
          pixelOffset: new Cesium.Cartesian2(12, 0),
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
      });
    });

    populateFollowDropdown();
    if (keepCamera) return;

    if (followedDroneId && routeCartesiansByDrone[followedDroneId]) {
      followDrone(followedDroneId);
    } else if (viewer.entities.values.length > 0) {
      viewer.zoomTo(viewer.entities, new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-35)));
    }
  }

  function renderTimeSyncMarkers(targets) {
    // Same accepted/draft summary and target lookup as Grid/Map's
    // drawTimeSyncMarkers. Imported selections alone do not claim a solved ToT.
    const sync = typeof getActiveTimeSync === "function" ? getActiveTimeSync() : state.timeSync;
    if (!sync?.enabled || !["synchronized", "infeasible"].includes(sync.status)) return;
    const members = Array.isArray(sync.synchronized_waypoints) ? sync.synchronized_waypoints : [];
    const targetById = new Map();
    for (const items of [targets, state.env?.targets, state.initialEnvSnapshot?.targets]) {
      for (const target of items || []) {
        const id = String(target.id || target.label || "");
        if (id && Number.isFinite(target.lat) && Number.isFinite(target.lng) && !targetById.has(id)) {
          targetById.set(id, target);
        }
      }
    }

    const synchronized = sync.status === "synchronized";
    const outerRadius = () => 13 + 7 * (synchronized
      ? (Math.sin(Date.now() / 220) + 1) * 0.5 : 0.35);
    const labelColor = Cesium.Color.fromCssColorString(synchronized ? "#cffafe" : "#fde68a");
    for (const member of members) {
      const target = targetById.get(String(member.waypoint));
      if (!target) continue;
      const did = member.drone_id != null ? ` D${member.drone_id}` : "";
      const name = `${member.waypoint} ToT${did}`;
      const position = Cesium.Cartesian3.fromDegrees(target.lng, target.lat, (target.alt_km || 0) * 1000);
      // Screen-facing rings stay readable when the camera tilts or zooms,
      // like the pixel-sized target dots. The outer ring breathes; the inner
      // ring stays fixed. Cesium owns updates and disposal with its entities.
      viewer.entities.add({
        name,
        position,
        billboard: {
          image: makeTimeSyncIcon(synchronized, false),
          verticalOrigin: Cesium.VerticalOrigin.CENTER,
          scale: new Cesium.CallbackProperty(() => outerRadius() / 20, false),
          eyeOffset: new Cesium.Cartesian3(0, 0, -25),
        },
        label: {
          text: `${synchronized ? "SYNC" : "SYNC BLOCKED"}${did}`,
          font: "bold 10px sans-serif", fillColor: labelColor,
          outlineColor: Cesium.Color.BLACK, outlineWidth: 2,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          showBackground: true,
          backgroundColor: Cesium.Color.fromCssColorString("#020617").withAlpha(0.84),
          backgroundPadding: new Cesium.Cartesian2(4, 2),
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          pixelOffset: new Cesium.CallbackProperty(() => new Cesium.Cartesian2(0, -outerRadius() - 4), false),
          eyeOffset: new Cesium.Cartesian3(0, 0, -25),
        },
      });
      viewer.entities.add({
        name: `${name} inner`,
        position,
        billboard: {
          image: makeTimeSyncIcon(synchronized, true),
          verticalOrigin: Cesium.VerticalOrigin.CENTER,
          eyeOffset: new Cesium.Cartesian3(0, 0, -25),
        },
      });
    }
  }

  const timeSyncIconCache = new Map();
  function makeTimeSyncIcon(synchronized, inner) {
    const key = `${synchronized}:${inner}`;
    if (timeSyncIconCache.has(key)) return timeSyncIconCache.get(key);
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 48;
    const ctx = canvas.getContext("2d");
    ctx.beginPath();
    ctx.arc(24, 24, inner ? 13 * 0.62 : 20, 0, Math.PI * 2);
    if (!inner) {
      ctx.fillStyle = synchronized ? "rgba(6, 182, 212, 0.16)" : "rgba(245, 158, 11, 0.14)";
      ctx.fill();
    }
    ctx.strokeStyle = synchronized ? "#06b6d4" : "#f59e0b";
    ctx.lineWidth = inner ? 1.5 : synchronized ? 2.5 : 2;
    if (!synchronized && !inner) ctx.setLineDash([5, 4]);
    ctx.stroke();
    const image = canvas.toDataURL();
    timeSyncIconCache.set(key, image);
    return image;
  }

  function populateFollowDropdown() {
    const select = $("cesium-follow-drone");
    if (!select) return;
    const currentValue = select.value;
    const droneIds = Object.keys(routeCartesiansByDrone).sort();
    select.innerHTML = '<option value="">None</option>' +
      droneIds.map((did) => `<option value="${did}">D${did}</option>`).join("");
    if (droneIds.includes(currentValue)) select.value = currentValue;
  }

  function setPreset(headingDeg, pitchDeg) {
    if (!viewer || viewer.entities.values.length === 0) return;
    viewer.zoomTo(
      viewer.entities,
      new Cesium.HeadingPitchRange(Cesium.Math.toRadians(headingDeg), Cesium.Math.toRadians(pitchDeg))
    );
  }

  function followDrone(did) {
    followedDroneId = did || null;
    if (!viewer) return;
    if (!did) {
      if (viewer.entities.values.length > 0) {
        viewer.zoomTo(viewer.entities, new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-35)));
      }
      return;
    }

    // If the marker already exists (animation has reached/passed this
    // drone), snap the camera to it immediately via the SAME chase-cam math
    // applyChaseCam uses every frame during animation — so picking a drone
    // mid-flight doesn't leave the view stale until the next frame tick.
    if (droneMarkers[did]) {
      applyChaseCam(did);
      return;
    }

    // Marker doesn't exist yet (animation hasn't reached this drone) — frame
    // its static route instead so the view isn't left wherever it was.
    // updateDronePosition() picks up live chase-cam once the marker exists.
    const droneEntities = viewer.entities.values.filter(
      (e) => e.name === `D${did} route` || e.name === `D${did} marker`
    );
    if (droneEntities.length > 0) {
      viewer.zoomTo(droneEntities, new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-30)));
    } else {
      appendDebugLine(`⚠️ Cesium 3D view: no entities found to follow for D${did}`);
    }
  }

  const DEFAULT_CHASE_BACK_M = 300;
  const DEFAULT_CHASE_UP_M = 150;
  // Chase-cam turn smoothing: time constant (s) of the camera's heading. At a
  // waypoint the drone's heading changes in one frame; the camera eases toward
  // it (~95% after 3x this). 0 = snap instantly (the old behaviour).
  const DEFAULT_CHASE_SMOOTH_S = 0.6;

  function currentChaseSmoothSeconds() {
    const input = $("cesium-chase-smooth-s");
    const v = input ? parseFloat(input.value) : NaN;
    return Number.isFinite(v) && v >= 0 ? v : DEFAULT_CHASE_SMOOTH_S;
  }

  // Ease the camera's horizontal heading toward the drone's, rotating about the
  // local up axis — a sharp or U-turn swings smoothly left/right instead of
  // passing through a degenerate in-between direction.
  function smoothedCamForward(did, rawForward, up) {
    const horizontal = (v) => {
      const h = Cesium.Cartesian3.subtract(
        v, Cesium.Cartesian3.multiplyByScalar(up, Cesium.Cartesian3.dot(v, up), new Cesium.Cartesian3()),
        new Cesium.Cartesian3()
      );
      const len = Cesium.Cartesian3.magnitude(h);
      return len > 1e-9 ? Cesium.Cartesian3.divideByScalar(h, len, h) : null;
    };
    const target = horizontal(rawForward);
    const now = Date.now();
    const prev = droneCamForward[did] ? horizontal(droneCamForward[did]) : null;
    const dt = droneCamTime[did] ? Math.min(0.5, (now - droneCamTime[did]) / 1000) : 0;
    droneCamTime[did] = now;
    if (!target) return prev || rawForward;
    const tau = currentChaseSmoothSeconds();
    if (!prev || tau <= 0) {
      droneCamForward[did] = target;
      return target;
    }
    const cross = Cesium.Cartesian3.cross(prev, target, new Cesium.Cartesian3());
    const angle = Math.atan2(Cesium.Cartesian3.dot(cross, up), Cesium.Cartesian3.dot(prev, target));
    const step = angle * (1 - Math.exp(-dt / tau));
    const q = Cesium.Quaternion.fromAxisAngle(up, step);
    const rot = Cesium.Matrix3.fromQuaternion(q);
    const next = Cesium.Matrix3.multiplyByVector(rot, prev, new Cesium.Cartesian3());
    Cesium.Cartesian3.normalize(next, next);
    droneCamForward[did] = next;
    return next;
  }

  function currentChaseOffsets() {
    const backInput = $("cesium-chase-back-m");
    const upInput = $("cesium-chase-up-m");
    const back = backInput ? parseFloat(backInput.value) : NaN;
    const up = upInput ? parseFloat(upInput.value) : NaN;
    return {
      back: Number.isFinite(back) ? back : DEFAULT_CHASE_BACK_M,
      up: Number.isFinite(up) ? up : DEFAULT_CHASE_UP_M,
    };
  }

  function isFiniteCartesian3(v) {
    return v && Number.isFinite(v.x) && Number.isFinite(v.y) && Number.isFinite(v.z);
  }

  function logExit(did, reason) {
    if (!logExit._logged) logExit._logged = {};
    if (logExit._logged[reason]) return;
    logExit._logged[reason] = true;
    appendDebugLine(`🎥 applyChaseCam(D${did}) early-return: ${reason} (one-time log per reason)`);
  }

  function applyChaseCam(did) {
    // Owns the camera outright every frame — no trackedEntity/viewFrom.
    // Those were tried first (matching Cesium's intended API) but produced
    // inconsistent behind-ness depending on travel direction (fine heading
    // north, wrong heading east/west) — that's Cesium's own internal
    // EntityView velocity-basis heuristic, not something tunable from here.
    // This directly computes heading from consecutive real positions
    // (already being tracked in droneLastPosition for exactly this) and
    // sets the camera explicitly — same technique proven to work in the
    // original standalone 3D-spike chase-cam.
    //
    // Guarded defensively (try/catch + finite-value checks): an earlier
    // version fed camera.setView() a `forward` vector with a vertical
    // component (present during climb/descent — see rampAltitudeAtDistance)
    // alongside a purely-geocentric `up`, which are close to parallel near
    // the poles of a climb/descent and can degenerate into NaN inside
    // Cesium's internal orientation math. A NaN camera position/orientation
    // can make the WebGL renderer effectively hang each frame — this is the
    // leading suspect for the full-browser freeze reported during a 2-drone
    // climb/descent test. try/catch here turns that into a recoverable
    // skipped frame (logged once) instead of an unrecoverable freeze.
    try {
      const marker = droneMarkers[did];
      const lastPos = droneLastPosition[did];
      if (!viewer || !marker) {
        logExit(did, "no viewer/marker");
        return;
      }
      // BUG THAT MADE FOLLOW NEVER MOVE THE CAMERA: entities.add({position:
      // someCartesian3}) does NOT store the raw Cartesian3 on the entity —
      // Cesium wraps it in a ConstantPositionProperty internally. Reading
      // marker.position back gives that wrapper object, whose .x/.y/.z are
      // all undefined (real Cesium behavior, confirmed via debug logging:
      // every single call hit "position not finite"). Must call
      // .getValue(time) to extract the actual Cartesian3.
      const position = marker.position.getValue(Cesium.JulianDate.now());
      if (!isFiniteCartesian3(position)) {
        logExit(did, "position not finite");
        return;
      }

      const up = Cesium.Cartesian3.normalize(position, new Cesium.Cartesian3()); // geocentric up

      // Heading fallback: falling back to viewer.camera.direction was a
      // self-reinforcing bug — during a stationary waypoint pause (drones
      // hold for ~0.5s at airport departures; see AIRPORT_DEPARTURE_PAUSE_
      // MISSION_SECONDS in isr.js), lastPos≈position for many consecutive
      // frames, so `forward` kept reusing whatever camera.direction the
      // PREVIOUS setView call just set — a stable fixed point that locks
      // onto the pre-Follow (Oblique) direction and never escapes, exactly
      // matching "Follow shows Oblique regardless of drone." Fall back to
      // the drone's OWN route heading (last known good forward, cached
      // separately from droneLastPosition) instead of the camera's own
      // output — breaks the feedback loop.
      let forward;
      if (lastPos && isFiniteCartesian3(lastPos) && Cesium.Cartesian3.distance(lastPos, position) > 1e-3) {
        forward = Cesium.Cartesian3.subtract(position, lastPos, new Cesium.Cartesian3());
        Cesium.Cartesian3.normalize(forward, forward);
        droneLastForward[did] = forward;
      } else if (droneLastForward[did]) {
        forward = droneLastForward[did];
      } else {
        forward = Cesium.Cartesian3.clone(viewer.camera.direction);
      }

      // Reject a near-parallel forward/up pair (steep climb/descent) rather
      // than hand Cesium a near-degenerate orientation basis — fall back to
      // the camera's current direction for this one frame instead.
      if (Math.abs(Cesium.Cartesian3.dot(forward, up)) > 0.999) {
        forward = Cesium.Cartesian3.clone(viewer.camera.direction);
      }

      // The camera follows a SMOOTHED heading so waypoint turns swing the view
      // gradually instead of in one frame.
      forward = smoothedCamForward(did, forward, up);

      const { back: chaseBackM, up: chaseUpM } = currentChaseOffsets();
      const camPos = Cesium.Cartesian3.clone(position);
      const backOffset = Cesium.Cartesian3.multiplyByScalar(forward, chaseBackM, new Cesium.Cartesian3());
      Cesium.Cartesian3.subtract(camPos, backOffset, camPos);
      const upOffset = Cesium.Cartesian3.multiplyByScalar(up, chaseUpM, new Cesium.Cartesian3());
      Cesium.Cartesian3.add(camPos, upOffset, camPos);

      // (Removed a "camPos == position" degenerate-check here that should
      // never legitimately trigger by construction — camPos is always
      // offset by ~sqrt(chaseBackM² + chaseUpM²). Removing it in
      // case it was somehow the culprit; added exit-point logging below
      // since the one-time "applying chase-cam" log never fired and this
      // wasn't the only candidate.)

      // Look back toward the drone (not purely along `forward`) so it stays
      // centered in frame instead of drifting toward the top/bottom edge.
      const lookDir = Cesium.Cartesian3.subtract(position, camPos, new Cesium.Cartesian3());
      Cesium.Cartesian3.normalize(lookDir, lookDir);

      if (!isFiniteCartesian3(camPos) || !isFiniteCartesian3(lookDir) || !isFiniteCartesian3(up)) {
        if (!applyChaseCam._loggedInvalid) {
          applyChaseCam._loggedInvalid = true;
          appendDebugLine(`⚠️ Cesium chase-cam: invalid camPos/lookDir/up for D${did}, skipping (one-time log)`);
        }
        return;
      }

      if (!applyChaseCam._loggedOnce) {
        applyChaseCam._loggedOnce = true;
        appendDebugLine(`🎥 Cesium chase-cam applying for D${did}: camPos=(${camPos.x.toFixed(0)},${camPos.y.toFixed(0)},${camPos.z.toFixed(0)})`);
      }

      viewer.camera.setView({ destination: camPos, orientation: { direction: lookDir, up } });
    } catch (err) {
      if (!applyChaseCam._warned) {
        applyChaseCam._warned = true;
        appendDebugLine(`⚠️ Cesium chase-cam error (D${did}, suppressing further logs): ${err.message}`);
      }
    }
  }

  function updateDronePosition(did, lat, lng, distanceTraveledKm, totalDistanceKm) {
    // Called every animation frame (from drawEnvironment's per-drone loop in
    // isr.js) with the SAME lat/lng already computed for the 2D canvas.
    // Creates the marker entity on first call for this drone, then just
    // updates .position on every subsequent call — plain Cartesian3, no
    // clock/PositionProperty dependency (see applyChaseCam's comment for why
    // trackedEntity/viewFrom were abandoned). Altitude follows the identical
    // climb/descent ramp as the static route polyline (rampAltitudeAtDistance).
    if (!viewer) return;
    if (typeof lat !== "number" || typeof lng !== "number") return;

    const cruiseAltM = droneCruiseAltM(did);
    const totalKm = totalDistanceKm || 0;
    const profile = flightProfile(did);
    const alt = profile
      ? profileAltitudeM(profile, distanceTraveledKm || 0)
      : totalKm > 0
      ? rampAltitudeAtDistance(distanceTraveledKm || 0, totalKm - (distanceTraveledKm || 0), totalKm, cruiseAltM, did)
      : 0;

    const position = Cesium.Cartesian3.fromDegrees(lng, lat, alt);

    let marker = droneMarkers[did];
    if (!marker) {
      marker = viewer.entities.add({
        name: `D${did} marker`,
        position,
        point: {
          pixelSize: 14,
          color: droneColor(did),
          outlineColor: Cesium.Color.WHITE,
          outlineWidth: 2,
        },
        label: {
          text: `D${did}`,
          font: "12px sans-serif",
          fillColor: Cesium.Color.WHITE,
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 2,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          pixelOffset: new Cesium.Cartesian2(0, -16),
        },
      });
      droneMarkers[did] = marker;
    } else {
      marker.position = position;
    }

    if (!updateDronePosition._loggedFollowCheck) {
      updateDronePosition._loggedFollowCheck = true;
      appendDebugLine(`🎥 updateDronePosition check: did="${did}" followedDroneId="${followedDroneId}" match=${followedDroneId === did}`);
    }
    if (followedDroneId === did) {
      applyChaseCam(did);
    }
    droneLastPosition[did] = position;
  }

  let squareIconCache = null;
  function makeSquareIcon() {
    // Canvas-drawn unrotated square, matching the 2D canvas's real-airport
    // glyph exactly (fill #3b82f6, stroke #1d4ed8) for visual consistency.
    if (squareIconCache) return squareIconCache;
    const c = document.createElement("canvas");
    c.width = 24;
    c.height = 24;
    const ctx = c.getContext("2d");
    ctx.fillStyle = "#3b82f6";
    ctx.strokeStyle = "#1d4ed8";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.rect(2, 2, 20, 20);
    ctx.fill();
    ctx.stroke();
    squareIconCache = c.toDataURL();
    return squareIconCache;
  }

  let diamondIconCache = null;
  function makeDiamondIcon() {
    // Canvas-drawn 45°-rotated square, matching the 2D canvas's tanker glyph
    // (orange fill, dark-orange outline) so tankers read the same way in 3D.
    if (diamondIconCache) return diamondIconCache;
    const c = document.createElement("canvas");
    c.width = 28;
    c.height = 28;
    const ctx = c.getContext("2d");
    ctx.translate(14, 14);
    ctx.rotate(Math.PI / 4);
    ctx.fillStyle = "#f97316";
    ctx.strokeStyle = "#c2410c";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.rect(-8, -8, 16, 16);
    ctx.fill();
    ctx.stroke();
    diamondIconCache = c.toDataURL();
    return diamondIconCache;
  }

  let visitedIconCache = null;
  function makeVisitedIcon() {
    // The 2D canvas's visited mark: a green X.
    if (visitedIconCache) return visitedIconCache;
    const c = document.createElement("canvas");
    c.width = 28;
    c.height = 28;
    const ctx = c.getContext("2d");
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(4, 4);
    ctx.lineTo(24, 24);
    ctx.moveTo(24, 4);
    ctx.lineTo(4, 24);
    ctx.strokeStyle = "rgba(0, 0, 0, 0.7)"; // dark edge so it reads on terrain
    ctx.lineWidth = 6;
    ctx.stroke();
    ctx.strokeStyle = "#22c55e";
    ctx.lineWidth = 3.5;
    ctx.stroke();
    visitedIconCache = c.toDataURL();
    return visitedIconCache;
  }

  const cutIconCache = {};
  function makeCutIcon(isLost) {
    // The 2D canvas's cut marker: an outlined diamond, white (red when lost).
    const key = isLost ? "lost" : "cut";
    if (cutIconCache[key]) return cutIconCache[key];
    const r = isLost ? 12 : 8;
    const size = 2 * r + 6;
    const c = document.createElement("canvas");
    c.width = size;
    c.height = size;
    const ctx = c.getContext("2d");
    const m = size / 2;
    ctx.beginPath();
    ctx.moveTo(m, m - r);
    ctx.lineTo(m + r, m);
    ctx.lineTo(m, m + r);
    ctx.lineTo(m - r, m);
    ctx.closePath();
    ctx.strokeStyle = isLost ? "#ef4444" : "#ffffff";
    ctx.lineWidth = isLost ? 3 : 2.5;
    ctx.stroke();
    cutIconCache[key] = c.toDataURL();
    return cutIconCache[key];
  }

  // Test hook: names of the entities currently in the 3D scene.
  function _entityNames() { return viewer ? viewer.entities.values.map((e) => e.name) : []; }
  // Test hook: heights (m) at the ends of each drone's route line.
  function _routeEndHeights() {
    const out = {};
    Object.entries(routeCartesiansByDrone).forEach(([did, cs]) => {
      const h = (c) => Math.round(Cesium.Cartographic.fromCartesian(c).height);
      out[did] = [h(cs[0]), h(cs[cs.length - 1])];
    });
    return out;
  }

  // Test hook: camera height (m).
  function _camHeight() { return viewer ? viewer.camera.positionCartographic.height : -1; }

  return { subscribeLifecycle, _camHeight, _entityNames, _routeEndHeights, init, destroy, render, isActive, setPreset, followDrone, updateDronePosition, groundElevationsKm, setMapType };
})();
