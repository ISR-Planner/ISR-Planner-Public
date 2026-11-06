// isr.js  — single-file version with:
// - Edit mode ON/OFF
// - Canvas drawing + editing (airports/targets/SAMs)
// - Drone config + sequences
// - Stats
// - Import/Export
// - Planner integration

"use strict";

// VERSION CHECK - Fixed multiple trajectories after accept 2026-01-29
const ISR_CODE_VERSION = "2026-01-29-single-traj-fix";

// ----------------------------------------------------
// DOM helper
// ----------------------------------------------------
const { domHelpers, debugOutput, voiceControls, traceView, chatView, fleetDrawers, memoryView } = ISRFrontendUI.create({
  document,
  factories: {
    dom: ISRDom,
    debugOutput: ISRDebugOutput,
    voiceControls: ISRVoiceControls,
    traceView: ISRTraceView,
    chatView: ISRChatView,
    fleetDrawers: ISRFleetDrawers,
    memoryView: ISRMemoryView,
  },
  getById: id => $(id),
  escapeHtml: text => escapeHtml(text),
  getVoice: () => window.ISRVoice,
  fetchRequest: (url, options) => fetch(url, options),
  formatDebugTime: () => new Date().toLocaleTimeString(),
  formatTraceTime: ts => new Date(ts).toLocaleTimeString(),
  formatChatTime: () => new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
  createMessageId: () => "msg-" + Date.now() + "-" + Math.random().toString(36).substring(2, 7),
});

// Compatibility entrypoints for existing scripts and callers.
function $(id) {
  return domHelpers.getById(id);
}

function setText(id, text) {
  return domHelpers.setText(id, text);
}

// ----------------------------------------------------
// Global state
// ----------------------------------------------------
// A drone's climb/descent angle when none is set (degrees).
const DEFAULT_CLIMB_ANGLE_DEG = 12;

// ----------------------------------------------------
// Target categories: what each target letter means (a: Personnel, ...). The
// letter is the category id stored on targets and in drone settings
// (target_access / detection_radius / loiter_times). The list comes from the
// mission (env.target_categories, editable in the Assets tab), else the catalog,
// else this default (same as catalog/asset_catalog.json).
// ----------------------------------------------------
const MAX_TARGET_CATEGORIES = 10;  // colours stay distinct on the map
const CATEGORY_PALETTE = ["#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#a855f7", "#06b6d4", "#eab308", "#ec4899", "#84cc16", "#f97316"];
const DEFAULT_TARGET_CATEGORIES = [
  { id: "a", name: "Personnel", examples: "troops, teams, gatherings", color: "#3b82f6" },
  { id: "b", name: "Light vehicles", examples: "trucks, pickups, convoys", color: "#22c55e" },
  { id: "c", name: "Heavy / armoured vehicles", examples: "tanks, artillery, launchers", color: "#f59e0b" },
  { id: "d", name: "Emitters / radar sites", examples: "radars, SAM radars, jammers, AAA", color: "#ef4444" },
  { id: "e", name: "Command & control", examples: "HQs, command posts, relays", color: "#a855f7" },
  { id: "f", name: "Infrastructure", examples: "bridges, dams, power, depots", color: "#06b6d4" },
  { id: "g", name: "Maritime", examples: "ships, patrol boats", color: "#eab308" }
];

function targetCategories() {
  const st = typeof state !== "undefined" ? state : null;
  const fromEnv = st?.env?.target_categories;
  if (Array.isArray(fromEnv) && fromEnv.length) return fromEnv;
  const fromCatalog = st?.assetCatalog?.target_categories;
  if (Array.isArray(fromCatalog) && fromCatalog.length) return fromCatalog;
  return DEFAULT_TARGET_CATEGORIES;
}
function categoryIds() {
  return targetCategories().map((c) => String(c.id).toLowerCase());
}
function categoryOf(id) {
  const key = String(id || "a").toLowerCase();
  return targetCategories().find((c) => String(c.id).toLowerCase() === key) || null;
}
function categoryColor(id) {
  const cat = categoryOf(id);
  if (cat?.color) return cat.color;
  const i = categoryIds().indexOf(String(id || "a").toLowerCase());
  return CATEGORY_PALETTE[(i < 0 ? 0 : i) % CATEGORY_PALETTE.length];
}
function categoryName(id) {
  return categoryOf(id)?.name || `Type ${String(id || "a").toUpperCase()}`;
}
// Where each target was drawn on the canvas (drawEnvironment), for the hover label.
let targetScreenPositions = [];

// One value per category: { a: v, b: v, ... }.
function perCategory(value) {
  return Object.fromEntries(categoryIds().map((id) => [id, value]));
}
// A drone's per-category settings with every current category present: one the
// dict lacks (a category added later) gets `fill`.
function withAllCategories(dict, fill) {
  const out = { ...(dict || {}) };
  categoryIds().forEach((id) => { if (out[id] === undefined) out[id] = fill; });
  return out;
}

// Units. The planner's time unit is the MINUTE and distance/fuel is km, so a
// drone's `speed` is stored in km per minute and loiter times are minutes:
// loiter fuel = minutes x km/min = km flown in the orbit. The Asset tab shows
// speed in km/h. (Older files: speed 1 = 60 km/h, their fuel numbers unchanged.)
const MINUTES_PER_HOUR = 60;
function speedKmPerMinFromInput(input) {
  const kmh = parseFloat(input?.value);
  return kmh > 0 ? kmh / MINUTES_PER_HOUR : 1;
}
function showSpeedKmh(input, kmPerMin) {
  if (input) input.value = Math.round((Number(kmPerMin) || 1) * MINUTES_PER_HOUR * 10) / 10;
}

// A drone may refuel at a tanker only within this much of its cruise altitude
// (same rule as server/solver/flight_profile.py MAX_TANKER_ALTITUDE_CHANGE_KM).
const MAX_TANKER_ALTITUDE_CHANGE_KM = 2;

const state = {
  env: null,                 // environment JSON (airports, targets, sams)
  envFilename: null,         // name of JSON file

  sequences: {},             // { "1": "A1,T1,A1", ... }
  routes: {},                // { "1": {route, points, distance, fuel_budget}, ... }
  wrappedPolygons: [],       // wrapped SAM polygons from solver
  samSliceGroups: [],        // per-altitude SAM slice outlines when drones fly different altitudes
  timeSync: null,             // latest time-on-target sync summary from solver
  currentDroneForSeq: "1",

  droneConfigs: {},          // { "1": {enabled, fuel_budget, start_airport, end_airport, target_access}, ... }

  // DURABLE per-leg fuel store: { "1": kmUsedThisLeg, ... }. This is the single
  // home for "fuel used in the current leg". Unlike animation.drones[did]
  // .legBurnedDistance (which startAnimation wipes on every (re)start), this
  // survives cuts — so the stats numerator does NOT reset after a cut. It
  // accumulates across cuts (a cut is fuel-neutral) and resets to 0 only when the
  // drone crosses a refuel airport (new leg). legBurnedDistance is seeded FROM
  // this on each startAnimation so the live counter continues where the leg left off.
  legFuel: {},

  // DURABLE finalized per-leg fuel: { "1": [leg1Used, leg2Used, ...], ... }. Each
  // entry is a COMPLETED leg's fuel (frozen at the refuel crossing that ended it).
  // The ACTIVE (not-yet-finished) leg lives in legFuel[did]. Box numerator for a
  // refuel drone = legFuelHistory[did].join("+") + "+" + activeLeg. At a refuel
  // crossing the active leg is pushed here and legFuel[did] resets to 0.
  legFuelHistory: {},

  missionId: null,           // current v4 mission_id from the backend
  missionName: null,         // officer-declared mission name/number (§1a-0)
  missionStatus: null,       // "active" | "ended" | null (no mission yet)

  solveAbortController: null, // AbortController for canceling solver requests

  // Editing-related
  editMode: true,            // Edit: ON/OFF
  addMode: null,             // "airport" | "target" | "sam" | null
  selectedObject: null,      // { kind: "airport"|"target"|"sam", index: number } or null
  dragging: null,            // { kind, index, offsetX, offsetY } during drag

  // SAM wrapping debounce
  samWrappingTimeout: null,  // timeout ID for debounced wrapping updates
  wrappingAbortController: null,  // AbortController for canceling pending wrapping requests

  // Trajectory visibility
  trajectoryVisible: {
    "1": true,
    "2": true,
    "3": true,
    "4": true,
    "5": true,
  },

  // Animation state
  animation: {
    active: false,
    drones: {},              // { "1": { progress: 0, animating: false }, ... }
    animationId: null,       // requestAnimationFrame ID
    speedMultiplier: 1,      // Effective playback speed, including the selected boost
  },

  // Checkpoint / segmented solve state
  checkpoint: {
    active: false,
    pct: 0.5,
    segments: {},   // per drone: { prefix, suffix, splitPoint, checkpointDist }
  },

  // Mission history for segmented replanning
  missionHistory: [],          // Array of segment snapshots
  initialEnvSnapshot: null,    // Immutable copy of original env for reset
  visitedTargets: [],          // Target IDs completed across all segments
  visitedTargetByDrone: {},    // { targetId: droneId } owner for per-drone stats

  // Single cut distance captured during freezeAtCheckpoint (same for all drones)
  pendingCutDistance: null,

  // Target-to-segment mapping for visualization
  // { "T1": 1, "T2": 1, "T3": 2, ... } - which segment each target belongs to
  targetSegmentMap: {},

  // Current segment number for dynamic cut-based segment assignment
  // Starts at 1, increments with each Cut button press
  currentCutSegment: 1,

  // Track which targets were assigned in previous cuts (to avoid reassigning)
  previouslyAssignedTargets: [],

  // Per-drone cut positions captured during freezeAtCheckpoint
  // { [did]: [x,y] } - used when committing segments
  pendingCutPositions: null,

  // Checkpoints for the current cut - per drone (e.g., { "1": {id: "C1-1", x: 50, y: 30}, "2": {id: "C1-2", x: 65, y: 45} })
  // Named as C{segment}-{drone} (e.g., C1-1 = Checkpoint 1 for Drone 1)
  pendingCheckpoints: null,

  // Counter for checkpoint segment number (C1, C2, C3, ...)
  checkpointSegmentCounter: 0,

  // Drones lost at the current checkpoint (disabled after cut but before solve)
  // Array of drone IDs: ["1", "3"] - used when drawing lost drone markers
  pendingLostDrones: [],

  // Drones disabled during live edit (marked with red diamond at checkpoint)
  liveEditDisabledDrones: [],

  // Legacy single cut position (backward compatibility)
  pendingCutPosition: null,

  // Agent trace runs for Agents Trace tab
  agentTraceRuns: [],  // Array of { ts, user_message, mission_id, trace_events }
};

// ----------------------------------------------------
// Map Integration (Leaflet underlay)
// ----------------------------------------------------
const mapState = {
  active: false,
  leafletMap: null,
  resizeTimer: null,
  tileLayer: null,
  mapType: "street",
  // Bounds must have EQUAL degree spans for lat and lng (both 0.15°)
  // This ensures fitBounds() maps a square region to square container
  // without zoom adjustment that would break coordinate transformation
  bounds: { south: 37.70, north: 37.85, west: -122.51, east: -122.36 },
};

// ====================================================================
// MAP VIEW INITIALIZATION
//
// Single source of truth: viewState (centerLat, centerLng, zoomLevel).
// Anchor point: mapState.bounds, re-centered on viewState whenever the
// "home" location changes (init, env import).
//
// On startup, viewState is populated by exactly one of three sources,
// in priority order:
//   1. localStorage  — a previously saved real user view
//   2. geolocation   — first-time visitors who grant permission
//   3. SF default    — anything else
//
// resetView() ("Home" — Option A): snap back to the current anchor
// (mapState.bounds center, zoom 13). Does NOT re-prompt geolocation.
//
// Rules for changes to this file:
//   - No flags like "did this come from a fallback." If the data
//     can't tell you, the data model is wrong — fix the model.
//   - Only persist viewState that came from geolocation, localStorage
//     restore, env import, or user interaction. Never persist the SF
//     default — it's what we use when nothing is stored.
//   - Adding state requires a one-line comment saying why.
//   - Two patches to this code within a week = stop and rewrite.
// ====================================================================

const VIEW_STORAGE_KEY = 'isrLastView';
const VIEW_STORAGE_TTL_MS = 30 * 24 * 60 * 60 * 1000;  // 30 days
const SF_DEFAULT = Object.freeze({ centerLat: 37.775, centerLng: -122.435, zoomLevel: 13 });

const viewPreferences = ISRViewPreferences.create({
  getStorage: () => localStorage,
  now: () => Date.now(),
  storageKey: VIEW_STORAGE_KEY,
  ttlMs: VIEW_STORAGE_TTL_MS,
  defaultZoom: SF_DEFAULT.zoomLevel,
  normalizeView: view => migrateLegacyMapCoordinates(view),
});

// SINGLE-WORLD COORDINATE CONTRACT
// Mission lat/lng are geographic coordinates, never offsets into repeated map
// tiles. Runtime editing rejects locations outside this world; only the import
// boundary repairs files written by the old, unrestricted map editor.
function migrateLegacyMapCoordinates(data) {
  const longitude = value => Number.isFinite(value) && (value < -180 || value > 180)
    ? ((value + 180) % 360 + 360) % 360 - 180 : value;
  const pairs = value => {
    if (!Array.isArray(value)) return visit(value);
    if (value.length >= 2 && Number.isFinite(value[0]) && Number.isFinite(value[1])) {
      return [value[0], longitude(value[1]), ...value.slice(2)];
    }
    return value.map(pairs);
  };
  const visit = value => {
    if (Array.isArray(value)) return value.map(visit);
    if (!value || typeof value !== "object") return value;
    const result = {};
    for (const [key, entry] of Object.entries(value)) {
      if (key === "lng" || key === "centerLng") result[key] = longitude(entry);
      else if (key === "trajectory" || key === "wrapped_polygons") result[key] = pairs(entry);
      else if (key === "cutPosition" || key === "cutPositions") {
        result[key] = Array.isArray(entry) ? pairs(entry) : entry &&
          Object.fromEntries(Object.entries(entry).map(([id, point]) => [id, pairs(point)]));
      } else result[key] = visit(entry);
    }
    // Saved viewport bounds use west/east, not lng. Preserve the span while
    // moving an old repeated-world anchor onto the single geographic world.
    const b = result.bounds;
    if (b && !Array.isArray(b) && Number.isFinite(b.west) && Number.isFinite(b.east)) {
      const center = (b.west + b.east) / 2;
      const shift = longitude(center) - center;
      b.west += shift; b.east += shift;
    }
    return result;
  };
  return visit(data);
}

function mercatorPoint(lat, lng, zoom) {
  const scale = 256 * Math.pow(2, zoom);
  const phi = Math.max(-85.0511287798066, Math.min(85.0511287798066, lat)) * Math.PI / 180;
  return [(lng + 180) / 360 * scale,
    (1 - Math.log(Math.tan(phi) + 1 / Math.cos(phi)) / Math.PI) / 2 * scale];
}

function mercatorLat(y, zoom) {
  return Math.atan(Math.sinh(Math.PI * (1 - 2 * y / (256 * Math.pow(2, zoom))))) * 180 / Math.PI;
}

function mapCanvasScale(canvas) {
  const rect = canvas.getBoundingClientRect?.();
  return [canvas.width / (rect?.width || canvas.width), canvas.height / (rect?.height || canvas.height)];
}

function mapPointToCanvas(canvas, lat, lng) {
  const point = mercatorPoint(lat, lng, viewState.zoomLevel);
  const center = mercatorPoint(viewState.centerLat, viewState.centerLng, viewState.zoomLevel);
  const [sx, sy] = mapCanvasScale(canvas);
  return [canvas.width / 2 + (point[0] - center[0]) * sx, canvas.height / 2 + (point[1] - center[1]) * sy];
}

function constrainMapView() {
  const canvas = $("env-canvas");
  const scale = 256 * Math.pow(2, viewState.zoomLevel);
  const [sx, sy] = canvas ? mapCanvasScale(canvas) : [1, 1];
  const halfX = Math.min(scale / 2, (canvas?.width || 0) / sx / 2);
  const halfY = Math.min(scale / 2, (canvas?.height || 0) / sy / 2);
  const [x, y] = mercatorPoint(viewState.centerLat, viewState.centerLng, viewState.zoomLevel);
  viewState.centerLng = Math.max(halfX, Math.min(scale - halfX, x)) / scale * 360 - 180;
  viewState.centerLat = mercatorLat(Math.max(halfY, Math.min(scale - halfY, y)), viewState.zoomLevel);
}

// A geographic leg crossing the date line has two pieces on a single flat
// world. Split it at the edges instead of drawing a chord across the continents.
function appendMapPath(ctx, points, canvas, closed = false) {
  if (!points.length) return;
  const zoom = viewState.zoomLevel;
  const center = mercatorPoint(viewState.centerLat, viewState.centerLng, zoom);
  const [sx, sy] = mapCanvasScale(canvas);
  const project = (lat, lng) => {
    const point = mercatorPoint(lat, lng, zoom);
    return [canvas.width / 2 + (point[0] - center[0]) * sx,
      canvas.height / 2 + (point[1] - center[1]) * sy];
  };
  const path = closed ? [...points, points[0]] : points;
  let previous = path[0];
  let pixel = project(previous[0], previous[1]);
  ctx.moveTo(...pixel);
  for (const point of path.slice(1)) {
    const next = project(point[0], point[1]);
    const delta = point[1] - previous[1];
    if (Math.abs(delta) > 180) {
      const edge = delta > 180 ? -180 : 180;
      const unwrappedEnd = point[1] + (delta > 180 ? -360 : 360);
      if (unwrappedEnd === previous[1]) {
        ctx.moveTo(...next);
        previous = point; pixel = next;
        continue;
      }
      const fraction = (edge - previous[1]) / (unwrappedEnd - previous[1]);
      const edgeY = pixel[1] + fraction * (next[1] - pixel[1]);
      ctx.lineTo(project(0, edge)[0], edgeY);
      ctx.moveTo(project(0, -edge)[0], edgeY);
    }
    ctx.lineTo(...next);
    previous = point; pixel = next;
  }
}

function interpolateLongitude(from, to, fraction) {
  let delta = to - from;
  if (delta > 180) delta -= 360;
  if (delta < -180) delta += 360;
  const lng = from + delta * fraction;
  return lng > 180 ? lng - 360 : lng < -180 ? lng + 360 : lng;
}

// Canvas draws straight screen segments, so approximate the shared flight
// curve to subpixel accuracy at the CURRENT zoom. This is display geometry
// only: never change the authoritative trajectory or its distance/time frame.
const mapFlightPathCache = new WeakMap();
function appendMapFlightPath(ctx, points, canvas) {
  if (!points.length) return;
  const zoom = viewState.zoomLevel;
  // Check content as well as identity: checkpoint anchoring may edit an array
  // in place. Retain only its current zoom, and release discarded routes via
  // WeakMap. Panning and playback do not change the sampled geographic curve.
  const signature = points.map(point => `${point[0]},${point[1]}`).join(';');
  const cached = mapFlightPathCache.get(points);
  if (cached?.zoom === zoom && cached.signature === signature) {
    appendMapPath(ctx, cached.sampled, canvas);
    return;
  }
  const sampled = [points[0]];
  const worldWidth = 256 * Math.pow(2, zoom);
  const project = (point, anchorX) => {
    const pixel = mercatorPoint(point[0], point[1], zoom);
    // Measure curvature continuously across the date line. appendMapPath
    // splits the resulting geographic samples at the actual world edges.
    pixel[0] += Math.round((anchorX - pixel[0]) / worldWidth) * worldWidth;
    return pixel;
  };
  const subdivide = (from, to, start, end, depth) => {
    const middle = interpolateFlightPosition(from, to, 0.5);
    const mid = project(middle, (start[0] + end[0]) / 2);
    let error = Math.hypot(mid[0] - (start[0] + end[0]) / 2,
      mid[1] - (start[1] + end[1]) / 2);
    // An arc crossing the equator can have its midpoint on the chord while
    // both halves curve away. Check the quarters as well as the midpoint.
    for (const t of [0.25, 0.75]) {
      const expected = [start[0] + t * (end[0] - start[0]), start[1] + t * (end[1] - start[1])];
      const actual = project(interpolateFlightPosition(from, to, t), expected[0]);
      error = Math.max(error, Math.hypot(actual[0] - expected[0], actual[1] - expected[1]));
    }
    if (error > 0.2 && depth < 20) {
      subdivide(from, middle, start, mid, depth + 1);
      subdivide(middle, to, mid, end, depth + 1);
    } else sampled.push(to);
  };
  for (let i = 1; i < points.length; i++) {
    const start = mercatorPoint(points[i - 1][0], points[i - 1][1], zoom);
    const end = project(points[i], start[0]);
    subdivide(points[i - 1], points[i], start, end, 0);
  }
  mapFlightPathCache.set(points, { zoom, signature, sampled });
  appendMapPath(ctx, sampled, canvas);
}
// END SINGLE-WORLD COORDINATE CONTRACT

// Re-center mapState.bounds on (lat, lng) while preserving its degree span.
// Bounds define the legacy coordinate anchor used by resetView() and the
// non-Leaflet grid renderer.
function centerMapBoundsOn(lat, lng) {
  const spanLat = (mapState.bounds.north - mapState.bounds.south);
  const spanLng = (mapState.bounds.east - mapState.bounds.west);
  mapState.bounds.south = lat - spanLat / 2;
  mapState.bounds.north = lat + spanLat / 2;
  mapState.bounds.west = lng - spanLng / 2;
  mapState.bounds.east = lng + spanLng / 2;
}

// ----------------------------------------------------
// Unified Viewport State (lat/lng center + zoom level)
// Both Grid and Map modes use this same viewport — only difference is
// whether Leaflet tiles are visible underneath.
// ----------------------------------------------------
const viewState = {
  // Provisional defaults; overwritten by initializeMapView() before first draw.
  centerLat: SF_DEFAULT.centerLat,
  centerLng: SF_DEFAULT.centerLng,
  zoomLevel: SF_DEFAULT.zoomLevel,

  minZoom: 0,
  maxZoom: 18,

  // Panning state (in-flight drag)
  isPanning: false,
  panStartX: 0,
  panStartY: 0,
  panStartLat: 0,
  panStartLng: 0,
};

// Apply a {centerLat, centerLng, zoomLevel} triple to viewState + bounds + Leaflet.
function applyView(view) {
  viewState.centerLat = view.centerLat;
  viewState.centerLng = view.centerLng;
  viewState.zoomLevel = view.zoomLevel ?? SF_DEFAULT.zoomLevel;
  constrainMapView();
  centerMapBoundsOn(viewState.centerLat, viewState.centerLng);
  if (mapState.active && mapState.leafletMap) {
    mapState.leafletMap.setView([viewState.centerLat, viewState.centerLng], viewState.zoomLevel);
  }
}

// "Home" button. Snap to the current anchor at default zoom.
// No geolocation prompt — that only happens during init when there's
// nothing to home to.
function resetView() {
  const b = mapState.bounds;
  applyView({
    centerLat: (b.south + b.north) / 2,
    centerLng: (b.west + b.east) / 2,
    zoomLevel: SF_DEFAULT.zoomLevel,
  });
  updateZoomSlider();
  drawEnvironment();
}

// Restore view from a saved env file's map_view block. Treated as a
// legitimate user-chosen view: persisted just like a pan/zoom.
function restoreMapView(mapViewData) {
  if (!mapViewData) return;
  mapViewData = migrateLegacyMapCoordinates(mapViewData);

  if (mapViewData.bounds) {
    mapState.bounds.south = mapViewData.bounds.south;
    mapState.bounds.north = mapViewData.bounds.north;
    mapState.bounds.west = mapViewData.bounds.west;
    mapState.bounds.east = mapViewData.bounds.east;
  }

  const center = mapViewData.center;
  if (center) {
    applyView({
      centerLat: center.lat,
      centerLng: center.lng,
      zoomLevel: mapViewData.zoom ?? viewState.zoomLevel,
    });
  } else if (mapViewData.zoom !== undefined) {
    viewState.zoomLevel = mapViewData.zoom;
  }

  updateZoomSlider();
  saveViewToLocalStorage();
  appendDebugLine(`📍 Restored map view: center=(${viewState.centerLat.toFixed(4)}, ${viewState.centerLng.toFixed(4)}), zoom=${viewState.zoomLevel}`);
}

// Pull current view FROM Leaflet (user dragged/zoomed the map).
function syncViewStateFromLeaflet() {
  if (!mapState.active || !mapState.leafletMap) return;
  const center = mapState.leafletMap.getCenter();
  viewState.centerLat = center.lat;
  viewState.centerLng = center.lng;
  viewState.zoomLevel = mapState.leafletMap.getZoom();
  constrainMapView();
  // Leaflet also constrains its own panning. Correct an unexpected external
  // setView before persisting it; tolerate Leaflet's pixel rounding to avoid
  // recursive move events at the world boundary.
  const actual = mercatorPoint(center.lat, center.lng, viewState.zoomLevel);
  const bounded = mercatorPoint(viewState.centerLat, viewState.centerLng, viewState.zoomLevel);
  if (Math.abs(actual[0] - bounded[0]) > 1 || Math.abs(actual[1] - bounded[1]) > 1 ||
      Math.abs(center.lng) > 180 || Math.abs(center.lat) > 85.0511287798066) {
    mapState.leafletMap.setView([viewState.centerLat, viewState.centerLng], viewState.zoomLevel, {animate: false});
  }
  saveViewToLocalStorage();
}

function saveViewToLocalStorage() {
  return viewPreferences.save(viewState);
}

// Read a saved view from localStorage, or null if absent/stale/malformed.
function readSavedView() {
  return viewPreferences.read();
}

// Ask the browser for the user's location. Returns a view triple or null.
async function readGeolocation() {
  if (!navigator.geolocation) return null;
  try {
    const position = await new Promise((resolve, reject) => {
      navigator.geolocation.getCurrentPosition(resolve, reject, {
        enableHighAccuracy: false,
        timeout: 10000,
        maximumAge: 300000,
      });
    });
    return {
      centerLat: position.coords.latitude,
      centerLng: position.coords.longitude,
      zoomLevel: SF_DEFAULT.zoomLevel,
    };
  } catch (geoErr) {
    const labels = { 1: 'PERMISSION_DENIED', 2: 'POSITION_UNAVAILABLE', 3: 'TIMEOUT' };
    const label = labels[geoErr?.code] || `error code=${geoErr?.code}`;
    appendDebugLine(`📍 [init-view] geolocation failed: ${label} — ${geoErr?.message || ''}`);
    return null;
  }
}

// Pick a starting view from one of three sources, in priority order.
// Called once at startup.
async function initializeMapView() {
  const saved = readSavedView();
  if (saved) {
    applyView(saved);
    appendDebugLine(`📍 [init-view] restored from localStorage: (${saved.centerLat.toFixed(4)}, ${saved.centerLng.toFixed(4)})`);
    return;
  }

  const geo = await readGeolocation();
  if (geo) {
    applyView(geo);
    appendDebugLine(`📍 [init-view] using browser location: (${geo.centerLat.toFixed(4)}, ${geo.centerLng.toFixed(4)})`);
    saveViewToLocalStorage();
    return;
  }

  applyView(SF_DEFAULT);
  appendDebugLine(`📍 [init-view] using SF default: (${SF_DEFAULT.centerLat}, ${SF_DEFAULT.centerLng})`);
  // Note: SF default is intentionally NOT persisted. Next reload will retry
  // geolocation (no prompt if previously denied — browser caches that decision).
}

// Update the zoom slider to reflect current viewState.zoomLevel
function updateZoomSlider() {
  const slider = $("zoom-slider");
  const label = $("zoom-level");
  if (slider) {
    slider.min = viewState.minZoom;
    slider.max = viewState.maxZoom;
    slider.value = viewState.zoomLevel;
  }
  if (label) {
    label.textContent = "z" + viewState.zoomLevel.toFixed(0);
  }
}

// Apply zoom from slider value
function applyZoomFromSlider(zoomValue) {
  const newZoom = Math.max(viewState.minZoom, Math.min(viewState.maxZoom, parseFloat(zoomValue)));
  if (newZoom === viewState.zoomLevel) return;

  viewState.zoomLevel = newZoom;
  constrainMapView();

  // Sync to Leaflet if in map mode
  syncLeafletToViewState();
  updateZoomSlider();
  drawEnvironment();
  saveViewToLocalStorage();
}

const MAP_TILES = {
  street: {
    url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    attr: "&copy; OpenStreetMap contributors",
  },
  satellite: {
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attr: "Tiles &copy; Esri",
  },
};

function initLeafletMap() {
  if (mapState.leafletMap) return;
  const canvas = $("env-canvas");
  if (!canvas || typeof L === "undefined") return;

  // Create map div — sibling before canvas
  let mapDiv = document.getElementById("leaflet-map");
  if (!mapDiv) {
    mapDiv = document.createElement("div");
    mapDiv.id = "leaflet-map";
    canvas.parentNode.insertBefore(mapDiv, canvas);
  }

  // Size and position: match the canvas exactly
  const rect = canvas.getBoundingClientRect();
  const cs = getComputedStyle(canvas);
  mapDiv.style.cssText = `
    position:absolute;
    top:${canvas.offsetTop}px;
    left:${canvas.offsetLeft}px;
    width:${rect.width}px;
    height:${rect.height}px;
    z-index:0;
    border-radius:${cs.borderRadius};
  `;

  // Make canvas parent relative so absolute positioning works
  canvas.parentNode.style.position = "relative";

  constrainMapView();
  // A single geographic world: no repeated tiles or draggable copies.
  const map = L.map(mapDiv, {
    center: [viewState.centerLat, viewState.centerLng],
    zoom: viewState.zoomLevel,
    zoomControl: false,
    scrollWheelZoom: true,
    dragging: true,
    keyboard: false,
    doubleClickZoom: false,
    touchZoom: true,
    maxBounds: [[-85.0511287798066, -180], [85.0511287798066, 180]],
    maxBoundsViscosity: 1,
    minZoom: viewState.minZoom,
    maxZoom: viewState.maxZoom,
  });

  // Sync viewState FROM Leaflet when map moves/zooms
  // This makes canvas items follow the map
  map.on("move", () => {
    syncViewStateFromLeaflet();
    drawEnvironment();
  });
  map.on("moveend", () => {
    syncViewStateFromLeaflet();
    drawEnvironment();
  });
  map.on("zoom", () => {
    syncViewStateFromLeaflet();
    updateZoomSlider();
    drawEnvironment();
  });

  const cfg = MAP_TILES[mapState.mapType];
  mapState.tileLayer = L.tileLayer(cfg.url, { attribution: cfg.attr, noWrap: true });
  mapState.tileLayer.addTo(map);
  mapState.leafletMap = map;

  mapState.resizeTimer = setTimeout(() => {
    if (mapState.leafletMap !== map) return;
    mapState.resizeTimer = null;
    map.invalidateSize();
  }, 200);
}

function destroyLeafletMap() {
  if (mapState.resizeTimer !== null) {
    clearTimeout(mapState.resizeTimer);
    mapState.resizeTimer = null;
  }
  if (mapState.tileLayer) { mapState.tileLayer.remove(); mapState.tileLayer = null; }
  if (mapState.leafletMap) { mapState.leafletMap.remove(); mapState.leafletMap = null; }
  const mapDiv = document.getElementById("leaflet-map");
  if (mapDiv) mapDiv.remove();
}

function toggle3dMode(active) {
  const tileSelect = $("map-tile-select");
  if (tileSelect) tileSelect.style.display = active || mapState.active ? "inline-block" : "none";
  const canvas = $("env-canvas");
  const cesiumDiv = $("cesium-container");
  const cesiumControls = $("cesium-controls");
  const btnGrid = $("btn-view-grid");
  const btnMap = $("btn-view-map");
  const btn3d = $("btn-view-3d");

  if (active) {
    if (canvas) canvas.style.display = "none";
    if (cesiumDiv) cesiumDiv.style.display = "block";
    if (cesiumControls) cesiumControls.style.display = "flex";
    if (btnGrid) btnGrid.classList.remove("active");
    if (btnMap) btnMap.classList.remove("active");
    if (btn3d) btn3d.classList.add("active");
    cesiumView.init("cesium-container").then((v) => {
      if (v) { cesiumView.setMapType(mapState.mapType); cesiumView.render(); }
    });
  } else {
    if (canvas) canvas.style.display = "";
    if (cesiumDiv) cesiumDiv.style.display = "none";
    if (cesiumControls) cesiumControls.style.display = "none";
    if (btn3d) btn3d.classList.remove("active");
    cesiumView.destroy();
  }
}

function updateCanvasPointerEvents() {
  // In map mode: let mouse events pass through to Leaflet UNLESS editing
  const canvas = $("env-canvas");
  if (!canvas) return;
  if (mapState.active && !state.addMode && !state.editMode) {
    canvas.style.pointerEvents = "none";
  } else {
    canvas.style.pointerEvents = "auto";
  }
}

function toggleMapMode(active) {
  mapState.active = active;
  const canvas = $("env-canvas");

  if (active) {
    initLeafletMap();
    if (canvas) {
      canvas.style.position = "relative";
      canvas.style.zIndex = "1";
      canvas.style.background = "transparent";
    }
    // The canvas is already visible and Leaflet is initialized synchronously.
    // Finish this switch before callers such as Fit mission set their new view.
    syncLeafletToViewState();
  } else {
    destroyLeafletMap();
    if (canvas) {
      canvas.style.position = "";
      canvas.style.zIndex = "";
      canvas.style.background = "";
      canvas.style.pointerEvents = "auto";
    }
  }
  updateCanvasPointerEvents();

  // Update buttons
  const btnGrid = $("btn-view-grid");
  const btnMap = $("btn-view-map");
  const tileSelect = $("map-tile-select");
  if (btnGrid) btnGrid.classList.toggle("active", !active);
  if (btnMap) btnMap.classList.toggle("active", active);
  if (tileSelect) tileSelect.style.display = active ? "inline-block" : "none";

  drawEnvironment();
}

function switchMapTiles(type) {
  mapState.mapType = type;
  cesiumView.setMapType(type);
  if (!mapState.leafletMap || !mapState.tileLayer) return;
  mapState.tileLayer.remove();
  const cfg = MAP_TILES[type];
  mapState.tileLayer = L.tileLayer(cfg.url, { attribution: cfg.attr, noWrap: true });
  mapState.tileLayer.addTo(mapState.leafletMap);
}

function attachMapControls() {
  const btnGrid = $("btn-view-grid");
  const btnMap = $("btn-view-map");
  const btn3d = $("btn-view-3d");
  const btnResetView = $("btn-reset-view");
  const tileSelect = $("map-tile-select");
  const zoomSlider = $("zoom-slider");

  // Leave 3D BEFORE opening the map: the map sizes itself to the 2D canvas,
  // which 3D hides (a map opened first came out 0x0 and stayed blank).
  if (btnGrid) btnGrid.addEventListener("click", () => { toggle3dMode(false); toggleMapMode(false); });
  if (btnMap) btnMap.addEventListener("click", () => { toggle3dMode(false); toggleMapMode(true); });
  if (btn3d) btn3d.addEventListener("click", () => { toggleMapMode(false); toggle3dMode(true); });
  if (btnResetView) btnResetView.addEventListener("click", () => resetView());

  const cesiumViewTop = $("cesium-view-top");
  const cesiumViewSide = $("cesium-view-side");
  const cesiumViewOblique = $("cesium-view-oblique");
  const cesiumViewReset = $("cesium-view-reset");
  const cesiumFollowDrone = $("cesium-follow-drone");
  if (cesiumViewTop) cesiumViewTop.addEventListener("click", () => cesiumView.setPreset(0, -89.9));
  if (cesiumViewSide) cesiumViewSide.addEventListener("click", () => cesiumView.setPreset(0, -5));
  if (cesiumViewOblique) cesiumViewOblique.addEventListener("click", () => cesiumView.setPreset(0, -35));
  if (cesiumViewReset) {
    cesiumViewReset.addEventListener("click", () => {
      if (cesiumFollowDrone) cesiumFollowDrone.value = "";
      cesiumView.followDrone(null);
      cesiumView.setPreset(0, -35);
    });
  }
  if (cesiumFollowDrone) {
    cesiumFollowDrone.addEventListener("change", () => cesiumView.followDrone(cesiumFollowDrone.value));
  }

  attachTankerAltitudeBox();
  if (tileSelect) tileSelect.addEventListener("change", function() { switchMapTiles(this.value); });

  // Zoom slider handler
  if (zoomSlider) {
    zoomSlider.addEventListener("input", function() {
      applyZoomFromSlider(this.value);
    });
  }
}

// ----------------------------------------------------
// MissionSegments - Unified segment manager (replaces segmentStore, MissionReplayLegacy, SegmentedImportManager)
// ----------------------------------------------------
/**
 * MissionSegments - Single source of truth for segmented missions
 *
 * Handles both CREATION workflow (building segments via Cut) and IMPORT workflow (loading pre-segmented JSON).
 *
 * Each segment contains:
 * - index: segment number (0, 1, 2, ...)
 * - env: { airports, targets, sams } for this segment
 * - drone_configs: { "1": { enabled, fuel_budget, ... }, ... }
 * - routes: { "1": { trajectory, route, fuel_remaining }, ... }
 * - frozenTargets: ["T1", "T2"] - targets visited BEFORE this segment
 * - cutPosition: { "1": [x,y], "2": [x,y] } - cut position (null for segment 0)
 */
class MissionSegments {
  constructor() {
    this.clear();
  }

  /** Clear all state */
  clear() {
    this._segments = [];
    this._currentIndex = 0;
    this._frozenTrajectories = {};  // { droneId: [[x,y], ...] } - accumulated frozen paths
    this._isImported = false;       // True if loaded from JSON (vs created via workflow)
  }

  /** Check if we have any segments */
  hasSegments() {
    return this._segments.length > 0;
  }

  /** Check if this is an imported mission */
  isImported() {
    return this._isImported;
  }

  /** Get current segment index */
  getCurrentIndex() {
    return this._currentIndex;
  }

  /** Get total segment count */
  getSegmentCount() {
    return this._segments.length;
  }

  /** Get segment by index */
  getSegment(index) {
    return this._segments[index] || null;
  }

  /** Get current segment */
  getCurrentSegment() {
    return this._segments[this._currentIndex] || null;
  }

  /** Get all segments */
  getAllSegments() {
    return this._segments;
  }

  // ========================================
  // CREATION WORKFLOW
  // ========================================

  /**
   * Add a new segment (called on Accept during creation workflow)
   * @param {object} segmentData - { env, drone_configs, routes, frozenTargets, cutPosition }
   * @returns {number} The new segment's index
   */
  addSegment(segmentData) {
    const segment = {
      index: this._segments.length,
      env: JSON.parse(JSON.stringify(segmentData.env || {})),
      drone_configs: JSON.parse(JSON.stringify(segmentData.drone_configs || {})),
      routes: this._copyRoutes(segmentData.routes || {}),
      frozenTargets: segmentData.frozenTargets ? [...segmentData.frozenTargets] : [],
      cutPosition: segmentData.cutPosition ? JSON.parse(JSON.stringify(segmentData.cutPosition)) : null
    };
    this._segments.push(segment);
    this._currentIndex = segment.index;
    return segment.index;
  }

  /**
   * Update the current segment's routes (called after optimization)
   */
  updateCurrentRoutes(routes) {
    const seg = this.getCurrentSegment();
    if (seg) {
      seg.routes = this._copyRoutes(routes);
    }
  }

  /**
   * Set frozen trajectory for a drone (accumulated from previous segments)
   * @param {string} droneId
   * @param {Array} points - [[x,y], [x,y], ...]
   */
  setFrozenTrajectory(droneId, points) {
    this._frozenTrajectories[droneId] = points ? [...points] : [];
  }

  /**
   * Get frozen trajectory for a drone
   * @param {string} droneId
   * @returns {Array} [[x,y], ...] or empty array
   */
  getFrozenTrajectory(droneId) {
    return this._frozenTrajectories[droneId] || [];
  }

  /**
   * Get all frozen trajectories
   * @returns {object} { droneId: [[x,y], ...], ... }
   */
  getAllFrozenTrajectories() {
    return this._frozenTrajectories;
  }

  /**
   * Clear frozen trajectories (called on Reset)
   */
  clearFrozenTrajectories() {
    this._frozenTrajectories = {};
  }

  // ========================================
  // IMPORT WORKFLOW
  // ========================================

  /**
   * Load segments from JSON (import workflow)
   * @param {object} data - { segments: [...], version: "2.0", ... }
   * @returns {boolean} Success
   */
  loadFromJson(data) {
    this.clear();

    const segments = data.segments || [];
    if (segments.length === 0) return false;

    segments.forEach((seg, idx) => {
      this._segments.push({
        index: idx,
        env: JSON.parse(JSON.stringify(seg.env || {})),
        drone_configs: JSON.parse(JSON.stringify(seg.drone_configs || {})),
        routes: this._copyRoutes(seg.routes || {}),
        frozenTargets: seg.frozenTargets ? [...seg.frozenTargets] : [],
        cutPosition: seg.cutPosition ? JSON.parse(JSON.stringify(seg.cutPosition)) : null
      });
    });

    this._currentIndex = 0;
    this._isImported = true;
    return true;
  }

  /**
   * Advance to next segment (called on Accept during import workflow)
   * @returns {boolean} True if advanced, false if at last segment
   */
  advanceToNextSegment() {
    if (this._currentIndex < this._segments.length - 1) {
      this._currentIndex++;
      return true;
    }
    return false;
  }

  /**
   * Check if at last segment
   */
  isLastSegment() {
    return this._currentIndex >= this._segments.length - 1;
  }

  /**
   * Reset to first segment (for animation replay)
   */
  resetToStart() {
    this._currentIndex = 0;
    this._frozenTrajectories = {};
  }

  // ========================================
  // EXPORT
  // ========================================

  /**
   * Export to JSON format
   * @returns {object} { version: "2.0", exportedAt: timestamp, segments: [...] }
   */
  toJSON() {
    return {
      version: "2.0",
      exportedAt: Date.now(),
      time_sync: readTimeSyncOptionsFromUI(),
      // Save map view so import restores the same geographic area
      map_view: {
        bounds: { ...mapState.bounds },
        center: { lat: viewState.centerLat, lng: viewState.centerLng },
        zoom: viewState.zoomLevel,
      },
      segments: this._segments.map(seg => ({
        index: seg.index,
        env: seg.env,
        drone_configs: seg.drone_configs,
        routes: seg.routes,
        frozenTargets: seg.frozenTargets,
        cutPosition: seg.cutPosition,
        lostDrones: seg.lostDrones || []
      }))
    };
  }

  // ========================================
  // HELPERS
  // ========================================

  /** Deep copy routes object */
  _copyRoutes(routes) {
    const copy = {};
    Object.entries(routes).forEach(([droneId, routeData]) => {
      copy[droneId] = {
        trajectory: routeData.trajectory ? routeData.trajectory.map(p => [...p]) : [],
        route: routeData.route ? [...routeData.route] : [],
        fuel_remaining: routeData.fuel_remaining || 0,
        // Preserve fields needed for stats display
        distance: routeData.distance || 0,
        fuel_budget: routeData.fuel_budget || 0,
        points: routeData.points || 0,
        // Preserve refuel metadata
        refuel_airport: routeData.refuel_airport || null,
        leg1_distance: routeData.leg1_distance || 0,
        leg2_distance: routeData.leg2_distance || 0,
        // Multi-stop refuel: preserve the N-leg metadata so the ⛽ stats split +
        // refuel-crossing survive segment-store / accept copies.
        leg_distances: routeData.leg_distances || null,
        refuel_indices: routeData.refuel_indices || null,
        refuel_stops: routeData.refuel_stops || null,
      };
    });
    return copy;
  }

  /** Debug: print segment table */
  printSegmentTable() {
    const lines = [];
    lines.push(`=== SEGMENTS (${this._segments.length} total, current=${this._currentIndex}) ===`);
    lines.push(`Idx | targets | routes | frozen | cutPos`);
    lines.push(`----+---------+--------+--------+--------`);
    this._segments.forEach(seg => {
      const targetsStr = String(seg.env?.targets?.length ?? 0).padStart(7);
      const routesStr = String(Object.keys(seg.routes || {}).filter(d => seg.routes[d]?.trajectory?.length > 0).length).padStart(6);
      const frozenStr = String(seg.frozenTargets?.length ?? 0).padStart(6);
      const cutStr = seg.cutPosition ? 'yes' : 'no';
      lines.push(`${String(seg.index).padStart(3)} | ${targetsStr} | ${routesStr} | ${frozenStr} | ${cutStr.padStart(6)}`);
    });
    lines.push(`===`);
    const table = lines.join('\n');
    console.log(table);
    return table;
  }
}

// Global mission segments instance
const mission = new MissionSegments();

// Legacy alias for backward compatibility during transition
const segmentStore = {
  segments: [],
  clear() { mission.clear(); this.segments = []; },
  addSegment(seg) {
    const idx = mission.addSegment(seg);
    this.segments = mission.getAllSegments();
    return idx;
  },
  getSegment(idx) { return mission.getSegment(idx); },
  getCurrentSegment() { return mission.getCurrentSegment(); },
  getAllSegments() { return mission.getAllSegments(); },
  count() { return mission.getSegmentCount(); },
  toJSON() { return mission.toJSON(); },
  updateLastSegment(updates) {
    const seg = mission.getCurrentSegment();
    if (seg) Object.assign(seg, updates);
  }
};

/**
 * Build a clean segment object for mission.addSegment()
 * Called from acceptSolution() to save segment data
 */
function buildCleanSegment(solutionToStore, droneConfigs, env, frozenTargets, cutPositions, lostDrones = [], checkpoint = null) {
  // Build routes object with trajectory, route, stats, and refuel metadata
  const routes = {};
  Object.entries(solutionToStore.routes || {}).forEach(([droneId, routeData]) => {
    routes[droneId] = {
      trajectory: routeData.trajectory ? [...routeData.trajectory] : [],
      route: routeData.route ? [...routeData.route] : [],
      fuel_remaining: routeData.fuel_remaining || 0,
      // Stats fields
      distance: routeData.distance || 0,
      fuel_budget: routeData.fuel_budget || 0,
      points: routeData.points || 0,
      // Refuel metadata
      refuel_airport: routeData.refuel_airport || null,
      leg1_distance: routeData.leg1_distance || 0,
      leg2_distance: routeData.leg2_distance || 0,
      // Multi-stop refuel: preserve N-leg metadata across segment-store/accept.
      leg_distances: routeData.leg_distances || null,
      refuel_indices: routeData.refuel_indices || null,
      refuel_stops: routeData.refuel_stops || null,
    };
  });

  // Progressive reveal: a segment's env carries only the targets STILL to visit at
  // that cut — i.e. the full set MINUS the targets already frozen (visited before
  // this segment). Storing the full target set in every segment made re-import show
  // ALL targets from segment 0 instead of revealing them cut by cut, because
  // getTargetsToDisplay() builds the reveal from each segment's own env.targets.
  const _frozenIds = new Set((frozenTargets || []).map(String));
  const _segmentTargets = (env.targets || []).filter(
    (t) => !_frozenIds.has(String(t.id))
  );

  return {
    env: {
      airports: JSON.parse(JSON.stringify(env.airports || [])),
      targets: JSON.parse(JSON.stringify(_segmentTargets)),
      sams: JSON.parse(JSON.stringify(env.sams || [])),
      checkpoints: JSON.parse(JSON.stringify(env.checkpoints || []))
    },
    drone_configs: JSON.parse(JSON.stringify(droneConfigs)),
    routes: routes,
    frozenTargets: frozenTargets ? [...frozenTargets] : [],
    cutPosition: cutPositions ? JSON.parse(JSON.stringify(cutPositions)) : null,
    lostDrones: lostDrones ? [...lostDrones] : [],
    checkpoint: checkpoint ? JSON.parse(JSON.stringify(checkpoint)) : null  // The checkpoint ID/position for this segment
  };
}

// ----------------------------------------------------
// Mission Mode State Machine
// ----------------------------------------------------
/**
 * MissionMode - Single source of truth for UI state
 *
 * IDLE:                 No committed plan exists yet. Can edit env or solve.
 * EDITING_ENV:          User is editing draftEnv. Must Accept Edits to proceed.
 * DRAFT_READY:          A draft solution exists. Must Accept Solution or Discard.
 * READY_TO_ANIMATE:     Committed plan exists. Can start/resume animation.
 * ANIMATING:            Drones are moving. Can Pause or Cut (C).
 * PAUSED_MID_ANIMATION: Paused but resumable (no truncation). Can resume or edit.
 * CHECKPOINT:           Cut occurred. Past locked, future removed. Must replan.
 */
// Debug logging: OFF by default. Flip via the console (`window.ISR_DEBUG = true`)
// to restore verbose tracing. Gates the per-frame cut-marker logs that used to
// flood the console (hundreds/sec) during the cut/accept redraw pipeline and made
// the UI feel frozen. dbg() is a no-op unless the flag is set.
window.ISR_DEBUG = window.ISR_DEBUG || false;
function dbg(...args) { if (window.ISR_DEBUG) console.log(...args); }

const MissionMode = {
  IDLE: "IDLE",
  EDITING_ENV: "EDITING_ENV",
  DRAFT_READY: "DRAFT_READY",
  READY_TO_ANIMATE: "READY_TO_ANIMATE",
  ANIMATING: "ANIMATING",
  PAUSED_MID_ANIMATION: "PAUSED_MID_ANIMATION",
  CHECKPOINT: "CHECKPOINT",
};

// Mission state machine state
const missionState = {
  mode: MissionMode.IDLE,
  // Bumped on every mode change and every new draft. A slow answer (the agent
  // takes 10-60 s) is applied only if nothing changed since it was asked for.
  revision: 0,

  // Environment state (two-phase commit)
  draftEnv: null,              // Editable environment shown in editor
  acceptedEnv: null,           // Committed environment used for solving

  // Solution state (two-phase commit)
  draftSolution: null,         // Last solver output (not yet accepted)
  committedSegments: [],       // Array of accepted solutions (mission history)
  currentSegmentIndex: -1,     // Index of current segment being animated (-1 = none)

  // Runtime state for animation resume
  pauseContext: null,          // { droneStates: {...} } - saved state for resume

  // Checkpoint source tracking - distinguishes replay cuts from manual replan
  // "replay_cut" = checkpoint entered from animation cut (solve disabled)
  // null = checkpoint entered from edits/manual (solve enabled)
  checkpointSource: null,

  // Segmented mission workflow - for solving N segments sequentially
  segmentedMission: null,      // Full segmented mission definition from import
  currentBuildSegmentIndex: 0, // Which segment we're currently solving (0-indexed)

  // Live edit state - for editing during animation
  wasAnimating: false,         // True if we entered edit mode during animation
  stagedEnv: null,             // Staged environment copy (edits go here during live edit)
  stagedDroneConfigs: null,    // Staged drone configs copy
};

// ----------------------------------------------------
// Legacy Segment class - now delegates to mission system
// ----------------------------------------------------

/**
 * Segment - Legacy wrapper for backward compatibility
 * Creates segment-like objects that work with existing code
 */
function createSegmentObject(data) {
  const seg = {
    index: data.index || 0,
    solution: data.solution ? JSON.parse(JSON.stringify(data.solution)) : { routes: {}, sequences: {} },
    env: data.env ? JSON.parse(JSON.stringify(data.env)) : {},
    cutDistance: data.cutDistance ?? null,
    isCheckpointReplan: data.isCheckpointReplan || false,
    timestamp: data.timestamp || new Date().toISOString(),
    cutPosition: data.cutPosition ? [...data.cutPosition] : null,
    cutPositions: data.cutPositions ? JSON.parse(JSON.stringify(data.cutPositions)) : null,
    lostDrones: data.lostDrones ? [...data.lostDrones] : null,
    visitedTargets: data.visitedTargets ? [...data.visitedTargets] : null,
    visited_targets: data.visitedTargets ? [...data.visitedTargets] : null,  // Alias
    drone_configs: data.drone_configs ? JSON.parse(JSON.stringify(data.drone_configs)) : null,

    getCutDistance() {
      return this.cutDistance;
    },
    hasCheckpointBoundary() {
      return this.cutDistance !== null && this.cutDistance > 0;
    }
  };
  return seg;
}

/**
 * MissionReplay - Manages multi-segment mission playback
 *
 * Responsibilities:
 * - Store mission segments in sequence
 * - Track current playback position (segment index + distance)
 * - Detect segment boundary crossings
 * - Provide segment data for animation and display
 *
 * Does NOT:
 * - Control animation timing (that's the animation loop's job)
 * - Modify UI directly (emits events/callbacks instead)
 */
class MissionReplayLegacy {
  constructor() {
    this._segments = [];
    this._currentSegmentIndex = 0;
    this._onSegmentSwitch = null;  // Callback: (fromIndex, toIndex, newSegment) => void
    this._frozenTrajectories = {}; // §8.8: { droneId: [[lat,lng],...] } already-flown (past) paths, display-only
  }

  // --- Frozen (past) trajectory store — display-only history (§5/§8.8) ---
  // Frozen history ACCUMULATES across cuts: Start→C1 AND C1→C2 AND … all stay
  // drawn. Stored per (drone, cut/segment index) so re-displaying the SAME cut
  // overwrites just that piece (idempotent) while earlier cuts persist. Lives on
  // MissionReplayLegacy because `missionReplay` is that class (a copy on
  // MissionSegments was unreachable → history silently never showed).
  // Shape: _frozenTrajectories[droneId] = { segIdx: [[lat,lng],...], ... }
  setFrozenTrajectory(droneId, points, segIdx) {
    const k = String(droneId);
    if (!this._frozenTrajectories[k] || Array.isArray(this._frozenTrajectories[k])) {
      this._frozenTrajectories[k] = {};  // migrate any old flat-array value
    }
    this._frozenTrajectories[k][String(segIdx ?? 0)] = points ? [...points] : [];
  }
  // Concatenated past path for a drone (all cut pieces, in segment order).
  getFrozenTrajectory(droneId) {
    const bySeg = this._frozenTrajectories[String(droneId)];
    if (!bySeg) return [];
    if (Array.isArray(bySeg)) return bySeg;  // legacy flat
    const out = [];
    Object.keys(bySeg).map(Number).sort((a, b) => a - b).forEach((s) => {
      const pts = bySeg[String(s)] || [];
      pts.forEach((p) => out.push(p));
    });
    return out;
  }
  // For drawing: { droneId: concatenatedPts[] }
  getAllFrozenTrajectories() {
    const result = {};
    Object.keys(this._frozenTrajectories).forEach((did) => {
      result[did] = this.getFrozenTrajectory(did);
    });
    return result;
  }
  clearFrozenTrajectories() {
    this._frozenTrajectories = {};
  }

  // --- Segment Management ---

  /**
   * Add a new segment to the mission
   */
  addSegment(segmentData) {
    const segment = createSegmentObject({
      index: this._segments.length,
      ...segmentData
    });
    this._segments.push(segment);
    return segment;
  }

  /**
   * Get all segments
   */
  getSegments() {
    return [...this._segments];
  }

  /**
   * Get segment by index
   */
  getSegment(index) {
    return this._segments[index] ?? null;
  }

  /**
   * Get current segment
   */
  getCurrentSegment() {
    return this._segments[this._currentSegmentIndex] ?? null;
  }

  /**
   * Get next segment (if any)
   */
  getNextSegment() {
    return this._segments[this._currentSegmentIndex + 1] ?? null;
  }

  /**
   * Get segment count
   */
  getSegmentCount() {
    return this._segments.length;
  }

  /**
   * Get current segment index
   */
  getCurrentSegmentIndex() {
    return this._currentSegmentIndex;
  }

  /**
   * Check if there are more segments after current
   */
  hasNextSegment() {
    return this._currentSegmentIndex < this._segments.length - 1;
  }

  // --- Playback Control ---

  /**
   * Set current segment index (for advancing after accept)
   */
  setCurrentSegmentIndex(index) {
    if (index >= 0 && index < this._segments.length) {
      this._currentSegmentIndex = index;
    }
  }

  /**
   * Reset to beginning for replay
   */
  resetToStart() {
    this._currentSegmentIndex = 0;
    this._frozenTrajectories = {};  // §8.8: Reset (t=0) clears flown-history paths
  }

  /**
   * Clear all solutions from all segments (for Reset in segmentInfo workflow)
   * Keeps env, cutDistance, cutPositions intact
   */
  clearAllSolutions() {
    for (const seg of this._segments) {
      seg.solution = { routes: {}, sequences: {} };
    }
  }

  /**
   * Clear all segments (full reset)
   */
  clear() {
    this._segments = [];
    this._currentSegmentIndex = 0;
  }

  /**
   * Keep only first N segments
   */
  truncateAfter(index) {
    if (index < this._segments.length - 1) {
      this._segments = this._segments.slice(0, index + 1);
      if (this._currentSegmentIndex > index) {
        this._currentSegmentIndex = index;
      }
    }
  }

  /**
   * Replace a segment at a given index with new data
   * Creates a new segment object with the provided data
   */
  replaceSegment(index, segmentData) {
    if (index < 0 || index >= this._segments.length) {
      return false;
    }
    const newSegment = createSegmentObject({
      index: index,
      ...segmentData
    });
    this._segments[index] = newSegment;
    return true;
  }

  /**
   * Update the solution in the current segment (e.g., after optimization)
   */
  updateCurrentSegmentSolution(routes, sequences) {
    const segment = this.getCurrentSegment();
    if (!segment) {
      return false;
    }
    // Update the solution's routes and sequences
    if (segment.solution) {
      segment.solution.routes = JSON.parse(JSON.stringify(routes));
      if (sequences) {
        segment.solution.sequences = JSON.parse(JSON.stringify(sequences));
      }
      return true;
    }
    return false;
  }

  /**
   * Set callback for segment switches
   */
  onSegmentSwitch(callback) {
    this._onSegmentSwitch = callback;
  }

  /**
   * Check if mission has crossed into the next segment
   * Spatial checkpoints are reached on each drone's own playback timeline.
   * The scalar distance is retained for legacy missions without cut positions.
   *
   * @param missionDistance - Legacy cumulative distance/fuel counter
   * @param checkpointReached - Whether all drones have reached their saved cut positions
   * @returns {switched: boolean, newSegment: Segment|null}
   */
  checkSegmentBoundary(missionDistance, checkpointReached = null) {
    const nextSegment = this.getNextSegment();
    if (!nextSegment || !nextSegment.hasCheckpointBoundary()) {
      return { switched: false, newSegment: null };
    }

    const cutDistance = nextSegment.getCutDistance();
    const reached = checkpointReached === null
      ? cutDistance !== null && missionDistance >= cutDistance : checkpointReached;
    if (reached) {
      // Crossed! Switch to next segment
      const fromIndex = this._currentSegmentIndex;
      this._currentSegmentIndex++;
      if (this._onSegmentSwitch) {
        this._onSegmentSwitch(fromIndex, this._currentSegmentIndex, nextSegment);
      }

      return { switched: true, newSegment: nextSegment };
    }

    return { switched: false, newSegment: null };
  }

  /**
   * Get the starting distance for the current segment
   * (0 for segment 0, cutDistance for checkpoint replans)
   */
  getStartingDistance() {
    const segment = this.getCurrentSegment();
    if (!segment || segment.index === 0) {
      return 0;
    }
    return segment.getCutDistance() ?? 0;
  }

  /**
   * Export replay state for debugging
   */
  getDebugInfo() {
    return {
      segmentCount: this._segments.length,
      currentIndex: this._currentSegmentIndex,
      segments: this._segments.map(s => ({
        index: s.index,
        isCheckpointReplan: s.isCheckpointReplan,
        hasCheckpoint: s.hasCheckpointBoundary(),
        cutDistance: s.getCutDistance(),
        envTargets: s.env.targets?.length ?? 0,
        routeCount: Object.keys(s.solution.routes || {}).length,
        lostDrones: s.lostDrones || [],
        visitedTargets: s.visited_targets || []
      }))
    };
  }

  /**
   * Print segment table to console and debug panel for debugging
   */
  printSegmentTable() {
    const lines = [];
    lines.push(`=== SEGMENT TABLE (${this._segments.length} segments, current=${this._currentSegmentIndex}) ===`);
    lines.push(`Idx | cutDist | targets | routes | lostDrones | visited`);
    lines.push(`----+---------+---------+--------+------------+---------`);
    this._segments.forEach(s => {
      const cutDist = s.getCutDistance();
      const cutStr = cutDist !== null ? cutDist.toFixed(1).padStart(7) : '  null ';
      const targetsStr = String(s.env?.targets?.length ?? 0).padStart(7);
      const routesStr = String(Object.keys(s.solution?.routes || {}).length).padStart(6);
      const lostStr = (s.lostDrones || []).join(',') || '-';
      const visitedStr = (s.visited_targets || []).join(',') || '-';
      lines.push(`${String(s.index).padStart(3)} | ${cutStr} | ${targetsStr} | ${routesStr} | ${lostStr.padEnd(10)} | ${visitedStr}`);
    });
    lines.push(`===`);

    const table = lines.join('\n');
    console.log(table);
    return table;
  }
}

// Global mission replay instance (legacy system)
const missionReplay = new MissionReplayLegacy();


/**
 * SegmentedImportManager - Manages state for imported segmented missions
 *
 * This class is the SINGLE SOURCE OF TRUTH for:
 * - All targets in the mission (never changes after load)
 * - Which targets have been visited/frozen (updated on Accept)
 * - Current segment index
 * - Cut positions for each segment
 *
 * The key insight: state.env.targets should contain ALL targets for DRAWING.
 * Filtering for the solver happens in buildCheckpointEnv().
 * The drawing code uses state.visitedTargets to show green X on frozen targets.
 */
class SegmentedImportManager {
  constructor() {
    this.clear();
  }

  /** Clear all segmented import state */
  clear() {
    this._allTargets = [];           // ALL targets - never modified after load
    this._segmentTargets = [];       // Targets per segment: [[seg0 targets], [seg1 targets], ...]
    this._baseEnv = null;            // Base environment (airports, sams, drone_configs)
    this._segmentCuts = [];          // Array of {cutDistance, visitedTargets, dronePositions}
    this._currentSegmentIndex = 0;   // Which segment we're solving
    this._isActive = false;          // Is this a segmented import workflow?
    this._segmentSolutions = [];     // Solutions per segment (if present in JSON)
  }

  /** Check if we're in segmented import mode */
  isActive() {
    return this._isActive;
  }

  /** Get current segment index */
  getCurrentSegmentIndex() {
    return this._currentSegmentIndex;
  }

  /** Get all targets (for drawing) */
  getAllTargets() {
    return JSON.parse(JSON.stringify(this._allTargets));
  }

  /** Get visited targets for current segment (from cut data) */
  getVisitedTargetsForSegment(segmentIndex) {
    // Segment 0: no targets visited yet
    if (segmentIndex === 0) {
      return [];
    }
    // Segment N: use visitedTargets from cut[N-1]
    const cutIdx = segmentIndex - 1;
    if (cutIdx >= 0 && cutIdx < this._segmentCuts.length) {
      return [...(this._segmentCuts[cutIdx].visitedTargets || [])];
    }
    return [];
  }

  /** Get cut position for segment (where drone starts) */
  getCutPositionForSegment(segmentIndex) {
    // Segment 0: starts at airport, no cut position
    if (segmentIndex === 0) {
      return null;
    }
    // Segment N's cutPosition = where segment N starts (C{N} marker)
    // This is stored directly in _segmentCutPositions[N] as {"1": [x,y], "2": [x,y]}
    if (this._segmentCutPositions && segmentIndex < this._segmentCutPositions.length) {
      const rawCutPos = this._segmentCutPositions[segmentIndex];
      if (rawCutPos) {
        console.log(`[getCutPositionForSegment] Seg ${segmentIndex} returning:`, rawCutPos);
        return JSON.parse(JSON.stringify(rawCutPos));
      }
    }
    // Fallback to old _segmentCuts method
    const cutIdx = segmentIndex - 1;
    if (cutIdx >= 0 && cutIdx < this._segmentCuts.length) {
      const cut = this._segmentCuts[cutIdx];
      if (cut.dronePositions) {
        const positions = {};
        Object.entries(cut.dronePositions).forEach(([did, posData]) => {
          if (posData.position && posData.position.length >= 2) {
            positions[did] = [...posData.position];
          }
        });
        if (Object.keys(positions).length > 0) {
          console.log(`[getCutPositionForSegment] Seg ${segmentIndex} fallback returning:`, positions);
          return positions;
        }
      }
    }
    console.log(`[getCutPositionForSegment] Seg ${segmentIndex} no data found`);
    return null;
  }

  /** Get cut distance for segment */
  getCutDistanceForSegment(segmentIndex) {
    if (segmentIndex === 0) return 0;
    const cutIdx = segmentIndex - 1;
    if (cutIdx >= 0 && cutIdx < this._segmentCuts.length) {
      // Return actual cutDistance (may be null for last segment)
      return this._segmentCuts[cutIdx].cutDistance;
    }
    return null;  // No cut data available
  }

  /** Get drone_configs for a specific segment (from the imported JSON) */
  getDroneConfigsForSegment(segmentIndex) {
    if (!this._segmentDroneConfigs || segmentIndex < 0 || segmentIndex >= this._segmentDroneConfigs.length) {
      return null;
    }
    return JSON.parse(JSON.stringify(this._segmentDroneConfigs[segmentIndex]));
  }

  /** Get solution for a specific segment (from the imported JSON, if present) */
  getSolutionForSegment(segmentIndex) {
    if (!this._segmentSolutions || segmentIndex < 0 || segmentIndex >= this._segmentSolutions.length) {
      return null;
    }
    return this._segmentSolutions[segmentIndex] ? JSON.parse(JSON.stringify(this._segmentSolutions[segmentIndex])) : null;
  }

  /** Check if imported JSON has solutions (for replay vs re-solve) */
  hasSolutions() {
    return this._segmentSolutions && this._segmentSolutions.some(s => s && Object.keys(s.routes || {}).length > 0);
  }

  /** Get total number of segments */
  getTotalSegments() {
    return this._segmentCuts.length + 1;
  }

  /** Advance to next segment */
  advanceToNextSegment() {
    if (this._currentSegmentIndex < this._segmentCuts.length) {
      this._currentSegmentIndex++;
      return true;
    }
    return false;
  }

  /** Check if all segments are complete */
  isComplete() {
    return this._currentSegmentIndex >= this._segmentCuts.length;
  }

  /** Get unfrozen targets for solver (current segment's targets minus visited) */
  getUnfrozenTargets() {
    const visited = this.getVisitedTargetsForSegment(this._currentSegmentIndex);
    const segmentTargets = this.getTargetsToDisplay();  // Current segment's targets
    return segmentTargets.filter(t => !visited.includes(t.id));
  }

  /** Get targets to DISPLAY for current segment */
  getTargetsToDisplay() {
    // Return CUMULATIVE targets from segment 0 through current segment
    // This ensures progressive reveal: each Accept shows all previous + new targets
    const seenTargets = new Map();

    for (let i = 0; i <= this._currentSegmentIndex && i < this._segmentTargets.length; i++) {
      const segTargets = this._segmentTargets[i] || [];
      segTargets.forEach(t => {
        if (!seenTargets.has(t.id)) {
          seenTargets.set(t.id, t);
        }
      });
    }

    return Array.from(seenTargets.values()).map(t => JSON.parse(JSON.stringify(t)));
  }

  /** Get env for DISPLAY (only targets up to current segment) */
  getEnvForDisplay() {
    if (!this._baseEnv) return null;
    const env = JSON.parse(JSON.stringify(this._baseEnv));
    env.targets = this.getTargetsToDisplay();
    return env;
  }

  /** Get base env with all targets (for drawing) */
  getFullEnv() {
    if (!this._baseEnv) return null;
    const env = JSON.parse(JSON.stringify(this._baseEnv));
    env.targets = this.getAllTargets();
    return env;
  }

  /** Get env for solver (filtered targets, synthetic starts for segment > 0) */
  getEnvForSolver() {
    if (!this._baseEnv) return null;
    const env = JSON.parse(JSON.stringify(this._baseEnv));
    env.targets = this.getUnfrozenTargets();

    // Merge state.droneConfigs to preserve UI-configured values (end_airport, fuel_budget, enabled)
    // This is critical because _baseEnv.drone_configs comes from the imported JSON,
    // but the user may have changed these values in the UI
    const droneConfigs = JSON.parse(JSON.stringify(env.drone_configs || {}));
    Object.entries(state.droneConfigs || {}).forEach(([did, cfg]) => {
      if (droneConfigs[did]) {
        // Preserve end_airport from UI config
        if (cfg.end_airport) droneConfigs[did].end_airport = cfg.end_airport;
        // Preserve fuel_budget from UI config
        if (cfg.fuel_budget !== undefined) droneConfigs[did].fuel_budget = cfg.fuel_budget;
        // Preserve enabled from UI config
        if (cfg.enabled !== undefined) droneConfigs[did].enabled = cfg.enabled;
        // Preserve target_access from UI config
        if (cfg.target_access) droneConfigs[did].target_access = cfg.target_access;
        // Preserve speed from UI config
        if (cfg.speed !== undefined) droneConfigs[did].speed = cfg.speed;
      }
    });
    env.drone_configs = droneConfigs;

    // For segment > 0, add synthetic start at cut position
    if (this._currentSegmentIndex > 0) {
      const cutPositions = this.getCutPositionForSegment(this._currentSegmentIndex);
      if (cutPositions) {
        env.synthetic_starts = env.synthetic_starts || {};

        Object.entries(cutPositions).forEach(([did, pos]) => {
          const nodeId = `D${did}_START`;
          env.synthetic_starts[nodeId] = syntheticStartNode(nodeId, pos);
          if (env.drone_configs[did]) {
            env.drone_configs[did].start_airport = nodeId;
          }
        });
      }
    }
    return env;
  }

  /**
   * Load from segmentInfo format JSON
   * @param {Object} data - JSON with type:"segmented", targets, segmentInfo.segmentCuts
   */
  loadFromSegmentInfo(data) {
    this.clear();

    // Store ALL targets (never modify this)
    this._allTargets = JSON.parse(JSON.stringify(data.targets || []));

    // Store base environment (without targets - we'll add them dynamically)
    this._baseEnv = {
      airports: JSON.parse(JSON.stringify(data.airports || [])),
      sams: JSON.parse(JSON.stringify(data.sams || [])),
      drone_configs: JSON.parse(JSON.stringify(data.drone_configs || {})),
    };

    // Store segment cuts
    const segmentCuts = data.segmentInfo?.segmentCuts || [];
    this._segmentCuts = segmentCuts.map(cut => {
      const dronePositions = cut.dronePositions || {};
      const firstDronePos = Object.values(dronePositions)[0];
      return {
        cutDistance: firstDronePos?.distanceTraveled || firstDronePos?.totalDistance || 0,
        visitedTargets: [...(cut.visitedTargets || [])],
        dronePositions: JSON.parse(JSON.stringify(dronePositions)),
      };
    });

    this._currentSegmentIndex = 0;
    this._isActive = true;

    return true;
  }

  /**
   * Load from segments array format (legacy format with segments[])
   * This format has segments[0].env, segments[1].env, etc.
   * We need to collect all targets and build cut data from the segments.
   */
  loadFromSegmentsArray(data) {
    this.clear();

    const segments = data.segments || [];
    if (segments.length === 0) return false;

    // Collect ALL targets from all segments
    const seenTargetIds = new Set();
    const allTargets = [];

    // First check if top-level targets exist
    if (data.targets && data.targets.length > 0) {
      data.targets.forEach(t => {
        if (!seenTargetIds.has(t.id)) {
          seenTargetIds.add(t.id);
          allTargets.push(JSON.parse(JSON.stringify(t)));
        }
      });
    }

    // Collect targets from each segment AND store per-segment targets
    this._segmentTargets = [];
    segments.forEach((seg) => {
      const segTargets = [];
      (seg.env?.targets || []).forEach(t => {
        segTargets.push(JSON.parse(JSON.stringify(t)));
        if (!seenTargetIds.has(t.id)) {
          seenTargetIds.add(t.id);
          allTargets.push(JSON.parse(JSON.stringify(t)));
        }
      });
      this._segmentTargets.push(segTargets);
    });

    this._allTargets = allTargets;

    // Build base env - collect airports and SAMs from ALL segments (not just segment 0)
    const seg0Env = segments[0]?.env || {};
    // IMPORTANT: Check for top-level drone_configs first (new export format),
    // then segment 0's drone_configs, then seg0Env.drone_configs
    const droneConfigs = data.drone_configs ||
                         segments[0]?.drone_configs ||
                         seg0Env.drone_configs ||
                         {};

    // Collect ALL airports from all segments (merge by id)
    // Also check top-level airports (data.airports or data.env.airports) for export formats
    // that put airports at the top level rather than per-segment
    const allAirportsMap = new Map();

    // First, check top-level airports
    const topLevelAirports = data.airports || data.env?.airports || [];
    topLevelAirports.forEach(a => {
      if (a.id && !allAirportsMap.has(a.id)) {
        allAirportsMap.set(a.id, JSON.parse(JSON.stringify(a)));
      }
    });

    // Then collect from each segment (which may add additional airports)
    segments.forEach(seg => {
      (seg.env?.airports || []).forEach(a => {
        if (a.id && !allAirportsMap.has(a.id)) {
          allAirportsMap.set(a.id, JSON.parse(JSON.stringify(a)));
        }
      });
    });
    const allAirports = Array.from(allAirportsMap.values());
    console.log(`[loadFromSegmentsArray] Collected ${allAirports.length} airports: ${allAirports.map(a => a.id).join(', ')}`);

    // Collect ALL SAMs from all segments (merge by position)
    const allSamsMap = new Map();
    segments.forEach(seg => {
      (seg.env?.sams || []).forEach(s => {
        const key = s.id || `${s.center?.lat}_${s.center?.lng}`;
        if (!allSamsMap.has(key)) {
          allSamsMap.set(key, JSON.parse(JSON.stringify(s)));
        }
      });
    });
    const allSams = Array.from(allSamsMap.values());

    this._baseEnv = {
      airports: allAirports,
      sams: allSams,
      drone_configs: JSON.parse(JSON.stringify(droneConfigs)),
    };
    // Store per-segment drone_configs for retrieval during Accept
    this._segmentDroneConfigs = [];
    this._segmentSolutions = [];
    this._segmentCutPositions = [];  // Store raw cutPosition from each segment
    segments.forEach((seg, idx) => {
      const segConfigs = seg.drone_configs || seg.env?.drone_configs || {};
      this._segmentDroneConfigs.push(JSON.parse(JSON.stringify(segConfigs)));
      // Store solution if present (for replay without re-solve)
      // Check both seg.solution (legacy format) and seg.routes (V2 export format)
      let solution = seg.solution || null;
      if (!solution && seg.routes) {
        // Convert V2 export format (seg.routes) to expected solution format
        solution = { routes: seg.routes };
      }
      this._segmentSolutions.push(solution ? JSON.parse(JSON.stringify(solution)) : null);
      // Store raw cutPosition directly from segment (seg.cutPosition has format {"1": [x,y], "2": [x,y]})
      const rawCutPos = seg.cutPosition || seg.cutPositions || null;
      this._segmentCutPositions.push(rawCutPos ? JSON.parse(JSON.stringify(rawCutPos)) : null);
      if (rawCutPos) {
        console.log(`[loadFromSegmentsArray] Seg ${idx} cutPosition stored:`, rawCutPos);
      }
    });

    // Check if segment 0 has segmentInfo with segmentCuts (hybrid format)
    const seg0SegmentInfo = segments[0]?.env?.segmentInfo;
    if (seg0SegmentInfo && Array.isArray(seg0SegmentInfo.segmentCuts) && seg0SegmentInfo.segmentCuts.length > 0) {
      // Use the segmentInfo.segmentCuts data directly (preferred source)
      this._segmentCuts = seg0SegmentInfo.segmentCuts.map(cut => {
        const dronePositions = cut.dronePositions || {};
        const firstDronePos = Object.values(dronePositions)[0];
        return {
          cutDistance: firstDronePos?.distanceTraveled || firstDronePos?.totalDistance || 0,
          visitedTargets: [...(cut.visitedTargets || [])],
          dronePositions: JSON.parse(JSON.stringify(dronePositions)),
        };
      });
    } else {
      // Build segment cuts from segments[1+]
      // Each segment after 0 represents a cut point
      this._segmentCuts = [];

      // Helper function to calculate trajectory length in km using haversine
      const calculateTrajectoryLength = (trajectory) => {
        if (!trajectory || trajectory.length < 2) return 0;
        let totalDist = 0;
        for (let j = 1; j < trajectory.length; j++) {
          const lat1 = trajectory[j - 1][0], lng1 = trajectory[j - 1][1];
          const lat2 = trajectory[j][0], lng2 = trajectory[j][1];
          totalDist += haversineKm(lat1, lng1, lat2, lng2);
        }
        return totalDist;
      };

      // Helper function to calculate distance along trajectory to a cut position (in km)
      const calculateDistanceToCutPosition = (trajectory, cutPos) => {
        if (!trajectory || trajectory.length < 2 || !cutPos) return null;

        const cutLat = cutPos[0], cutLng = cutPos[1];

        // Find the segment where cutPos lies and calculate distance to that point
        let cumulativeDist = 0;
        for (let j = 0; j < trajectory.length - 1; j++) {
          const p1 = trajectory[j];
          const p2 = trajectory[j + 1];
          const lat1 = p1[0], lng1 = p1[1];
          const lat2 = p2[0], lng2 = p2[1];

          // Calculate segment length in km
          const segLenKm = haversineKm(lat1, lng1, lat2, lng2);

          // Invert the same geographic curve that placed the cut marker.
          if (segLenKm > 0.001) {  // Avoid division by tiny numbers
            const nearest = closestFlightPosition(p1, p2, [cutLat, cutLng]);
            if (nearest.distanceKm < 0.5) {
              return cumulativeDist + nearest.fraction * segLenKm;
            }
          }

          cumulativeDist += segLenKm;
        }

        // If cutPos wasn't found on any segment, return full trajectory length
        return cumulativeDist;
      };

      // Calculate cumulative distances for each segment
      // cutDistance for segment N is the distance traveled to reach the cut position for segment N
      let cumulativeDistance = 0;

      for (let i = 1; i < segments.length; i++) {
        const seg = segments[i];
        // Use explicit cutDistance if provided, otherwise calculate from cutPosition
        let cutDistance = seg.cutDistance ?? null;

        if (cutDistance === null) {
          // Get cut position for this segment (where drones were when cut happened)
          const cutPosData = seg.cutPosition || seg.cutPositions;
          const prevSeg = segments[i - 1];
          const prevRoutes = prevSeg?.routes || prevSeg?.solution?.routes || {};

          console.log(`[loadFromSegmentsArray] Seg ${i}: Calculating cutDistance from cutPosition`);

          // Calculate distance to cut position for each drone, use the max
          let maxDistToCut = 0;
          Object.entries(prevRoutes).forEach(([did, routeData]) => {
            const traj = routeData?.trajectory || [];
            const droneCutPos = cutPosData?.[did];

            if (droneCutPos && Array.isArray(droneCutPos) && traj.length > 1) {
              const distToCut = calculateDistanceToCutPosition(traj, droneCutPos);
              console.log(`[loadFromSegmentsArray] Seg ${i}: D${did} distToCut=${distToCut?.toFixed(1) || 'null'} to pos [${droneCutPos[0].toFixed(1)},${droneCutPos[1].toFixed(1)}]`);
              if (distToCut !== null && distToCut > maxDistToCut) {
                maxDistToCut = distToCut;
              }
            } else {
              // Fallback: use full trajectory length if no cut position
              const trajLen = calculateTrajectoryLength(traj);
              console.log(`[loadFromSegmentsArray] Seg ${i}: D${did} no cutPos, using full trajLen=${trajLen.toFixed(1)}`);
              if (trajLen > maxDistToCut) {
                maxDistToCut = trajLen;
              }
            }
          });

          cumulativeDistance += maxDistToCut;
          cutDistance = cumulativeDistance;
          console.log(`[loadFromSegmentsArray] Seg ${i}: cutDistance = ${cutDistance.toFixed(1)} (cumulative)`);
        } else {
          cumulativeDistance = cutDistance;  // Use explicit value
          console.log(`[loadFromSegmentsArray] Seg ${i}: Using explicit cutDistance = ${cutDistance}`);
        }

        // Get visited targets: targets that appeared in PREVIOUS segments but NOT in this segment
        // OR from explicit visitedTargets/frozenTargets if available
        let visitedTargets = [];
        if (seg.visitedTargets) {
          visitedTargets = [...seg.visitedTargets];
        } else if (seg.frozenTargets) {
          // V2 export format uses frozenTargets
          visitedTargets = [...seg.frozenTargets];
        } else {
          // Infer: targets that appeared in previous segments but are missing from this one
          const thisSegTargetIds = new Set((seg.env?.targets || []).map(t => t.id));
          const prevSegTargetIds = new Set();

          // Collect all targets from segments 0 to i-1
          for (let j = 0; j < i; j++) {
            const prevSegTargets = this._segmentTargets[j] || [];
            prevSegTargets.forEach(t => prevSegTargetIds.add(t.id));
          }

          // Visited = targets in previous segments but NOT in current segment
          visitedTargets = Array.from(prevSegTargetIds).filter(id => !thisSegTargetIds.has(id));
        }

        // Get drone positions from cutPosition or cutPositions if available
        const dronePositions = {};
        // Check both cutPosition (singular, from JSON) and cutPositions (plural, legacy)
        const cutPosData = seg.cutPosition || seg.cutPositions;
        console.log(`[loadFromSegmentsArray] Segment ${i} cutPosData:`, cutPosData);
        if (cutPosData) {
          Object.entries(cutPosData).forEach(([did, pos]) => {
            // Handle both formats: direct array [x,y] or object {position: [x,y]}
            if (Array.isArray(pos)) {
              dronePositions[did] = { position: pos };
              console.log(`[loadFromSegmentsArray] Seg ${i} D${did}: stored position [${pos[0].toFixed(2)}, ${pos[1].toFixed(2)}]`);
            } else if (pos && pos.position) {
              dronePositions[did] = { position: pos.position };
              console.log(`[loadFromSegmentsArray] Seg ${i} D${did}: stored position [${pos.position[0].toFixed(2)}, ${pos.position[1].toFixed(2)}]`);
            }
          });
        }

        this._segmentCuts.push({
          cutDistance,
          visitedTargets,
          dronePositions,
        });
        console.log(`[loadFromSegmentsArray] _segmentCuts[${this._segmentCuts.length - 1}] cutDistance=${cutDistance?.toFixed(1) || 'null'}, dronePositions:`, dronePositions);
      }
    }

    // Log summary of all segment cuts for debugging
    console.log(`[loadFromSegmentsArray] Segment cuts summary:`);
    this._segmentCuts.forEach((cut, idx) => {
      console.log(`   Cut ${idx + 1}: cutDistance=${cut.cutDistance?.toFixed(1) || 'null'}, visited=[${cut.visitedTargets.join(',')}]`);
    });

    this._currentSegmentIndex = 0;
    this._isActive = true;

    return true;
  }

  /** Debug info */
  getDebugInfo() {
    return {
      isActive: this._isActive,
      currentSegment: this._currentSegmentIndex,
      totalSegments: this.getTotalSegments(),
      allTargets: this._allTargets.map(t => t.id),
      visitedNow: this.getVisitedTargetsForSegment(this._currentSegmentIndex),
      unfrozen: this.getUnfrozenTargets().map(t => t.id),
    };
  }
}

// Global segmented import manager instance
const segmentedImport = new SegmentedImportManager();

/**
 * Merge arrays by ID - upserts objects from srcArr into baseArr
 * Objects with matching IDs are replaced, new objects are added
 */
function mergeById(baseArr, srcArr) {
  const m = new Map(baseArr.map(o => [String(o.id), o]));
  srcArr.forEach(o => m.set(String(o.id), o));
  return Array.from(m.values());
}

/**
 * Merge current state.env forward into all future segments
 * Updates both missionReplay segments AND missionState.segmentedMission.segments
 * Call this after accepting a solution to propagate env edits to later segments
 */
function mergeEnvForwardFromCurrent(startSegmentIndex) {
  // For segmentInfo workflow (imported segmented missions), DO NOT merge targets forward
  // Each segment should keep its own distinct targets
  const isSegmentInfoWorkflow = state.importedSegmentCuts && state.importedSegmentCuts.length > 0;
  if (isSegmentInfoWorkflow) {
    return;
  }

  const src = JSON.parse(JSON.stringify(state.env));

  // Update missionReplay segments
  for (let i = startSegmentIndex; i < missionReplay.getSegmentCount(); i++) {
    const seg = missionReplay.getSegment(i);
    if (!seg) continue;

    const merged = JSON.parse(JSON.stringify(seg.env));

    merged.targets = mergeById(merged.targets || [], src.targets || []);
    merged.sams = mergeById(merged.sams || [], src.sams || []);
    merged.airports = mergeById(merged.airports || [], src.airports || []);

    missionReplay.replaceSegment(i, {
      solution: seg.solution,
      env: merged,
      prefixDistances: seg.prefixDistances,
      isCheckpointReplan: seg.isCheckpointReplan,
      cutPosition: seg.cutPosition,
      cutPositions: seg.cutPositions,
      cutDistance: seg.cutDistance,  // Preserve cutDistance for segmentInfo workflow
    });
  }

  // Also update missionState.segmentedMission.segments (for segment-by-segment workflow)
  if (missionState.segmentedMission && missionState.segmentedMission.segments) {
    const buildIdx = missionState.currentBuildSegmentIndex || 0;
    appendDebugLine(`[mergeForward] buildIdx=${buildIdx}, src.targets=${(src.targets||[]).map(t=>t.id).join(",")}`);
    for (let i = buildIdx + 1; i < missionState.segmentedMission.segments.length; i++) {
      const seg = missionState.segmentedMission.segments[i];
      if (!seg || !seg.env) {
        appendDebugLine(`[mergeForward] seg ${i} has no env, skipping`);
        continue;
      }
      const beforeIds = (seg.env.targets || []).map(t => t.id);
      const srcIds = (src.targets || []).map(t => t.id);
      // Find IDs unique to this segment (not in src)
      const uniqueToSeg = beforeIds.filter(id => !srcIds.includes(id));
      appendDebugLine(`[mergeForward] seg ${i} BEFORE: ${beforeIds.join(",")}`);
      if (uniqueToSeg.length > 0) {
        appendDebugLine(`[mergeForward] seg ${i} UNIQUE targets (should be preserved): ${uniqueToSeg.join(",")}`);
      }

      seg.env.targets = mergeById(seg.env.targets || [], src.targets || []);
      seg.env.sams = mergeById(seg.env.sams || [], src.sams || []);
      seg.env.airports = mergeById(seg.env.airports || [], src.airports || []);

      const afterIds = seg.env.targets.map(t => t.id);
      appendDebugLine(`[mergeForward] seg ${i} AFTER: ${afterIds.join(",")}`);
    }
  }

  if (startSegmentIndex < missionReplay.getSegmentCount()) {
  }
}

/**
 * Get UI permissions based on current mission mode
 * Returns which actions are allowed in the current state
 */
function getUiPermissions() {
  const mode = missionState.mode;
  const hasCommittedPlan = missionState.committedSegments.length > 0 ||
                          missionReplay.getSegmentCount() > 0 ||
                          segmentedImport.isActive();
  const hasDraftSolution = missionState.draftSolution !== null;

  // CHECKPOINT from replay/cut disables solve; CHECKPOINT from edits allows solve
  const checkpointAllowsSolve = (mode === MissionMode.CHECKPOINT && missionState.checkpointSource !== "replay_cut");

  // Segmented mission: block animate until all segments solved and accepted
  // Support both old segmentedMission system AND new SegmentedImportManager
  const useNewManager = segmentedImport.isActive();

  let segmentedComplete = true;
  if (useNewManager) {
    // New SegmentedImportManager: check if all segments are complete
    // OR if we have routes to animate (e.g., after Reset which sets index back to 0)
    const hasRoutesToAnimate = Object.keys(state.routes || {}).some(did => {
      const r = state.routes[did];
      return r && Array.isArray(r.trajectory) && r.trajectory.length >= 2;
    });
    segmentedComplete = segmentedImport.isComplete() || hasRoutesToAnimate;
  } else if (missionState.segmentedMission) {
    // Old segmentedMission workflow
    const segCount = missionState.segmentedMission.segments.length;
    const segIdx = missionState.currentBuildSegmentIndex || 0;
    segmentedComplete = segIdx >= segCount;
  }

  return {
    // Animation controls
    canAnimate: mode === MissionMode.READY_TO_ANIMATE && segmentedComplete,
    canResume: mode === MissionMode.PAUSED_MID_ANIMATION,
    canPause: mode === MissionMode.ANIMATING,
    canCut: mode === MissionMode.ANIMATING || mode === MissionMode.PAUSED_MID_ANIMATION,

    // Editing controls
    canEnterEdit: mode === MissionMode.IDLE ||
                  mode === MissionMode.CHECKPOINT ||
                  mode === MissionMode.PAUSED_MID_ANIMATION ||
                  mode === MissionMode.READY_TO_ANIMATE ||
                  mode === MissionMode.ANIMATING,  // Live edit during animation
    canAcceptEdits: mode === MissionMode.EDITING_ENV,
    isLiveEditing: mode === MissionMode.EDITING_ENV && missionState.wasAnimating,

    // Planning controls
    // DRAFT_READY (a solution is on screen but NOT yet accepted) keeps the agent
    // ENABLED: a draft is a conversation-in-progress — the user refines it,
    // asks about it, or re-solves before accepting. The gate's real purpose is
    // to stop a re-solve from clobbering an ACCEPTED mission (READY_TO_ANIMATE).
    canSolve: ((mode === MissionMode.IDLE) ||
               (mode === MissionMode.DRAFT_READY) ||
               checkpointAllowsSolve) &&
              (mode !== MissionMode.READY_TO_ANIMATE),
    canOptimize: mode === MissionMode.DRAFT_READY,
    canAcceptSolution: mode === MissionMode.DRAFT_READY,
    canDiscardDraft: mode === MissionMode.DRAFT_READY,

    // AGENT-DRIVEN CONTINUE: the agent may take orders when the mission is PAUSED
    // mid-flight (the operator says "continue"/"abort"/"D1 to A5"). Also allowed while
    // live-editing FROM a paused state (edit the world, then tell the agent to continue)
    // — that is the whole agent-segmented flow. canSolve stays false here (a fresh
    // solve would clobber the mission); prepareAgentContinueCut cuts, then the agent replans.
    canAgentContinue: mode === MissionMode.PAUSED_MID_ANIMATION ||
                      (mode === MissionMode.EDITING_ENV && missionState.wasAnimating),

    // Reset
    canReset: mode !== MissionMode.ANIMATING &&
              mode !== MissionMode.EDITING_ENV &&
              hasCommittedPlan,

    // Derived states for UI
    isAnimating: mode === MissionMode.ANIMATING,
    isEditing: mode === MissionMode.EDITING_ENV,
    hasDraftSolution: hasDraftSolution,
    hasCommittedPlan: hasCommittedPlan,
  };
}

/**
 * Set mission mode and update UI accordingly
 */
function setMissionMode(newMode, reason = "") {
  const oldMode = missionState.mode;
  if (oldMode === newMode) return;

  missionState.mode = newMode;
  missionState.revision += 1;
  appendDebugLine(`Mode: ${newMode}${reason ? ` - ${reason}` : ""}`);

  // Update UI elements based on new mode
  updateStatusBanner();
  updateButtonStates();
  refreshSequenceBox();

  // Update Mission Control panel
  if (typeof updateMissionControlState === "function") {
    updateMissionControlState();
  }

  // Refresh the mission-busy gate (Post-Optimization, Import/Export get
  // locked during EDITING_ENV / ANIMATING / PAUSED_MID_ANIMATION).
  if (typeof updateMissionBusyLocked === "function") {
    updateMissionBusyLocked();
  }
}

/**
 * Get status banner text for current mode
 */
function getStatusBannerText() {
  const mode = missionState.mode;

  switch (mode) {
    case MissionMode.IDLE:
      return "Ready. Load environment, edit, or Run Planner.";
    case MissionMode.EDITING_ENV:
      return "Editing environment. Click Accept Edits when done.";
    case MissionMode.DRAFT_READY:
      return "Draft solution ready. Optimize or Accept Solution to proceed.";
    case MissionMode.READY_TO_ANIMATE:
      return "Solution accepted. Click Animate to start mission.";
    case MissionMode.ANIMATING:
      return "Animating. Press Pause to stop, or C to cut/checkpoint.";
    case MissionMode.PAUSED_MID_ANIMATION:
      return "Paused. Click Animate to resume, or Edit to modify environment.";
    case MissionMode.CHECKPOINT:
      return "Checkpoint. Edit environment and Run Planner to replan remainder.";
    default:
      return `Mode: ${mode}`;
  }
}

/**
 * Update the status banner display
 */
function updateStatusBanner() {
  const banner = $("status-banner");
  const bannerText = $("status-banner-text");
  if (!banner) return;

  const text = getStatusBannerText();
  const mode = missionState.mode;
  const perms = getUiPermissions();

  // Update text
  if (bannerText) {
    bannerText.textContent = text;
  }

  // Set banner color based on mode
  banner.className = "status-banner";
  switch (mode) {
    case MissionMode.ANIMATING:
      banner.classList.add("status-animating");
      break;
    case MissionMode.EDITING_ENV:
      banner.classList.add("status-editing");
      break;
    case MissionMode.DRAFT_READY:
      banner.classList.add("status-draft-ready");
      break;
    case MissionMode.READY_TO_ANIMATE:
      banner.classList.add("status-ready-to-animate");
      break;
    case MissionMode.CHECKPOINT:
      banner.classList.add("status-checkpoint");
      break;
    case MissionMode.PAUSED_MID_ANIMATION:
      banner.classList.add("status-paused");
      break;
    default:
      banner.classList.add("status-idle");
  }

  // Show/hide action buttons based on permissions
  const acceptEditsBtn = $("btn-accept-edits");
  const acceptSolutionBtn = $("btn-accept-solution");
  const discardBtn = $("btn-discard-draft");

  if (acceptEditsBtn) {
    acceptEditsBtn.style.display = perms.canAcceptEdits ? "inline-block" : "none";
  }
  if (acceptSolutionBtn) {
    acceptSolutionBtn.style.display = perms.canAcceptSolution ? "inline-block" : "none";
  }
  if (discardBtn) {
    discardBtn.style.display = perms.canDiscardDraft ? "inline-block" : "none";
  }
}

/**
 * Update button enabled/disabled states based on current mode
 */
function updateButtonStates() {
  const perms = getUiPermissions();

  // Pause button (if exists)
  const pauseBtn = $("btn-pause");
  if (pauseBtn) {
    pauseBtn.disabled = !perms.canPause;
  }

  // Edit button
  const editBtn = $("btn-edit-mode");
  if (editBtn) {
    editBtn.disabled = !perms.canEnterEdit && !perms.isEditing;
  }

  // Accept Edits button
  const acceptEditsBtn = $("btn-accept-edits");
  if (acceptEditsBtn) {
    acceptEditsBtn.disabled = !perms.canAcceptEdits;
    acceptEditsBtn.style.display = perms.isEditing ? "inline-block" : "none";
  }

  // Run Planner / Solve button
  const solveBtn = $("btn-run-planner");
  if (solveBtn) {
    solveBtn.disabled = !perms.canSolve;
  }

  // Accept Solution button
  const acceptSolutionBtn = $("btn-accept-solution");
  if (acceptSolutionBtn) {
    acceptSolutionBtn.disabled = !perms.canAcceptSolution;
    acceptSolutionBtn.style.display = perms.hasDraftSolution ? "inline-block" : "none";
  }

  // Discard Draft button
  const discardBtn = $("btn-discard-draft");
  if (discardBtn) {
    discardBtn.disabled = !perms.canDiscardDraft;
    discardBtn.style.display = perms.hasDraftSolution ? "inline-block" : "none";
  }

  // Keep the Agent panel visually consistent with Mission Control permissions.
  updateAgentUiState(perms);
}

/**
 * Keep the Agent panel state-aware (disable input/send) based on the same mission
 * state machine used by Mission Control.
 */
function updateAgentUiState(perms = null) {
  const p = perms || (typeof getUiPermissions === "function" ? getUiPermissions() : null);

  const inputEl = $("agent-input");
  const btnSend = $("btn-send-agent");
  const statusEl = $("agent-status");

  if (!inputEl || !btnSend) return;

  // Default: enabled
  let disable = false;
  let msg = "";
  let cls = "";

  if (p && p.canAgentContinue) {
    // PAUSED (or live-editing from a pause): the agent is ENABLED so the operator can
    // say "continue" / give a mid-mission order. The agent replans from the
    // cut. This overrides the isAnimating/isEditing/!canSolve disables below.
    disable = false;
    msg = "Paused — tell the agent to continue, or give an order (e.g. \"D1 to A1\", \"abort, all land at A1\").";
    cls = "agent-status-ok";
  } else if (p && p.isAnimating) {
    disable = true;
    msg = "Agent Solve disabled during animation. Pause or stop the animation first.";
    cls = "agent-status-warn";
  } else if (p && p.isEditing) {
    disable = true;
    msg = "Agent Solve disabled during Edit mode. Accept or cancel edits first.";
    cls = "agent-status-warn";
  } else if (p && !p.canSolve) {
    disable = true;
    msg = "Agent Solve disabled while paused/replaying/checkpoint-locked or when a solution is already accepted.";
    cls = "agent-status-warn";
  }

  inputEl.disabled = disable;
  btnSend.disabled = disable;

  // Optional: make the disabled state obvious in the placeholder
  if (disable) {
    inputEl.placeholder = "Agent disabled by mission state. Use Mission Control to Pause/Stop/Reset or enter Checkpoint replanning.";
  } else {
    inputEl.placeholder = "Type your question and press Enter...";
    cls = "agent-status-ok";
    msg = "Agent ready.";
  }

  if (statusEl) {
    statusEl.classList.remove("agent-status-warn", "agent-status-ok");
    if (cls) statusEl.classList.add(cls);
    statusEl.textContent = msg;
  }
}

// ----------------------------------------------------
// Mission Mode Action Handlers
// ----------------------------------------------------

/**
 * Enter editing mode - start tracking changes to environment
 */
function enterEditMode() {
  const perms = getUiPermissions();
  if (!perms.canEnterEdit) {
    appendDebugLine("Cannot enter edit mode in current state");
    return;
  }

  // Track if we entered during animation OR from paused state (for live edit mode)
  // Both cases should create a checkpoint when Done is pressed
  const wasPaused = missionState.mode === MissionMode.PAUSED_MID_ANIMATION ||
                    missionState.pauseContext !== null;
  missionState.wasAnimating = state.animation.active || wasPaused;

  if (missionState.wasAnimating) {
    // Live edit: create staged copies (edits go here, animation uses originals)
    missionState.stagedEnv = JSON.parse(JSON.stringify(state.env));
    missionState.stagedDroneConfigs = JSON.parse(JSON.stringify(state.droneConfigs));

    // If paused, stop the pause context - we're taking over from here
    if (wasPaused && missionState.pauseContext) {
      appendDebugLine("Edit from paused state - will create checkpoint on Done");
    } else {
      appendDebugLine("Live edit mode - animation continues, changes staged");
    }
  } else {
    // Normal edit: save a copy of current env as draft
    if (state.env) {
      missionState.draftEnv = JSON.parse(JSON.stringify(state.env));
    }
  }

  setMissionMode(MissionMode.EDITING_ENV, "entered edit mode");
  state.editMode = true;
  updateCanvasPointerEvents();

  updateEnvPanelLocked();
}

/**
 * Get the environment object to edit (staged during live edit, live otherwise)
 */
function getEditableEnv() {
  if (missionState.wasAnimating && missionState.stagedEnv) {
    return missionState.stagedEnv;
  }
  return state.env;
}

/**
 * Get the drone configs to edit (staged during live edit, live otherwise)
 */
function getEditableDroneConfigs() {
  if (missionState.wasAnimating && missionState.stagedDroneConfigs) {
    return missionState.stagedDroneConfigs;
  }
  return state.droneConfigs;
}

/**
 * Accept edits - commit the draft environment
 */
function acceptEdits() {
  const perms = getUiPermissions();
  if (!perms.canAcceptEdits) {
    appendDebugLine("Cannot accept edits in current state");
    return;
  }

  // If we entered edit mode during animation, use the live edit checkpoint flow
  if (missionState.wasAnimating) {
    acceptEditsWithCheckpoint().catch((err) => {
      // Not awaited by the click handler: surface failures in the debug tab
      // instead of leaving them in the browser console only.
      appendDebugLine(`❌ Live-edit replan failed: ${err?.message || err}`);
      console.error(err);
    });
    return;
  }

  // Check for duplicate target IDs (common cause of "only one target considered")
  const ids = (state.env.targets || []).map(t => String(t.id));
  const dup = ids.filter((id, i) => ids.indexOf(id) !== i);
  if (dup.length) {
    appendDebugLine(`❌ DUP target IDs: ${dup.join(", ")}`);
  }

  // Commit the draft environment
  missionState.acceptedEnv = JSON.parse(JSON.stringify(state.env));
  missionState.draftEnv = null;

  // Determine next state
  const hasCommittedPlan = missionState.committedSegments.length > 0;

  if (hasCommittedPlan) {
    // If we have a committed plan, we're in checkpoint mode - need to replan
    // Clear replay_cut flag since this is intentional replan from edits
    missionState.checkpointSource = null;
    setMissionMode(MissionMode.CHECKPOINT, "edits accepted, ready to replan");
  } else {
    // No committed plan yet - go back to IDLE
    setMissionMode(MissionMode.IDLE, "edits accepted");
  }

  state.editMode = false;
  setAddMode(null);
  updateEnvPanelLocked();
  updateCanvasPointerEvents();

  appendDebugLine("✅ Edits accepted");
  drawEnvironment();
}

/**
 * Prepare a CUT from the current (paused/live-edit) state — the SHARED cut engine.
 *
 * Freezes each drone's flown prefix, records the cut positions, carries remaining
 * fuel/refuels forward, mints the C{n}-{d} checkpoint ids (single cut-number source,
 * _cutCounterClaimed guard — no leak), applies the staged env, merges initialEnvSnapshot
 * so newly-added targets get the green ✕, and turns the editor OFF. It does NOT solve.
 *
 * Both the MANUAL path (acceptEditsWithCheckpoint → runPlanner) and the AGENT path
 * (prepareAgentContinueCut → agent solve) call this first, then supply the solution. The
 * cut is created HERE, at solve-time — a bare pause creates nothing.
 *
 * Returns cut metadata: { checkpointSegNum, cutPositions, frozenPrefixes, cutDistance }.
 */
async function prepareCutFromCurrentState() {
  appendDebugLine("📍 Creating checkpoint from current state...");

  // A cut means we are NOT at a completed mission — clear the full-replay flag
  // so the post-cut Animate resumes the new segment rather than replaying
  // start→end (matches freezeAtCheckpoint's identical line).
  state.animation.missionComplete = false;

  // 1. Stop animation (keeps drone positions intact)
  state.animation.active = false;
  if (state.animation.animationId) {
    cancelAnimationFrame(state.animation.animationId);
    state.animation.animationId = null;
  }

  // If we came from paused state, restore drone states from pause context
  // (in case they were modified during pause)
  if (missionState.pauseContext && missionState.pauseContext.droneStates) {
    // Only restore if state.animation.drones is empty or missing data
    const pausedDrones = missionState.pauseContext.droneStates;
    if (!state.animation.drones || Object.keys(state.animation.drones).length === 0) {
      state.animation.drones = pausedDrones;
      appendDebugLine("Restored drone states from pause context");
    }
  }

  // Clear pause context - we're taking over with checkpoint flow
  missionState.pauseContext = null;

  // 2. Capture current positions & calculate remaining fuel
  const cutPositions = {};
  const disabledDrones = [];
  const remainingFuel = {};
  const dronesAlreadyRefueled = [];  // Track drones that have passed their refuel point

  // Ensure checkpoint container exists
  state.checkpoint = state.checkpoint || { active: false, pct: 0, segments: {} };
  state.checkpoint.active = true;
  state.checkpoint.segments = {};

  // Create checkpoint segment number for this cut. ONE increment per logical cut
  // (see freezeAtCheckpoint): reuse the number if this cut already claimed one.
  if (!state._cutCounterClaimed) {
    state.checkpointSegmentCounter = (state.checkpointSegmentCounter || 0) + 1;
    state._cutCounterClaimed = true;
  }
  const checkpointSegNum = state.checkpointSegmentCounter;

  // Track targets visited in this segment
  const targetsVisitedThisSegment = [];

  Object.entries(state.animation.drones).forEach(([did, droneState]) => {
    const traj = state.routes[did]?.trajectory || [];
    if (traj.length < 2 || (droneState.totalDistance || 0) <= 0) return;

    const currentDist = droneState.distanceTraveled || 0;
    const totalDist = droneState.totalDistance || 0;
    const finished = totalDist > 0 && currentDist >= (totalDist - 1e-6);

    // The live distance includes stationary loiter and flight-profile intervals.
    // Split on the same timeline as the drone, not raw ground distance.
    const { prefixPoints, suffixPoints, splitPoint } =
      split_polyline_at_distance(traj, currentDist, droneState.cumulativeDistances);

    // Store checkpoint segment info
    state.checkpoint.segments[did] = {
      prefix: prefixPoints,
      suffix: suffixPoints,
      splitPoint,
      checkpointDist: currentDist,
      finishedAtCut: finished,
    };

    // Record cut position if drone is still moving
    if (!finished && splitPoint && splitPoint.length >= 2) {
      cutPositions[did] = [splitPoint[0], splitPoint[1]];
    }

    // §11.10 ONE fuel value: fuel_budget IS the tank. FUEL IS PER-LEG; a cut is
    // fuel-neutral. The replan's leg-1 start fuel (TRANSIENT, NOT the tank) =
    // tank − fuel burned this leg (legBurnedDistance; accumulates across cuts,
    // resets only at a refuel). Stored as start_fuel below — fuel_budget (the tank)
    // is never overwritten, so the Stats budget can't shrink after a cut.
    const cfg = state.droneConfigs[did] || {};
    const fullTank = Number(cfg.fuel_budget) || 150;
    // Fuel burned on the current leg = accumulated km since the last refuel. Prefer
    // the DURABLE store state.legFuel[did] (the authoritative value that survives
    // cuts, same source the stat reads at ~8114); fall back to the live animation
    // counter. This makes start_fuel = tank − burned correct across cuts: a cut is
    // fuel-neutral, so the burn carries forward and the tank is NOT refilled for free.
    const _durableBurned = state.legFuel[did];
    const burned = (typeof _durableBurned === "number")
      ? _durableBurned
      : ((droneState.legBurnedDistance) || 0);
    remainingFuel[did] = Math.max(0, fullTank - burned);
    // Track whether the drone has already passed its refuel point (for the
    // already-refueled bookkeeping that clears refuel_airport for leg-2-only replans).
    const refuelDist = droneState.refuelDistance;
    if (refuelDist && refuelDist > 0 && currentDist >= refuelDist) {
      dronesAlreadyRefueled.push(did);
    }
    appendDebugLine(`D${did}: leg fuel burned=${burned.toFixed(1)} of tank ${fullTank} → remaining=${remainingFuel[did].toFixed(1)}`);

    // Check if disabled during edit
    if (missionState.stagedDroneConfigs &&
        missionState.stagedDroneConfigs[did]?.enabled === false &&
        state.droneConfigs[did]?.enabled === true) {
      disabledDrones.push(did);
    }

    // Mark targets that have been passed
    const route = state.routes[did]?.route || [];
    const env = state.env;
    const fullTraj = traj;
    const cumDist = droneState.cumulativeDistances || [0];
    if (!droneState.cumulativeDistances) {
      for (let i = 1; i < fullTraj.length; i++) {
        const lat1 = fullTraj[i - 1][0], lng1 = fullTraj[i - 1][1];
        const lat2 = fullTraj[i][0], lng2 = fullTraj[i][1];
        cumDist.push(cumDist[i - 1] + haversineKm(lat1, lng1, lat2, lng2));
      }
    }

    route.forEach(wp => {
      if (!String(wp).startsWith("T")) return;
      if (state.visitedTargets.includes(wp)) return;

      // Look up target in initialEnvSnapshot (has ALL targets) or fall back to env
      // Snapshot ∪ live env ∪ segments: a target added after the snapshot was
      // taken (e.g. after a C-key cut) must still be found here.
      const allTargets = getAllKnownTargets();
      const target = allTargets.find(t => t.id === wp);
      if (!target) return;

      // §9: shared vertex-based visited check (accept edits / checkpoint).
      if (isTargetVisited(target, fullTraj, cumDist, currentDist, targetHitToleranceKm(target, did))) {
        targetsVisitedThisSegment.push(wp);
        markTargetVisitedByDrone(wp, did);
      }
    });

    // Stop animating but keep current position
    droneState.animating = false;

    // Truncate displayed trajectory for drones still in-flight; a finished
    // drone's trajectory is left as-is, but still stash _fullTrajectory so
    // later redraws/replays have it (matches freezeAtCheckpoint's handling).
    if (!finished) {
      state.routes[did]._fullTrajectory = state.routes[did]._fullTrajectory || traj;
      state.routes[did].trajectory = prefixPoints;
    } else {
      state.routes[did]._fullTrajectory = state.routes[did]._fullTrajectory || traj;
    }
  });

  if (targetsVisitedThisSegment.length > 0) {
    appendDebugLine(`🎯 Marked ${targetsVisitedThisSegment.length} targets as visited: ${targetsVisitedThisSegment.join(', ')}`);
  }

  // 3. Store for marker drawing
  state.pendingCutPositions = cutPositions;
  state.pendingCutDistance = state.animation.missionDistance || 0;
  state.liveEditDisabledDrones = disabledDrones;

  // Create checkpoint IDs
  state.pendingCheckpoints = {};
  Object.entries(cutPositions).forEach(([did, pos]) => {
    const checkpointId = `C${checkpointSegNum}-${did}`;
    state.pendingCheckpoints[did] = {
      id: checkpointId,
      lat: pos[0],
      lng: pos[1],
    };
    appendDebugLine(`✂️ Created checkpoint ${checkpointId} at [${pos[0].toFixed(6)}, ${pos[1].toFixed(6)}]`);
  });

  // Mark disabled drones as "lost" so they continue to render with red diamond
  // This sets the _lostAtPosition so the drone icon stays visible at the checkpoint
  disabledDrones.forEach(did => {
    const droneState = state.animation.drones[did];
    if (droneState && cutPositions[did]) {
      droneState._lostAtCutDistance = state.pendingCutDistance;
      droneState._lostAtPosition = cutPositions[did];
      appendDebugLine(`🔴 D${did} marked as disabled at checkpoint`);
    }
  });

  // 4. Apply staged environment to live
  if (missionState.stagedEnv) {
    state.env = JSON.parse(JSON.stringify(missionState.stagedEnv));
  }
  if (missionState.stagedDroneConfigs) {
    state.droneConfigs = JSON.parse(JSON.stringify(missionState.stagedDroneConfigs));
    // CRITICAL: Update UI to reflect staged configs (including disabled drones)
    // Without this, readDroneConfigsFromUI() in buildCheckpointEnv() gets stale data
    refreshDroneConfigUI();
    appendDebugLine(`Applied staged drone configs: ${Object.entries(state.droneConfigs).map(([d,c]) => `D${d}:${c.enabled?'✓':'✗'}`).join(' ')}`);
  }

  // 4b. Update initialEnvSnapshot to include ALL targets (visited + remaining)
  // This is critical for target marking during animation - it needs to see all targets
  if (state.initialEnvSnapshot) {
    // Merge: keep existing snapshot targets that aren't in the new env, add all new env targets
    const existingTargetIds = new Set((state.initialEnvSnapshot.targets || []).map(t => t.id));
    const newEnvTargets = state.env.targets || [];

    // Start with targets from new env
    const mergedTargets = [...newEnvTargets];

    // Add visited targets that might have been removed from env but should still be visible
    (state.initialEnvSnapshot.targets || []).forEach(t => {
      if (state.visitedTargets.includes(t.id) && !newEnvTargets.some(nt => nt.id === t.id)) {
        mergedTargets.push(t);
      }
    });

    state.initialEnvSnapshot.targets = mergedTargets;
    appendDebugLine(`📸 Updated initialEnvSnapshot: ${mergedTargets.length} targets (incl. ${state.visitedTargets.length} visited)`);
  } else {
    // Create new snapshot from current env
    state.initialEnvSnapshot = JSON.parse(JSON.stringify(state.env));
    appendDebugLine(`📸 Created initialEnvSnapshot: ${state.env.targets?.length || 0} targets`);
  }

  // 5. Apply remaining fuel as the TRANSIENT replan start fuel (§11.10) — NEVER
  // overwrite fuel_budget (the tank). start_fuel rides the next solve request only.
  Object.entries(remainingFuel).forEach(([did, fuel]) => {
    if (state.droneConfigs[did]) {
      state.droneConfigs[did].start_fuel = fuel;
      appendDebugLine(`D${did}: replan start fuel = ${fuel.toFixed(1)} km (tank ${state.droneConfigs[did].fuel_budget} unchanged)`);
    }
  });

  // 5b. Drop the refuel stop a drone has ALREADY passed, so the replan does not
  // plan a SECOND refuel for it. Multi-stop refuel is driven by refuel_stops[]
  // (the legacy single refuel_airport is the 2-leg fallback) — both must be
  // pruned, or expand_to_virtual_drones still splits the remaining mission into
  // an extra leg (the "3 legs from 1 stop after two cuts" bug).
  dronesAlreadyRefueled.forEach(did => {
    const cfg = state.droneConfigs[did];
    if (cfg) {
      cfg.refuel_airport = "";
      // Drop the FIRST remaining stop (the one just passed). The drone refuels
      // at most once per leg, so one cut past the refuel point retires one stop.
      if (Array.isArray(cfg.refuel_stops) && cfg.refuel_stops.length) {
        const dropped = cfg.refuel_stops.shift();
        appendDebugLine(`D${did}: retired passed refuel stop ${dropped} (${cfg.refuel_stops.length} remaining)`);
      } else {
        appendDebugLine(`D${did}: cleared refuel_airport (already refueled)`);
      }
    }
    // Also update the UI dropdown
    const refuelSel = $(`cfg-d${did}-refuel`);
    if (refuelSel) refuelSel.value = "";
  });

  // Log disabled drones
  if (disabledDrones.length > 0) {
    appendDebugLine(`🔴 Disabled drones (red markers): ${disabledDrones.join(', ')}`);
  }

  // 6. Clear edit state
  missionState.wasAnimating = false;
  missionState.stagedEnv = null;
  missionState.stagedDroneConfigs = null;
  state.editMode = false;
  setAddMode(null);
  updateEnvPanelLocked();
  updateCanvasPointerEvents();


  // 7. Update display (coalesced — this runs inside the redraw-heavy accept pipeline)
  requestDraw();
  setMissionMode(MissionMode.CHECKPOINT, "live edit checkpoint created");

  // Return the cut metadata so the caller (manual runPlanner OR agent solve) can render
  // the frozen prefix + diamond consistently. checkpoint.segments[did].prefix holds the
  // frozen flown path captured above.
  const frozenPrefixes = {};
  Object.entries(state.checkpoint.segments || {}).forEach(([did, seg]) => {
    if (seg && Array.isArray(seg.prefix) && seg.prefix.length >= 2) {
      frozenPrefixes[did] = seg.prefix;
    }
  });
  return {
    checkpointSegNum,
    cutPositions,
    frozenPrefixes,
    cutDistance: state.pendingCutDistance || 0,
  };
}

/**
 * MANUAL live-edit Done: create the cut, then auto-solve from it (the classic behavior).
 * Called when the operator clicks Done while live-editing during animation. Identical to
 * the old acceptEditsWithCheckpoint — the cut engine (steps 1–7) is now shared via
 * prepareCutFromCurrentState; step 8 (auto-solve) + step 9 (auto-accept) stay here.
 */
async function acceptEditsWithCheckpoint() {
  await prepareCutFromCurrentState();

  // 8. Auto-trigger solver (MANUAL path). Agent-driven continue does NOT go through
  // Accept Edits — the operator edits with the agent still enabled and just says
  // "continue", which routes to prepareAgentContinueCut instead. This manual auto-solve
  // is untouched.
  appendDebugLine("🔄 Replanning from checkpoint...");
  await runPlanner();

  // 9. Auto-accept solution (but DON'T auto-resume animation)
  if (missionState.draftSolution) {
    acceptSolution();
    appendDebugLine("✅ Replan ready - click Animate to resume");
  } else {
    appendDebugLine("❌ Replan failed - edit environment and try again");
  }
}

/**
 * Cancel editing - discard draft changes
 * For live edit mode, discards staged changes and returns to animation
 */
function cancelEdits() {
  if (missionState.mode !== MissionMode.EDITING_ENV) return;

  // If we entered edit mode during animation (live edit), use special handling
  if (missionState.wasAnimating) {
    cancelLiveEdit();
    return;
  }

  // Restore from draft or accepted env
  if (missionState.draftEnv) {
    state.env = JSON.parse(JSON.stringify(missionState.draftEnv));
  } else if (missionState.acceptedEnv) {
    state.env = JSON.parse(JSON.stringify(missionState.acceptedEnv));
  }
  missionState.draftEnv = null;

  // Determine which state to return to
  const hasCommittedPlan = missionState.committedSegments.length > 0;
  if (hasCommittedPlan) {
    // Clear replay_cut flag since this is intentional replan from edits
    missionState.checkpointSource = null;
    setMissionMode(MissionMode.CHECKPOINT, "edits cancelled");
  } else {
    setMissionMode(MissionMode.IDLE, "edits cancelled");
  }

  state.editMode = false;
  setAddMode(null);
  updateEnvPanelLocked();
  updateCanvasPointerEvents();

  appendDebugLine("❌ Edits cancelled, reverted to previous state");
  drawEnvironment();
}

/**
 * Cancel live edit - discard staged changes and return to animation
 * Animation continues as if nothing happened
 */
function cancelLiveEdit() {
  appendDebugLine("Live edit cancelled - animation continues");

  // Discard staged changes (animation uses original state.env)
  missionState.stagedEnv = null;
  missionState.stagedDroneConfigs = null;
  missionState.wasAnimating = false;
  state.editMode = false;
  setAddMode(null);
  updateEnvPanelLocked();
  updateCanvasPointerEvents();


  // Return to animation mode (animation is still running)
  setMissionMode(MissionMode.ANIMATING, "live edit cancelled");
  drawEnvironment();
}

/**
 * Accept solution - commit the draft solution as the next segment
 */
function acceptSolution() {
  const perms = getUiPermissions();
  if (!perms.canAcceptSolution) {
    appendDebugLine("Cannot accept solution in current state");
    return;
  }

  if (!missionState.draftSolution) {
    appendDebugLine("No draft solution to accept");
    return;
  }

  // Check if we're using the new SegmentedImportManager
  const useNewManager = segmentedImport.isActive();

  if (useNewManager) {
    // ========================================
    // NEW CLEAN PATH: Use SegmentedImportManager
    // ========================================
    acceptSolutionWithManager();
    return;
  }

  // ========================================
  // LEGACY PATH: Original complex logic
  // ========================================

  // NOTE: We do NOT mark targets as visited here!
  // Targets only get marked as visited (green X) when user presses C (cut) during animation.
  // The cut determines which targets are "frozen" (behind the marker).

  // Snapshot the authoritative current env
  const envSnapshot = JSON.parse(JSON.stringify(state.env));
  envSnapshot.targets = (envSnapshot.targets || []).filter(t => !state.visitedTargets.includes(t.id));

  let segment;
  const currentIdx = missionReplay.getCurrentSegmentIndex();
  const existingSegment = missionReplay.getSegment(currentIdx);
  const nextSegment = missionReplay.getSegment(currentIdx + 1);

  // Check if we're in segmented import workflow (either old segmentInfo or new segmentedImport)
  const isSegmentInfoWorkflow = (
    segmentedImport.isActive() ||  // NEW: Check new segmentedImport manager
    (state.importedSegmentCuts && state.importedSegmentCuts.length > 0) ||
    (existingSegment &&
      existingSegment.solution &&
      Object.keys(existingSegment.solution.routes || {}).length === 0 &&
      missionReplay.getSegmentCount() > 1)
  );

  // For NON-checkpoint replans: Use state.routes (which may have been optimized).
  // For CHECKPOINT replans: Use draftSolution.routes (segment trajectory only, not combined).
  // applyDraftSolutionToUI combines frozen+new for DISPLAY, but we store only the new segment.
  const solutionToStore = JSON.parse(JSON.stringify(missionState.draftSolution || {}));
  const isCheckpointReplan = solutionToStore.isCheckpointReplan;
  if (!isCheckpointReplan && state.routes && Object.keys(state.routes).length > 0) {
    solutionToStore.routes = JSON.parse(JSON.stringify(state.routes));
    // Accepting a FRESH solve (not a cut replan) starts a new mission timeline, so
    // the next cut must be C1. Reset the cut counter — an edit-during-animation
    // accept or other path may have already claimed a number.
    state.checkpointSegmentCounter = 0;
    state._cutCounterClaimed = false;
  }
  // For checkpoint replans, keep draftSolution.routes as-is (segment trajectory only)

  // CRITICAL: Save FULL solution BEFORE any truncation happens
  // This is used by Reset to show complete seg-0 trajectory and by animation for segment switches
  // For isSegmentInfoWorkflow: segments exist, use currentIdx
  // For LEGACY: adding new segment, use count (which equals currentIdx for sequential adds)
  const segmentIdxForSave = isSegmentInfoWorkflow ? currentIdx : missionReplay.getSegmentCount();
  if (!missionState.fullSolutionsPerSegment) {
    missionState.fullSolutionsPerSegment = {};
  }
  missionState.fullSolutionsPerSegment[segmentIdxForSave] = JSON.parse(JSON.stringify(solutionToStore));
  if (segmentIdxForSave === 0) {
    missionState.seg0FullSolution = JSON.parse(JSON.stringify(solutionToStore));
    appendDebugLine(`📦 [ACCEPT] Saved seg-0 FULL solution BEFORE truncation (${Object.keys(solutionToStore.routes || {}).length} drones)`);
  }

// CRITICAL: Only truncate when committing a non-checkpoint segment.
// For checkpoint replans, solver already returns routes starting at D?_START (synthetic start),
// so truncating here will destroy the new segment trajectory.
if (!solutionToStore.isCheckpointReplan &&
    state.pendingCutPositions && Object.keys(state.pendingCutPositions).length > 0) {
  Object.entries(state.pendingCutPositions).forEach(([did, cutPos]) => {
      if (!cutPos || cutPos.length < 2) return;
      const routeData = solutionToStore.routes?.[did];
      if (!routeData?.trajectory || routeData.trajectory.length === 0) return;

      const traj = routeData.trajectory;
      // Find trajectory point closest to cut position
      let cutIdx = 0;
      let minDist = Infinity;
      for (let i = 0; i < traj.length; i++) {
        const dx = traj[i][0] - cutPos[0];
        const dy = traj[i][1] - cutPos[1];
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < minDist) {
          minDist = dist;
          cutIdx = i;
        }
      }

      // Truncate trajectory at cut point
      const truncatedTraj = traj.slice(0, cutIdx + 1);
      // Add exact cut position as final point if needed
      const lastPt = truncatedTraj[truncatedTraj.length - 1];
      if (Math.abs(lastPt[0] - cutPos[0]) > 0.01 || Math.abs(lastPt[1] - cutPos[1]) > 0.01) {
        truncatedTraj.push([cutPos[0], cutPos[1]]);
      }

      solutionToStore.routes[did].trajectory = truncatedTraj;
      // Also update state.routes for immediate display
      if (state.routes[did]) {
        state.routes[did].trajectory = JSON.parse(JSON.stringify(truncatedTraj));
      }
    });
  }

  if (isSegmentInfoWorkflow) {
    // Old SegmentInfo workflow
    // Use drone_configs from SOLVE time (saved in draftSolution), NOT current UI state
    const segInfoDroneConfigs = missionState.draftSolution?.droneConfigs || state.droneConfigs;

    // Compute visited_targets for this segment
    const segVisitedTargets = new Set();
    Object.values(solutionToStore.routes || {}).forEach(routeData => {
      (routeData.route || []).forEach(wp => {
        if (String(wp).startsWith('T')) {
          segVisitedTargets.add(wp);
        }
      });
    });

    missionReplay.replaceSegment(currentIdx, {
      solution: solutionToStore,
      env: envSnapshot,
      cutDistance: existingSegment.cutDistance,
      cutPositions: existingSegment.cutPositions,
      isCheckpointReplan: existingSegment.isCheckpointReplan,
      drone_configs: JSON.parse(JSON.stringify(segInfoDroneConfigs)),  // Save SOLVE-time config
      visited_targets: [...segVisitedTargets],  // Store visited targets for deterministic replay
    });
    segment = missionReplay.getSegment(currentIdx);
    if (nextSegment) {
      missionReplay.setCurrentSegmentIndex(currentIdx + 1);
      missionState.currentBuildSegmentIndex = currentIdx + 1;
    }
  } else {
    // Normal workflow: ADD new segment
    // Read drone_configs directly from the UI table - what the user sees is what gets saved
    const segmentDroneConfigs = readDroneConfigsFromUI();
    appendDebugLine(`[ACCEPT] UI drone_configs: ${Object.entries(segmentDroneConfigs).map(([d,c]) => `D${d}:${c.enabled?'✓':'✗'}`).join(' ')}`);

    // Compute visited_targets: all targets in this segment's routes
    // This makes replay deterministic - we don't rely on geometric checks
    const segmentVisitedTargets = new Set();
    Object.values(solutionToStore.routes || {}).forEach(routeData => {
      (routeData.route || []).forEach(wp => {
        if (String(wp).startsWith('T')) {
          segmentVisitedTargets.add(wp);
        }
      });
    });

    // Use cutDistance from draftSolution (captured at solve time) as primary source
    // Fall back to state.pendingCutDistance for non-checkpoint replans
    const segmentCutDistance = solutionToStore.cutDistance ?? state.pendingCutDistance ?? null;

    segment = missionReplay.addSegment({
      solution: solutionToStore,
      env: envSnapshot,
      cutDistance: segmentCutDistance,
      isCheckpointReplan: solutionToStore.isCheckpointReplan || false,
      cutPositions: state.pendingCutPositions
        ? JSON.parse(JSON.stringify(state.pendingCutPositions))
        : null,
      lostDrones: state.pendingLostDrones.length > 0
        ? [...state.pendingLostDrones]
        : null,
      drone_configs: JSON.parse(JSON.stringify(segmentDroneConfigs)),  // Save SOLVE-time config
      visited_targets: [...segmentVisitedTargets],  // Store visited targets for deterministic replay
    });
    missionReplay.setCurrentSegmentIndex(segment.index);

    // DEBUG: Print segment table after adding segment
    appendDebugLine(missionReplay.printSegmentTable());

    // ========================================
    // SEGMENT STORE: Save clean segment for export
    // ========================================
    // lostDrones = drones that were disabled/lost at the START of this segment
    // For segment 0, no lost drones. For segment N, use pendingLostDrones.
    const segmentLostDrones = segment.index === 0 ? [] : [...(state.pendingLostDrones || [])];
    const cleanSegment = buildCleanSegment(
      solutionToStore,
      segmentDroneConfigs,
      envSnapshot,
      [...state.visitedTargets],  // frozenTargets = targets visited BEFORE this segment
      segment.index === 0 ? null : state.pendingCutPositions,
      segmentLostDrones
    );
    const _savedIdx = segmentStore.addSegment(cleanSegment);
    appendDebugLine(`[SegmentStore] Segment ${_savedIdx} saved: ${Object.keys(cleanSegment.routes).length} drones`);
  }

  // Save cut positions before clearing (needed for fresh segmentation workflow)
  const savedCutPositions = state.pendingCutPositions
    ? JSON.parse(JSON.stringify(state.pendingCutPositions))
    : null;
  // Clear the pending cut data after use
  state.pendingCutPositions = null;
  state.pendingCheckpoints = null;  // Clear the checkpoints after they've been added to env
  state.pendingLostDrones = [];
  state.liveEditDisabledDrones = [];
  state.pendingCutDistance = null;

  // Clear checkpoint state so next solve doesn't use stale data
  // (Bug fix: prevents trajectory from going back to airport after accept→solve)
  state.checkpoint = { active: false, pct: 0.5, segments: {} };

  // Also keep in missionState.committedSegments for backward compatibility
  missionState.committedSegments.push({
    index: segment.index,
    solution: JSON.parse(JSON.stringify(solutionToStore)),
    env: envSnapshot,
    timestamp: segment.timestamp,
    cutDistance: segment.cutDistance,
    isCheckpointReplan: solutionToStore.isCheckpointReplan || false,
  });

  // Propagate env edits forward into all future segments
  const finalIdx = missionReplay.getCurrentSegmentIndex();
  mergeEnvForwardFromCurrent(finalIdx + 1);
  missionState.currentSegmentIndex = segment.index;

  // Clear the draft
  missionState.draftSolution = null;

  appendDebugLine(`✅ Solution accepted as segment ${segment.index + 1}`);
  // This cut is committed; the NEXT cut must claim a fresh checkpoint number.
  state._cutCounterClaimed = false;

  // --- Old SegmentInfo workflow: advance to next segment or finish ---
  if (isSegmentInfoWorkflow) {
    const nextIdx = missionReplay.getCurrentSegmentIndex();
    const nextSeg = missionReplay.getSegment(nextIdx);

    const prevCutIdx = nextIdx - 1;
    if (state.importedSegmentCuts && prevCutIdx >= 0 && prevCutIdx < state.importedSegmentCuts.length) {
      const cutData = state.importedSegmentCuts[prevCutIdx];
      // Authoritative visited set for this segment: clear then seed (owner derived
      // inline by seedVisitedFromSegment — no separate rebuild needed).
      clearVisited();
      seedVisitedFromSegment(cutData.visitedTargets || []);
    }

    if (nextSeg && Object.keys(nextSeg.solution.routes || {}).length === 0) {
      const allTargetsEnv = state.initialEnvSnapshot || nextSeg.env;
      state.env = JSON.parse(JSON.stringify(allTargetsEnv));
      missionState.acceptedEnv = JSON.parse(JSON.stringify(allTargetsEnv));
      state.droneConfigs = JSON.parse(JSON.stringify(allTargetsEnv.drone_configs || state.droneConfigs || {}));
      state.env.drone_configs = state.droneConfigs;
      missionState.draftSolution = null;

      // Support both 'cutPosition' (V2 export) and 'cutPositions' (legacy)
      const nextSegCutPos = nextSeg.cutPosition || nextSeg.cutPositions;
      if (nextSegCutPos) {
        state.pendingCutPositions = JSON.parse(JSON.stringify(nextSegCutPos));
      }

      initDroneConfigsFromEnv();
      updateSamWrappingClientSide();
      setMissionMode(MissionMode.IDLE, `segment ${nextIdx + 1} ready to solve`);
      drawEnvironment();
      return;
    } else {
      const combinedRoutes = buildCombinedRoutesFromSegments();
      state.routes = combinedRoutes;
      Object.keys(combinedRoutes).forEach(did => {
        state.trajectoryVisible[did] = true;
      });
      setMissionMode(MissionMode.READY_TO_ANIMATE, "all segments accepted; ready to replay");
      appendDebugLine("✅ All segments accepted. Ready to Animate full mission.");
      drawEnvironment();
      return;
    }
  }

  // --- Original segmentedMission workflow (for old format) ---
  if (missionState.segmentedMission) {
    const nextIdx = (missionState.currentBuildSegmentIndex || 0) + 1;
    missionState.currentBuildSegmentIndex = nextIdx;

    const nextSeg = missionState.segmentedMission.segments[nextIdx];
    if (nextSeg) {
      state.env = JSON.parse(JSON.stringify(nextSeg.env));
      missionState.acceptedEnv = JSON.parse(JSON.stringify(nextSeg.env));
      state.droneConfigs = JSON.parse(JSON.stringify(nextSeg.drone_configs || state.env.drone_configs || {}));
      state.env.drone_configs = state.droneConfigs;
      missionState.draftSolution = null;

      setMissionMode(MissionMode.IDLE, `segment ${nextIdx + 1} ready to solve`);
      appendDebugLine(`➡️ Loaded segment ${nextIdx + 1} env. Ready to Solve.`);

      initDroneConfigsFromEnv();
      updateSamWrappingClientSide();
      drawEnvironment();
      return;
    } else {
      // Build combined routes from all segments for display and animation
      const combinedRoutes = buildCombinedRoutesFromSegments();
      state.routes = combinedRoutes;
      Object.keys(combinedRoutes).forEach(did => {
        state.trajectoryVisible[did] = true;
      });
      setMissionMode(MissionMode.READY_TO_ANIMATE, "all segments accepted; ready to replay");
      appendDebugLine("✅ All segments accepted. Ready to Animate full mission.");
      drawEnvironment();
      return;
    }
  }

  // Fresh segmentation workflow (building segments from N1 JSON)
  // After accepting a segment, keep currently displayed targets AND add targets from the new solution
  // The frozen targets (visited) will have green X marks drawn by drawEnvironment
  if (state.initialEnvSnapshot && state.visitedTargets.length > 0) {
    // We have frozen targets from pressing C - this is fresh segmentation

    // Extract targets from the newly accepted solution's routes
    const newSolutionRouteTargets = new Set();
    if (missionState.draftSolution?.routes) {
      Object.values(missionState.draftSolution.routes).forEach(routeData => {
        const route = routeData.route || [];
        route.forEach(stop => {
          if (stop && stop.startsWith('T')) {
            newSolutionRouteTargets.add(stop);
          }
        });
      });
    }

    // Add new targets from the solution to state.env.targets (if not already present)
    const currentTargetIds = new Set((state.env.targets || []).map(t => t.id));
    const targetsToAdd = (state.initialEnvSnapshot.targets || []).filter(
      t => newSolutionRouteTargets.has(t.id) && !currentTargetIds.has(t.id)
    );

    if (targetsToAdd.length > 0) {
      state.env.targets = [...(state.env.targets || []), ...targetsToAdd];
      appendDebugLine(`[ACCEPT] Added ${targetsToAdd.length} new targets: ${targetsToAdd.map(t=>t.id).join(",")}`);
    }

    // Also add any live-env target the snapshot lacks (e.g. added after a C-key
    // cut). targetsToAdd is drawn FROM the snapshot, so filtering it against the
    // snapshot always came back empty and new targets never reached it.
    const snapshotTargetIds = new Set((state.initialEnvSnapshot.targets || []).map(t => t.id));
    const newTargetsForSnapshot = (state.env.targets || []).filter(t => !snapshotTargetIds.has(t.id));
    if (newTargetsForSnapshot.length > 0) {
      state.initialEnvSnapshot.targets = [...(state.initialEnvSnapshot.targets || []), ...newTargetsForSnapshot];
    }

    // CRITICAL: Use the segment's stored drone_configs (not state.env which has initial configs)
    // The segment stores the correct cumulative disabled drone state
    state.droneConfigs = JSON.parse(JSON.stringify(segment.drone_configs || state.droneConfigs || {}));
    state.env.drone_configs = state.droneConfigs;
    missionState.acceptedEnv = JSON.parse(JSON.stringify(state.env));

    // Restore cut positions so the marker stays visible
    // The marker shows where the drone will start for the next segment
    if (savedCutPositions) {
      state.pendingCutPositions = savedCutPositions;
    }

    // Set to READY_TO_ANIMATE so Animate button is enabled
    setMissionMode(MissionMode.READY_TO_ANIMATE, "segment accepted, ready to animate");
    drawEnvironment();
    return;
  }

  // Non-segmented mission: normal flow (no cuts made yet)
  setMissionMode(MissionMode.READY_TO_ANIMATE, "solution accepted");
  drawEnvironment();
}

/**
 * Accept solution using the new SegmentedImportManager
 * This is the CLEAN path - simple and modular
 */
function acceptSolutionWithManager() {
  const currentSegIdx = segmentedImport.getCurrentSegmentIndex();
  appendDebugLine(`[ACCEPT-NEW] Segment ${currentSegIdx}, manager: ${JSON.stringify(segmentedImport.getDebugInfo())}`);

  // For NON-checkpoint replans: Use state.routes (which may have been optimized).
  // For CHECKPOINT replans: Use draftSolution.routes (segment trajectory only, not combined).
  const solutionToStore = JSON.parse(JSON.stringify(missionState.draftSolution || {}));
  const isCheckpointReplan = solutionToStore.isCheckpointReplan;

  if (!isCheckpointReplan && state.routes && Object.keys(state.routes).length > 0) {
    solutionToStore.routes = JSON.parse(JSON.stringify(state.routes));
  }
  // For checkpoint replans, keep draftSolution.routes as-is (segment trajectory only)

  // 1. Update MissionReplay segment with the solution (using optimized routes)
  // IMPORTANT: Use drone_configs from SOLVE time (saved in draftSolution), NOT current UI state
  // This ensures we save what was actually solved, not what the UI shows after user changes
  const currentSeg = missionReplay.getSegment(currentSegIdx);
  const solveTimeDroneConfigs = missionState.draftSolution?.droneConfigs || state.droneConfigs;

  // Save the FULL original solution before it gets truncated later
  // This is used by Reset and animation segment switches to show complete trajectories
  if (!missionState.fullSolutionsPerSegment) {
    missionState.fullSolutionsPerSegment = {};
  }
  missionState.fullSolutionsPerSegment[currentSegIdx] = JSON.parse(JSON.stringify(solutionToStore));
  // Keep seg0FullSolution for backward compatibility with Reset
  if (currentSegIdx === 0) {
    missionState.seg0FullSolution = JSON.parse(JSON.stringify(solutionToStore));
    // Log trajectory lengths at save time for debugging
    const seg0Lens = Object.entries(missionState.seg0FullSolution.routes || {}).map(([did, rd]) => `D${did}:${rd.trajectory?.length || 0}`).join(', ');
    appendDebugLine(`📦 [ACCEPT] seg0FullSolution saved: [${seg0Lens}]`);
  }

  if (currentSeg) {
    // DEBUG: Log trajectory lengths being stored
    const trajLens = Object.entries(solutionToStore.routes || {}).map(([did, rd]) => `D${did}:${rd.trajectory?.length || 0}`).join(', ');
    appendDebugLine(`📦 [ACCEPT] Storing seg ${currentSegIdx} in missionReplay: [${trajLens}]`);

    // Merge lostDrones: include both existing lost drones and newly disabled ones
    const mergedLostDrones = [...(currentSeg.lostDrones || [])];
    if (state.pendingLostDrones && state.pendingLostDrones.length > 0) {
      state.pendingLostDrones.forEach(did => {
        if (!mergedLostDrones.includes(did)) {
          mergedLostDrones.push(did);
          appendDebugLine(`🔴 Adding D${did} to lostDrones (disabled during live edit)`);
        }
      });
    }

    missionReplay.replaceSegment(currentSegIdx, {
      solution: solutionToStore,
      env: segmentedImport.getFullEnv(),
      cutDistance: currentSeg.cutDistance,
      cutPositions: currentSeg.cutPositions,
      lostDrones: mergedLostDrones.length > 0 ? mergedLostDrones : null,
      visitedTargets: currentSeg.visitedTargets,
      isCheckpointReplan: currentSeg.isCheckpointReplan,
      drone_configs: JSON.parse(JSON.stringify(solveTimeDroneConfigs)),
    });

    // DEBUG: Verify what was stored
    const storedSeg = missionReplay.getSegment(currentSegIdx);
    const storedLens = Object.entries(storedSeg?.solution?.routes || {}).map(([did, rd]) => `D${did}:${rd.trajectory?.length || 0}`).join(', ');
    appendDebugLine(`📦 [ACCEPT] Verified in missionReplay: [${storedLens}]`);

    // ========================================
  }

  // 2. Add to committed segments for backward compatibility
  missionState.committedSegments.push({
    index: currentSegIdx,
    solution: JSON.parse(JSON.stringify(solutionToStore)),
    env: segmentedImport.getFullEnv(),
    timestamp: Date.now(),
    cutDistance: currentSeg?.cutDistance || null,
    isCheckpointReplan: currentSegIdx > 0,
  });

  // 3. Clear draft
  missionState.draftSolution = null;

  // 4. Advance to next segment
  const hasMore = segmentedImport.advanceToNextSegment();
  const newSegIdx = segmentedImport.getCurrentSegmentIndex();

  if (hasMore) {
    // More segments to solve - calculate marker and freeze trajectory

    // 5. Calculate marker position and visited targets using cutDistance on the CONCATENATED trajectory
    // IMPORTANT: If the JSON didn't have solutions, cutDistance may be 0 from import.
    // We need to calculate it from the ACTUAL solved trajectory.
    let cutDistance = segmentedImport.getCutDistanceForSegment(newSegIdx);

    // If cutDistance is 0 or null, calculate from the solved trajectory
    if (!cutDistance || cutDistance <= 0) {
      // Calculate from the trajectory we just solved (segment currentSegIdx)
      const solvedRoutes = solutionToStore.routes || {};
      let maxTrajLen = 0;
      Object.values(solvedRoutes).forEach(routeData => {
        const traj = routeData?.trajectory || [];
        if (traj.length >= 2) {
          let trajLen = 0;
          for (let i = 1; i < traj.length; i++) {
            const dx = traj[i][0] - traj[i-1][0];
            const dy = traj[i][1] - traj[i-1][1];
            trajLen += Math.sqrt(dx * dx + dy * dy);
          }
          if (trajLen > maxTrajLen) maxTrajLen = trajLen;
        }
      });
      cutDistance = maxTrajLen;
      appendDebugLine(`[ACCEPT] cutDistance calculated from solved trajectory: ${cutDistance.toFixed(1)}`);

      // Update segmentedImport's _segmentCuts so future calls get the correct value
      if (newSegIdx > 0 && segmentedImport._segmentCuts && segmentedImport._segmentCuts[newSegIdx - 1]) {
        segmentedImport._segmentCuts[newSegIdx - 1].cutDistance = cutDistance;
        appendDebugLine(`[ACCEPT] Updated _segmentCuts[${newSegIdx - 1}].cutDistance = ${cutDistance.toFixed(1)}`);
      }
    }

    state.pendingCutPositions = {};

    // Derive lostDrones: drones that started segment N but won't start segment N+1
    // Two ways a drone becomes "lost":
    // 1. Was flying at C{N} but not at C{N+1} (crashed/ran out of fuel)
    // 2. Was enabled in segment N-1 but disabled before solving segment N (manually disabled)
    const savedLostDrones = [];
    const thisCutPositions = segmentedImport.getCutPositionForSegment(newSegIdx);
    const nextCutPositions = segmentedImport.getCutPositionForSegment(newSegIdx + 1);
    
    if (thisCutPositions && nextCutPositions) {
      // Case 1: Drones in this cut but not in next cut are "lost" at this cut
      Object.keys(thisCutPositions).forEach(did => {
        if (!nextCutPositions[did]) {
          savedLostDrones.push(did);
        }
      });
    } else if (thisCutPositions && !nextCutPositions) {
      // This is the last cut - no drones lost at this marker
      // (drones finish at their end airports, not "lost")
    }
    
    // Case 2: Also check for drones that were enabled in previous segment but disabled now
    // These are "lost" even if they weren't flying at a checkpoint
    const prevSeg = missionReplay.getSegment(newSegIdx - 1);
    const prevDroneConfigs = prevSeg?.drone_configs || {};
    
    // IMPORTANT: Get the NEXT segment's drone_configs from JSON to compare
    // Don't use state.droneConfigs because it may have been updated already
    const nextSegmentConfigs = segmentedImport.getDroneConfigsForSegment(newSegIdx) || {};
    
    Object.keys(prevDroneConfigs).forEach(did => {
      const wasEnabled = prevDroneConfigs[did]?.enabled === true;
      const isEnabled = nextSegmentConfigs[did]?.enabled === true;

      if (wasEnabled && !isEnabled && !savedLostDrones.includes(did)) {
        // Drone was active before but disabled now - mark as lost
        savedLostDrones.push(did);
      }
    });

    // Case 3: Also include drones from state.pendingLostDrones (from live edit checkpoint)
    // runPlanner() sets this when a drone has a cut position but is disabled
    if (state.pendingLostDrones && state.pendingLostDrones.length > 0) {
      state.pendingLostDrones.forEach(did => {
        if (!savedLostDrones.includes(did)) {
          savedLostDrones.push(did);
          appendDebugLine(`🔴 Adding D${did} to savedLostDrones (from pendingLostDrones)`);
        }
      });
    }

    // For segmented import: Use state.routes (which has the ACTUAL displayed trajectory: frozen + merged)
    // instead of rebuilding from MissionReplay (which has RAW solver outputs before truncation)
    const concatenatedRoutes = newSegIdx === 1 ? {} : null;

    if (newSegIdx === 1) {
      // First Accept - build from MissionReplay
      for (let i = 0; i < newSegIdx; i++) {
        const seg = missionReplay.getSegment(i);
        if (!seg || !seg.solution || !seg.solution.routes) continue;

        Object.entries(seg.solution.routes).forEach(([droneId, routeData]) => {
          const trajectory = routeData.trajectory || [];
          if (trajectory.length === 0) return;

          if (!concatenatedRoutes[droneId]) {
            concatenatedRoutes[droneId] = {
              trajectory: [...trajectory],
            };
          } else {
            const existing = concatenatedRoutes[droneId].trajectory;
            const lastPoint = existing[existing.length - 1];
            const firstPoint = trajectory[0];
            const isDuplicate = Math.abs(lastPoint[0] - firstPoint[0]) < 0.001 &&
                               Math.abs(lastPoint[1] - firstPoint[1]) < 0.001;
            if (isDuplicate) {
              concatenatedRoutes[droneId].trajectory = existing.concat(trajectory.slice(1));
            } else {
              concatenatedRoutes[droneId].trajectory = existing.concat(trajectory);
            }
          }
        });
      }
    }

    // For visited targets calculation, we need the routes regardless of cutPositions source
    // For newSegIdx > 1: use state.routes (actual displayed trajectory)
    // For newSegIdx === 1: use concatenatedRoutes (built from MissionReplay)
    const routesToUse = newSegIdx === 1 ? concatenatedRoutes : state.routes;

    // DEBUG: Log what routesToUse contains
    const routeDroneIds = Object.keys(routesToUse || {});
    appendDebugLine(`[ACCEPT] routesToUse has ${routeDroneIds.length} drones: [${routeDroneIds.join(",")}]`);
    routeDroneIds.forEach(did => {
      const trajLen = routesToUse[did]?.trajectory?.length || 0;
      appendDebugLine(`   D${did}: trajectory.length = ${trajLen}`);
    });

    // Get cutPositions - PRIORITIZE the JSON's stored positions (thisCutPositions)
    // Only calculate from trajectories if JSON doesn't have positions
    appendDebugLine(`[ACCEPT] thisCutPositions from JSON: ${thisCutPositions ? JSON.stringify(thisCutPositions) : 'null'}`);
    if (thisCutPositions && Object.keys(thisCutPositions).length > 0) {
      // USE THE JSON's CUT POSITIONS DIRECTLY
      state.pendingCutPositions = JSON.parse(JSON.stringify(thisCutPositions));
      const cutPosStr = Object.entries(state.pendingCutPositions).map(([did, pos]) => `D${did}:[${pos[0].toFixed(1)},${pos[1].toFixed(1)}]`).join(", ");
      appendDebugLine(`[ACCEPT] Using JSON cut positions: ${cutPosStr}`);
    } else {
      appendDebugLine(`[ACCEPT] WARNING: No JSON cut positions, falling back to trajectory endpoints`);
      // No JSON positions - calculate from trajectories
      Object.entries(routesToUse || {}).forEach(([did, routeData]) => {
        const traj = routeData.trajectory || [];

        if (traj.length < 2) return;

        // Calculate total trajectory length for this drone
        let trajTotalDist = 0;
        for (let i = 1; i < traj.length; i++) {
          const dx = traj[i][0] - traj[i - 1][0];
          const dy = traj[i][1] - traj[i - 1][1];
          trajTotalDist += Math.sqrt(dx * dx + dy * dy);
        }

        if (cutDistance > 0) {
          // Skip drones whose trajectory ended BEFORE cutDistance (already at end airport)
          // These drones don't get a marker at this cut point
          if (trajTotalDist < cutDistance) {
            return;
          }

          // Calculate marker position by splitting trajectory at cutDistance
          const result = split_polyline_at_distance(traj, cutDistance);
          if (result.splitPoint && result.splitPoint.length >= 2) {
            state.pendingCutPositions[did] = result.splitPoint;
          }
        } else {
          // No cutDistance from JSON - use the END of trajectory as the cut position
          // This is the position where the drone will start the next segment
          const lastPoint = traj[traj.length - 1];
          state.pendingCutPositions[did] = [lastPoint[0], lastPoint[1]];
          appendDebugLine(`   D${did}: Using trajectory end as cut position [${lastPoint[0].toFixed(1)}, ${lastPoint[1].toFixed(1)}]`);
        }
      });
    }

    // Get visited targets from the JSON's stored data
    // The JSON knows which targets were visited in each segment
    const jsonVisitedTargets = segmentedImport.getVisitedTargetsForSegment(newSegIdx);
    if (jsonVisitedTargets && jsonVisitedTargets.length > 0) {
      clearVisited();
      seedVisitedFromSegment(jsonVisitedTargets);
      appendDebugLine(`[ACCEPT] Using JSON visited targets for segment ${newSegIdx}: [${jsonVisitedTargets.join(",")}]`);
    } else {
      // Fallback: calculate from trajectory if JSON doesn't have visited targets
      const calculatedVisited = calculateVisitedTargetsFromRoutes(routesToUse, cutDistance);
      clearVisited();
      seedVisitedFromSegment(calculatedVisited);
      appendDebugLine(`[ACCEPT] Calculated visited targets (before cutDist=${cutDistance?.toFixed(1)}): [${calculatedVisited.join(",")}]`);
    }

    if (Object.keys(state.pendingCutPositions).length === 0) {
      appendDebugLine(`[ACCEPT] WARNING: No cut positions calculated! pendingCutPositions is empty.`);
      state.pendingCutPositions = null;
    } else {
      const cutPosStr = Object.entries(state.pendingCutPositions).map(([did, pos]) => `D${did}:[${pos[0].toFixed(1)},${pos[1].toFixed(1)}]`).join(", ");
      appendDebugLine(`[ACCEPT] Calculated cut positions: ${cutPosStr}`);
    }

    appendDebugLine(`[ACCEPT] Advanced to segment ${newSegIdx}, cutDist=${cutDistance?.toFixed(1) || 'null'}, visited=[${state.visitedTargets.join(",")}]`);

    // 6. Update MissionReplay index and save cut positions for the NEXT segment
    missionReplay.setCurrentSegmentIndex(newSegIdx);
    missionState.currentBuildSegmentIndex = newSegIdx;

    // Save the cut positions, visited targets, and lost drones into the next segment
    // This allows us to draw all previous markers and restore correct visited targets on Reset
    const nextSeg = missionReplay.getSegment(newSegIdx);
    if (nextSeg) {
      const savedCutPositions = state.pendingCutPositions ? JSON.parse(JSON.stringify(state.pendingCutPositions)) : nextSeg.cutPositions;
      missionReplay.replaceSegment(newSegIdx, {
        ...nextSeg,
        cutPositions: savedCutPositions,
        visitedTargets: [...state.visitedTargets],  // Save calculated visited targets
        lostDrones: savedLostDrones,  // Drones lost at this checkpoint
      });

      // Verify what was saved
      const savedSeg = missionReplay.getSegment(newSegIdx);
      const savedCutPosStr = savedSeg?.cutPositions
        ? Object.entries(savedSeg.cutPositions).map(([did, pos]) => `D${did}:[${pos[0].toFixed(1)},${pos[1].toFixed(1)}]`).join(", ")
        : "null";
      appendDebugLine(`[ACCEPT] Saved to missionReplay segment ${newSegIdx}: cutPositions=${savedCutPosStr}`);
    }

    // CRITICAL: Clear pendingCutPositions after saving to missionReplay
    // This prevents double-drawing: missionReplay loop draws with correct lostDrones,
    // while pendingCutPositions block would incorrectly use current droneConfigs state
    state.pendingCutPositions = null;

    // More segments to solve
    // PRESERVE existing frozen trajectory and DON'T recalculate it
    // The frozen trajectory is IMMUTABLE once placed
    const frozenRoutes = {};

    // For each drone, keep the existing frozen trajectory (already in state.routes)
    // Do NOT rebuild from MissionReplay - that would use NEW solver trajectories
    // ALSO include drones from previous segments that may now be disabled
    const activeDroneIds = newSegIdx === 1
      ? Object.keys(concatenatedRoutes || {})
      : Object.keys(state.routes || {});

    // Collect drone IDs from previous MissionReplay segments (these may have been disabled)
    const previousDroneIds = new Set();
    for (let i = 0; i < newSegIdx; i++) {
      const seg = missionReplay.getSegment(i);
      if (seg?.solution?.routes) {
        Object.keys(seg.solution.routes).forEach(did => previousDroneIds.add(did));
      }
    }

    // Combine: active drones + previously active drones (now disabled)
    const droneIds = [...new Set([...activeDroneIds, ...previousDroneIds])];

    // BUILD LOST DRONE MAP FIRST - needed for immediate truncation
    // lostDrones in segment N = drones that died at cut CN (end of segment N-1)
    const earlyLostDroneMap = {};
    for (let i = 1; i <= missionReplay.getSegmentCount(); i++) {
      const seg = missionReplay.getSegment(i);
      if (seg?.lostDrones) {
        seg.lostDrones.forEach(did => {
          earlyLostDroneMap[String(did)] = i;
        });
      }
    }
    // Note: lostDrones are stored in missionReplay segments, not segmentedImport
    // segmentedImport only manages targets/cuts, missionReplay has the actual segment data
    if (Object.keys(earlyLostDroneMap).length > 0) {
      appendDebugLine(`[ACCEPT] Lost drones: ${Object.entries(earlyLostDroneMap).map(([d,s]) => `D${d}@seg${s}`).join(', ')}`);
    }

    droneIds.forEach((droneId) => {
      // Check if this drone is LOST
      const lostAtSeg = earlyLostDroneMap[droneId];
      const isLostDrone = lostAtSeg !== undefined && lostAtSeg <= newSegIdx;

      // KEEP FULL TRAJECTORY AT ACCEPT TIME
      // Truncation now happens at Run Planner time (applyDraftSolutionToUI)
      // EXCEPT: Lost drones need immediate truncation at kill position
      if (newSegIdx > 1 && state.routes[droneId] && state.routes[droneId].trajectory) {
        // state.routes has the MERGED trajectory (frozen + new segment)
        let mergedTraj = state.routes[droneId].trajectory || [];
        if (mergedTraj.length > 0) {
          // If drone is lost, truncate at kill position
          if (isLostDrone) {
            const killSeg = missionReplay.getSegment(lostAtSeg);
            // cutPositions are stored in the segment (V2: cutPosition, legacy: cutPositions)
            const killSegCutPos = killSeg?.cutPosition || killSeg?.cutPositions;
            const killPos = killSegCutPos?.[droneId] || segmentedImport.getCutPositionForSegment(lostAtSeg)?.[droneId];
            if (killPos) {
              mergedTraj = truncateTrajectoryAtPosition([...mergedTraj], killPos);
              appendDebugLine(`[ACCEPT] D${droneId}: LOST drone - truncated at kill pos [${killPos[0].toFixed(1)},${killPos[1].toFixed(1)}], ${state.routes[droneId].trajectory.length} → ${mergedTraj.length} pts`);
            }
          }
          frozenRoutes[droneId] = {
            ...JSON.parse(JSON.stringify(state.routes[droneId])),
            trajectory: mergedTraj,
          };
          if (!isLostDrone) {
            appendDebugLine(`[ACCEPT] D${droneId}: Storing FULL merged trajectory (length ${mergedTraj.length}), truncation deferred to Run Planner`);
          }
        }
      } else if (newSegIdx === 1 && concatenatedRoutes && concatenatedRoutes[droneId]) {
        // First Accept
        let fullTraj = concatenatedRoutes[droneId].trajectory || [];

        // Get the full route data from the last segment for this drone
        let lastRouteData = null;
        for (let i = newSegIdx - 1; i >= 0; i--) {
          const seg = missionReplay.getSegment(i);
          if (seg?.solution?.routes?.[droneId]) {
            lastRouteData = seg.solution.routes[droneId];
            break;
          }
        }

        if (fullTraj.length > 0) {
          // If drone is lost, truncate at kill position
          if (isLostDrone) {
            const killSeg = missionReplay.getSegment(lostAtSeg);
            // cutPositions are stored in the segment (V2: cutPosition, legacy: cutPositions)
            const killSegCutPos = killSeg?.cutPosition || killSeg?.cutPositions;
            const killPos = killSegCutPos?.[droneId] || segmentedImport.getCutPositionForSegment(lostAtSeg)?.[droneId];
            if (killPos) {
              fullTraj = truncateTrajectoryAtPosition([...fullTraj], killPos);
              appendDebugLine(`[ACCEPT] D${droneId}: LOST drone - truncated at kill pos [${killPos[0].toFixed(1)},${killPos[1].toFixed(1)}], ${concatenatedRoutes[droneId].trajectory.length} → ${fullTraj.length} pts`);
            }
          }
          frozenRoutes[droneId] = {
            ...(lastRouteData || {}),
            trajectory: fullTraj,
          };
          if (!isLostDrone) {
            appendDebugLine(`[ACCEPT] D${droneId}: Storing FULL trajectory (length ${fullTraj.length}), truncation deferred to Run Planner`);
          }
        }
      }
    });

    // UNCONDITIONAL FINAL PASS: Ensure ALL trajectories from MissionReplay are preserved
    // This catches any drone that wasn't handled above (disabled, edge cases, etc.)
    //
    // BUG FIX: Previously, for each segment i, we only built trajectory up to segment i.
    // This caused dead drone trajectories to disappear because their cumulative trajectory
    // wasn't being built correctly. Now we collect ALL unique droneIds from ALL segments
    // and build each drone's cumulative trajectory from ALL segments where it appears.

    const allDroneIdsInReplay = new Set();
    for (let i = 0; i < newSegIdx; i++) {
      const seg = missionReplay.getSegment(i);
      if (seg?.solution?.routes) {
        Object.keys(seg.solution.routes).forEach(did => allDroneIdsInReplay.add(did));
      }
    }

    // Build map of which drones were lost at which segment
    // lostDrones in segment N = drones that died at cut CN (end of segment N-1)
    const droneLostAtSegment = {};
    for (let i = 1; i < missionReplay.getSegmentCount(); i++) {
      const seg = missionReplay.getSegment(i);
      if (seg?.lostDrones) {
        seg.lostDrones.forEach(did => {
          droneLostAtSegment[String(did)] = i;
        });
      }
    }

    allDroneIdsInReplay.forEach(droneId => {
      // If this drone already has a frozen route, skip
      if (frozenRoutes[droneId]?.trajectory?.length > 0) return;

      // Check if this drone was lost
      const lostAtSeg = droneLostAtSegment[droneId];

      // Build cumulative trajectory from ALL segments where this drone appears
      // But stop at the kill position if drone was lost
      const cumulativeTraj = [];
      let lastRouteData = null;

      for (let j = 0; j < newSegIdx; j++) {
        // Skip if drone was already lost before this segment
        if (lostAtSeg !== undefined && lostAtSeg <= j) {
          break;
        }

        const s = missionReplay.getSegment(j);
        const routeData = s?.solution?.routes?.[droneId];
        let t = routeData?.trajectory || [];

        if (t.length === 0) continue;

        // Check if drone is killed at the END of this segment (lost at j+1)
        const droneKilledHere = (lostAtSeg === j + 1);
        if (droneKilledHere) {
          // Truncate at kill position
          const nextSeg = missionReplay.getSegment(j + 1);
          const nextSegCutPos = nextSeg?.cutPosition || nextSeg?.cutPositions;
          const killPos = nextSegCutPos?.[droneId];
          if (killPos) {
            t = truncateTrajectoryAtPosition([...t], killPos);
            appendDebugLine(`[ACCEPT] D${droneId}: LOST - truncated at kill pos [${killPos[0].toFixed(1)},${killPos[1].toFixed(1)}]`);
          }
        }

        lastRouteData = routeData;  // Keep track of the last route data for this drone
        if (cumulativeTraj.length === 0) {
          cumulativeTraj.push(...t);
        } else {
          // Skip first point if duplicate
          const lastPt = cumulativeTraj[cumulativeTraj.length - 1];
          const firstPt = t[0];
          const startIdx = (lastPt && firstPt &&
            Math.abs(lastPt[0] - firstPt[0]) < 0.001 &&
            Math.abs(lastPt[1] - firstPt[1]) < 0.001) ? 1 : 0;
          cumulativeTraj.push(...t.slice(startIdx));
        }

        // If drone was killed here, don't continue to next segment
        if (droneKilledHere) break;
      }

      if (cumulativeTraj.length > 0 && lastRouteData) {
        frozenRoutes[droneId] = {
          ...lastRouteData,
          trajectory: cumulativeTraj,
        };
        if (lostAtSeg !== undefined) {
          appendDebugLine(`[ACCEPT] D${droneId}: LOST drone frozen trajectory preserved (${cumulativeTraj.length} pts)`);
        }
      }
    });

    // IMPORTANT: Update ALL previous MissionReplay segments with their PER-SEGMENT trajectories
    // frozenRoutes contains the cumulative frozen trajectory (all segments concatenated)
    // We need to split it back into individual segment trajectories for MissionReplay

    // Get the previous frozen routes (before this Accept)
    const prevFrozenTraj = {};
    if (newSegIdx > 1) {
      // For segments after the first, we have previous frozen trajectory in state.routes (before updating)
      // But we just overwrote state.routes with frozenRoutes, so we need to rebuild it from Mission Replay
      for (let i = 0; i < newSegIdx - 1; i++) {
        const seg = missionReplay.getSegment(i);
        if (seg && seg.solution && seg.solution.routes) {
          Object.entries(seg.solution.routes).forEach(([did, routeData]) => {
            const traj = routeData.trajectory || [];
            if (traj.length === 0) return;

            if (!prevFrozenTraj[did]) {
              prevFrozenTraj[did] = [...traj];
            } else {
              // Concatenate
              const existing = prevFrozenTraj[did];
              const lastPoint = existing[existing.length - 1];
              const firstPoint = traj[0];
              const isDuplicate = Math.abs(lastPoint[0] - firstPoint[0]) < 0.001 &&
                                 Math.abs(lastPoint[1] - firstPoint[1]) < 0.001;
              if (isDuplicate) {
                prevFrozenTraj[did] = existing.concat(traj.slice(1));
              } else {
                prevFrozenTraj[did] = existing.concat(traj);
              }
            }
          });
        }
      }
    }

    // Now update the CURRENT segment (currentSegIdx) with its portion
    const currentSeg = missionReplay.getSegment(currentSegIdx);
    if (currentSeg && currentSeg.solution && currentSeg.solution.routes) {
      const segmentSolution = {
        routes: {},
        sequences: currentSeg.solution.sequences || {},
      };

      Object.entries(currentSeg.solution.routes).forEach(([did, routeData]) => {
        // Extract this segment's trajectory from frozenRoutes
        const cumulativeTraj = frozenRoutes[did] ? frozenRoutes[did].trajectory : [];
        const prevTraj = prevFrozenTraj[did] || [];

        // This segment's trajectory = cumulative - previous
        const segmentTraj = cumulativeTraj.slice(prevTraj.length);

        segmentSolution.routes[did] = {
          ...routeData,
          trajectory: segmentTraj,
        };

      });

      missionReplay.replaceSegment(currentSegIdx, {
        ...currentSeg,
        solution: segmentSolution,
      });
    }

    state.routes = frozenRoutes;
    // Debug: Log trajectory lengths and route sequences for all frozen routes
    appendDebugLine(`[ACCEPT] state.routes set with ${Object.keys(frozenRoutes).length} drones:`);
    Object.entries(frozenRoutes).forEach(([did, routeData]) => {
      const trajLen = routeData?.trajectory?.length || 0;
      const routeSeq = routeData?.route || [];
      const trajFirst = routeData?.trajectory?.[0];
      const trajLast = routeData?.trajectory?.[trajLen - 1];
      appendDebugLine(`  D${did}: route=[${routeSeq.join(',')}], traj=${trajLen}pts (${trajFirst ? `${trajFirst[0].toFixed(1)},${trajFirst[1].toFixed(1)}` : '?'} → ${trajLast ? `${trajLast[0].toFixed(1)},${trajLast[1].toFixed(1)}` : '?'})`);
    });

    // Make frozen trajectories visible
    Object.keys(frozenRoutes).forEach(did => {
      state.trajectoryVisible[did] = true;
    });

    // Keep the visited targets we set earlier (from JSON or calculated)
    // Don't recalculate - the JSON's visitedTargets is the source of truth
    appendDebugLine(`[ACCEPT] Final visited targets: [${state.visitedTargets.join(",")}]`);

    // Set up env for drawing - targets up to current segment (progressive reveal)
    const displayEnv = segmentedImport.getEnvForDisplay();
    state.env = displayEnv;

    // SIMPLE: Get drone_configs directly from the JSON for this segment
    // The JSON stores per-segment drone_configs which specify exactly which drones are enabled
    const segmentConfigs = segmentedImport.getDroneConfigsForSegment(newSegIdx);

    let derivedConfigs;
    if (segmentConfigs && Object.keys(segmentConfigs).length > 0) {
      // MERGE JSON configs with current UI configs, preserving user modifications
      derivedConfigs = JSON.parse(JSON.stringify(segmentConfigs));

      // Override with current UI values where they exist
      // UI values (like fuel_budget) should ALWAYS trump JSON values
      Object.keys(derivedConfigs).forEach(did => {
        const currentConfig = state.droneConfigs?.[did];
        if (currentConfig) {
          // Preserve UI-modified values: fuel_budget, target_access, etc.
          if (currentConfig.fuel_budget !== undefined) {
            derivedConfigs[did].fuel_budget = currentConfig.fuel_budget;
          }
          if (currentConfig.target_access) {
            derivedConfigs[did].target_access = JSON.parse(JSON.stringify(currentConfig.target_access));
          }
        }
      });



      // Update start_airport for drones that have cutPositions
      const thisSegCutPositions = segmentedImport.getCutPositionForSegment(newSegIdx);
      if (thisSegCutPositions) {
        Object.keys(thisSegCutPositions).forEach(did => {
          if (derivedConfigs[did]) {
            derivedConfigs[did].start_airport = `D${did}_START`;
          }
        });
      }

    } else {
      // Fallback to displayEnv drone_configs
      derivedConfigs = JSON.parse(JSON.stringify(displayEnv.drone_configs || {}));
    }

    state.droneConfigs = derivedConfigs;
    state.env.drone_configs = state.droneConfigs;
    missionState.acceptedEnv = JSON.parse(JSON.stringify(displayEnv));

    initDroneConfigsFromEnv();
    updateSamWrappingClientSide();

    const unfrozen = segmentedImport.getUnfrozenTargets();
    appendDebugLine(`➡️ Segment ${newSegIdx}: ${segmentedImport.getAllTargets().length} total, ${unfrozen.length} to visit, frozen: ${state.visitedTargets.join(",") || "none"}`);

    setMissionMode(MissionMode.IDLE, `segment ${newSegIdx} ready to solve`);
    drawEnvironment();
  } else {
    // All segments complete - ready to animate
    // CRITICAL FIX: Before building combined routes, we need to extract just the final
    // segment's portion from state.routes (which has the full merged trajectory).
    // Otherwise buildCombinedRoutesFromSegments() will concatenate the FULL trajectory
    // from segment 3 onto segments 0-2, causing phantom overlay trajectories.

    // Build previous frozen trajectory from segments 0 to currentSegIdx-1
    const prevFrozenTraj = {};
    for (let i = 0; i < currentSegIdx; i++) {
      const seg = missionReplay.getSegment(i);
      if (seg && seg.solution && seg.solution.routes) {
        Object.entries(seg.solution.routes).forEach(([did, routeData]) => {
          const traj = routeData.trajectory || [];
          if (traj.length === 0) return;

          if (!prevFrozenTraj[did]) {
            prevFrozenTraj[did] = [...traj];
          } else {
            // Concatenate, skipping duplicate junction point
            const existing = prevFrozenTraj[did];
            const lastPoint = existing[existing.length - 1];
            const firstPoint = traj[0];
            const isDuplicate = Math.abs(lastPoint[0] - firstPoint[0]) < 0.001 &&
                               Math.abs(lastPoint[1] - firstPoint[1]) < 0.001;
            if (isDuplicate) {
              prevFrozenTraj[did] = existing.concat(traj.slice(1));
            } else {
              prevFrozenTraj[did] = existing.concat(traj);
            }
          }
        });
      }
    }

    // Now update the FINAL segment (currentSegIdx) with just its portion
    const finalSeg = missionReplay.getSegment(currentSegIdx);
    if (finalSeg && finalSeg.solution && finalSeg.solution.routes) {
      const segmentSolution = {
        routes: {},
        sequences: finalSeg.solution.sequences || {},
      };

      Object.entries(finalSeg.solution.routes).forEach(([did, routeData]) => {
        // state.routes has the full merged trajectory
        const fullTraj = state.routes[did]?.trajectory || routeData.trajectory || [];
        const prevTraj = prevFrozenTraj[did] || [];

        // This segment's trajectory = full - previous
        const segmentTraj = fullTraj.slice(prevTraj.length);

        segmentSolution.routes[did] = {
          ...routeData,
          trajectory: segmentTraj,
        };

      });

      missionReplay.replaceSegment(currentSegIdx, {
        ...finalSeg,
        solution: segmentSolution,
      });
    }

    // After all segments accepted: Show the FULL combined trajectory from all segments
    // This is the complete frozen mission trajectory
    appendDebugLine(`[FINAL ACCEPT] missionReplay has ${missionReplay.getSegmentCount()} segments`);
    const combinedRoutes = buildCombinedRoutesFromSegments();
    appendDebugLine(`[FINAL ACCEPT] buildCombinedRoutesFromSegments returned ${combinedRoutes ? Object.keys(combinedRoutes).length : 0} drones`);

    // Use the concatenated/truncated trajectories directly
    // buildCombinedRoutesFromSegments() already truncates each segment at the correct cut point
    if (combinedRoutes && Object.keys(combinedRoutes).length > 0) {
      state.routes = JSON.parse(JSON.stringify(combinedRoutes));
      Object.keys(state.routes).forEach(did => {
        state.trajectoryVisible[did] = true;
      });
      // Log trajectory details and extract sequence by detecting waypoints (lat/lng)
      const fullEnv = segmentedImport.getFullEnv() || state.env;
      const waypointMap = {};
      // Use higher precision for lat/lng (6 decimal places)
      (fullEnv.airports || []).forEach(a => { waypointMap[`${a.lat.toFixed(6)},${a.lng.toFixed(6)}`] = a.id; });
      (fullEnv.targets || []).forEach(t => { waypointMap[`${t.lat.toFixed(6)},${t.lng.toFixed(6)}`] = t.id; });
      // Add checkpoints (cutPositions are now lat/lng)
      for (let segIdx = 1; segIdx < missionReplay.getSegmentCount(); segIdx++) {
        const seg = missionReplay.getSegment(segIdx);
        const segCutPos = seg?.cutPosition || seg?.cutPositions;
        if (segCutPos) {
          Object.entries(segCutPos).forEach(([droneId, pos]) => {
            if (pos && pos.length >= 2) {
              waypointMap[`${pos[0].toFixed(6)},${pos[1].toFixed(6)}`] = `C${segIdx}-${droneId}`;
            }
          });
        }
      }

      Object.entries(state.routes).forEach(([did, rd]) => {
        const traj = rd.trajectory || [];

        // Extract sequence by checking each trajectory point against waypoints (lat/lng)
        const sequence = [];
        const visited = new Set();
        for (const pt of traj) {
          const key = `${pt[0].toFixed(6)},${pt[1].toFixed(6)}`;
          const wpId = waypointMap[key];
          if (wpId && !visited.has(wpId)) {
            // Only include checkpoints for THIS drone
            if (wpId.match(/^C\d+-\d+$/)) {
              const cpDroneId = wpId.split('-')[1];
              if (cpDroneId !== did) continue;
            }
            sequence.push(wpId);
            visited.add(wpId);
          }
        }

        appendDebugLine(`  D${did}: ${traj.length} pts, SEQUENCE = ${sequence.join(' → ')}`);
      });
      appendDebugLine(`📺 Showing combined trajectory`);
    } else {
      // Fallback to segment 0 if combined routes fail
      const seg0Solution = missionState.seg0FullSolution || missionReplay.getSegment(0)?.solution;
      if (seg0Solution?.routes) {
        state.routes = JSON.parse(JSON.stringify(seg0Solution.routes));
        Object.keys(state.routes).forEach(did => {
          state.trajectoryVisible[did] = true;
        });
        appendDebugLine(`📺 Fallback: Showing segment 0 trajectory (${Object.entries(state.routes).map(([d,r]) => `D${d}:${r.trajectory?.length||0}`).join(', ')})`);
      } else {
        state.routes = {};
      }
    }

    // After final Accept, ALL targets are visited because the full mission was completed
    // The drone flew from airport through all checkpoints back to airport
    const allTargetsForAccept = state.env?.targets || segmentedImport.getAllTargets() || [];
    markAllVisited(allTargetsForAccept);
    appendDebugLine(`[FINAL ACCEPT] All targets marked as visited: [${allTargetsForAccept.map(t => t.id).join(",")}]`);
    // Clear any pending cut markers
    state.pendingCutPositions = null;

    // Set env to full environment with all targets
    const fullEnv = segmentedImport.getFullEnv();
    state.env = fullEnv || segmentedImport.getEnvForDisplay();
    // Use final segment's drone_configs (reflects final state of drones)
    const finalSegConfigs = missionReplay.getSegment(currentSegIdx)?.drone_configs || state.env.drone_configs || {};
    state.droneConfigs = JSON.parse(JSON.stringify(finalSegConfigs));
    state.env.drone_configs = state.droneConfigs;
    missionState.acceptedEnv = JSON.parse(JSON.stringify(state.env));

    // Reset segment manager to start (so animation starts from segment 0)
    segmentedImport._currentSegmentIndex = 0;
    missionReplay.resetToStart();

    setMissionMode(MissionMode.READY_TO_ANIMATE, "all segments accepted; ready to replay");
    appendDebugLine("✅ All segments accepted. Ready to Animate full mission.");
    drawEnvironment();
  }
}

/**
 * Discard draft solution - go back to previous state
 */
function discardDraftSolution() {
  // DEBUG: Log who called discard and from where
  const perms = getUiPermissions();
  if (!perms.canDiscardDraft) {
    appendDebugLine("Cannot discard draft in current state");
    return;
  }

  // REJECT an agent continuation: remove the proposed solution and RE-OPEN the editor
  // with the operator's staged edits intact, so they can adjust and continue again. No
  // solve, no change to the committed mission state. prepareAgentContinueCut stashed the
  // pre-cut staged env in agentRejectRestore before the cut engine cleared it.
  const _d = missionState.draftSolution;
  if (_d && _d._agentContinuation) {
    const _r = missionState.agentRejectRestore || {};
    missionState.draftSolution = null;
    missionState.agentRejectRestore = null;
    state.pendingLostDrones = [];
    // Restore the staged edits (if the operator was live-editing) and re-open the editor.
    missionState.stagedEnv = _r.stagedEnv ? JSON.parse(JSON.stringify(_r.stagedEnv)) : JSON.parse(JSON.stringify(state.env || {}));
    missionState.stagedDroneConfigs = _r.stagedDroneConfigs ? JSON.parse(JSON.stringify(_r.stagedDroneConfigs)) : JSON.parse(JSON.stringify(state.droneConfigs || {}));
    missionState.wasAnimating = true;   // we came from a paused/live-edit
    state.editMode = true;
    setMissionMode(MissionMode.EDITING_ENV, "agent continuation rejected — editor re-opened");
    if (typeof updateEnvPanelLocked === "function") updateEnvPanelLocked();
    if (typeof updateCanvasPointerEvents === "function") updateCanvasPointerEvents();
    updateAnimationButtonStates();
    if (typeof updateAgentUiState === "function") updateAgentUiState(getUiPermissions());
    requestDraw();
    appendDebugLine("↩️ Rejected the continuation — editor re-opened with your edits. Adjust and say \"continue\", or turn the editor off to solve manually.");
    return;
  }

  // CRITICAL: If discarding a checkpoint replan, restore the checkpoint state
  // so the next solve will still respect frozen trajectories
  const draft = missionState.draftSolution;
  if (draft && draft.isCheckpointReplan) {
    appendDebugLine("🔄 Restoring checkpoint state from discarded draft...");
    restoreCheckpointFromDraft(draft);
  }

  // Clear the draft solution
  missionState.draftSolution = null;

  // Clear pending lost drones (from rejected solve attempts)
  state.pendingLostDrones = [];

  // BUGFIX: Sync drone configs from UI to state after discard
  // This ensures that any edits made in the UI after the last solve are preserved
  // Without this, edits might be lost because state.droneConfigs could be stale
  state.droneConfigs = readDroneConfigsFromUI();
  appendDebugLine("✓ Synced drone configs from UI after discard");

  // Check if we restored checkpoint state (determines what mode to go to)
  const restoredCheckpoint = draft && draft.isCheckpointReplan && state.checkpoint?.active;

  // Check if we have a committed solution to restore from MissionReplay
  const currentSegment = missionReplay.getCurrentSegment();
  const hasCommittedPlan = currentSegment && currentSegment.solution;

  if (hasCommittedPlan) {
    // Restore the committed segment's routes
    const solution = currentSegment.solution;
    if (solution.routes) {
      // Solution already has routes in the new format
      state.routes = JSON.parse(JSON.stringify(solution.routes));
    } else if (solution.drone_routes) {
      // Legacy format
      state.routes = {};
      solution.drone_routes.forEach(r => {
        state.routes[String(r.drone_id)] = {
          route: r.route,
          trajectory: r.trajectory,
          distance: r.distance,
          fuel_budget: r.fuel_budget,
          points: r.points || 0,
        };
      });
    }
    // If checkpoint state was restored, go to CHECKPOINT mode so user can solve again
    // Otherwise go back to ready to animate
    if (restoredCheckpoint) {
      setMissionMode(MissionMode.CHECKPOINT, "draft discarded, checkpoint state restored - ready to solve again");
    } else {
      setMissionMode(MissionMode.READY_TO_ANIMATE, "draft discarded, restored committed solution");
    }
  } else {
    // Fallback to old system
    const oldSegment = missionState.committedSegments[missionState.committedSegments.length - 1];
    if (oldSegment && oldSegment.solution && oldSegment.solution.drone_routes) {
      state.routes = {};
      oldSegment.solution.drone_routes.forEach(r => {
        state.routes[String(r.drone_id)] = {
          route: r.route,
          trajectory: r.trajectory,
          distance: r.distance,
          fuel_budget: r.fuel_budget,
          points: r.points || 0,
        };
      });
      if (restoredCheckpoint) {
        setMissionMode(MissionMode.CHECKPOINT, "draft discarded, checkpoint state restored - ready to solve again");
      } else {
        setMissionMode(MissionMode.READY_TO_ANIMATE, "draft discarded, restored committed solution");
      }
    } else {
      // No committed solution - clear routes and go to IDLE
      state.routes = {};
      if (restoredCheckpoint) {
        setMissionMode(MissionMode.CHECKPOINT, "draft discarded, checkpoint state restored - ready to solve again");
      } else {
        setMissionMode(MissionMode.IDLE, "draft discarded");
      }
    }
  }

  appendDebugLine(restoredCheckpoint
    ? "🔄 Draft discarded - checkpoint state restored, ready to solve again"
    : "❌ Draft solution discarded");
  drawEnvironment();
}

/**
 * Reset mission - return to initial state with first solution visible
 * Goes to READY_TO_ANIMATE so user can replay from the beginning
 */
function invalidateMissionAsyncWork() {
  state.lifecycleGeneration = (state.lifecycleGeneration || 0) + 1;
  if (state.solveAbortController) state.solveAbortController.abort();
  state.solveAbortController = null;
  if (typeof window !== "undefined") window.weatherCheckpoints?.reset();
}

function resetMission() {
  const perms = getUiPermissions();
  if (!perms.canReset) {
    appendDebugLine("Cannot reset in current state");
    return;
  }

  // Must have segments to reset to (check both old and new system)
  // Check BEFORE clearing anything
  if (missionReplay.getSegmentCount() === 0 && missionState.committedSegments.length === 0) {
    appendDebugLine("No committed segments to reset to");
    return;
  }

  // RESET is destructive: it erases ALL cuts/segments and returns to the original
  // mission. Warn the user (so they can export first) — but ONLY when there is cut
  // work to lose (>1 segment). A reset of an uncut mission is harmless, no nag.
  const hasCutWork =
    missionReplay.getSegmentCount() > 1 ||
    (missionState.committedSegments && missionState.committedSegments.length > 1);
  if (hasCutWork) {
    const ok = confirm(
      "RESET clears all cuts and returns to the original mission.\n\n" +
      "This cannot be undone — export your mission first if you want to keep the cut work.\n\n" +
      "Reset anyway?"
    );
    if (!ok) {
      appendDebugLine("Reset cancelled by user");
      return;
    }
  }

  invalidateMissionAsyncWork();

  // --- HARD RESET: stop playback + clear segmented replay ---
  // Note: We do NOT clear missionReplay here - we need it to restore segment 0
  try {
    // Stop animation if running
    if (state.animationFrameId) {
      cancelAnimationFrame(state.animationFrameId);
      state.animationFrameId = null;
    }
    if (state.animationTimerId) {
      clearInterval(state.animationTimerId);
      state.animationTimerId = null;
    }

    // Clear segment-related state (but NOT missionReplay - we need it for reset)
    missionState.currentSegmentIndex = 0;
    missionState.currentBuildSegmentIndex = 0;

    // Clear cut/freeze bookkeeping
    state.pendingCutPositions = null;
    state.pendingCutDistance = null;
    state.pendingLostDrones = [];
    clearVisited();

    // Clear routes (will be restored from segment 0)
    state.routes = {};
    state.sequences = {};
    state.timeSync = null;
  } catch (e) {
  }

  // =====================================================
  // 1. Stop animation
  // =====================================================
  if (state.animation.animationId) {
    cancelAnimationFrame(state.animation.animationId);
    state.animation.animationId = null;
  }
  state.animation.active = false;
  state.animation.drones = {};
  state.animation.elapsedMissionSeconds = 0;
  state.animation.missionComplete = false;  // RESET handles its own full replay
  state.animation.continuousReplay = false;
  state.animation.frameClock = null;
  state.animation.missionDistance = 0;
  state.animation.segmentStartOffset = 0;
  state.animation.checkpointPause = null;
  state.legFuel = {};  // fresh mission: clear durable per-leg fuel store
  state.legFuelHistory = {};  // and the finalized per-leg history
  // §11.10: clear any transient replan start_fuel so a fresh mission's leg-1 uses
  // the full tank (fuel_budget), not a stale reduced value from a prior cut.
  Object.values(state.droneConfigs || {}).forEach((c) => { if (c) delete c.start_fuel; });

  // =====================================================
  // 2. Clear ALL state first (before restoring solution)
  // =====================================================
  state.checkpoint = { active: false, pct: 0.5, segments: {} };
  state.pendingCutDistance = null;
  state.pendingCutPositions = null;
  state.pendingCheckpoints = null;
  state.checkpointSegmentCounter = 0;  // Reset checkpoint counter on full reset
  state._cutCounterClaimed = false;    // next cut claims a fresh number
  state.pendingLostDrones = [];
  state.missionHistory = [];
  clearVisited();
  state.currentCutSegment = 1;
  state.previouslyAssignedTargets = [];
  state.targetSegmentMap = {};
  state.trajectoryVisible = {};  // No trajectories shown until Display is clicked
  state.timeSync = null;
  updateTimeSyncUi();

  missionState.currentSegmentIndex = 0;
  missionState.committedSegments = missionState.committedSegments.slice(0, 1);
  missionState.draftEnv = null;
  missionState.draftSolution = null;
  missionState.pauseContext = null;
  missionState.checkpointSource = null;

  // =====================================================
  // 3. Reset MissionReplay to start
  // =====================================================
  missionReplay.resetToStart();
  // §8.8: clear frozen (past) history so a Reset (t=0) shows no stale flown paths.
  if (missionReplay.clearFrozenTrajectories) missionReplay.clearFrozenTrajectories();

  // =====================================================
  // NEW PATH: For multi-segment missions (via import OR cut workflow)
  // =====================================================
  const resetSegCount = missionReplay.getSegmentCount();
  // Use this path if we have multiple segments (whether from import or from cut workflow)
  if (resetSegCount > 1 || segmentedImport.isActive()) {
    // At Reset, load segment 0's FULL trajectory (airport → airport)
    // This is the same as creation workflow - animation will switch segments at checkpoints
    // For import: use segmentedImport. For creation: use seg0FullSolution.
    let seg0Solution = null;
    if (segmentedImport.isActive()) {
      seg0Solution = segmentedImport.getSolutionForSegment(0);
    }
    if (!seg0Solution?.routes || Object.keys(seg0Solution.routes).length === 0) {
      seg0Solution = missionState.seg0FullSolution;
    }
    if (!seg0Solution?.routes || Object.keys(seg0Solution.routes).length === 0) {
      const seg0 = missionReplay.getSegment(0);
      seg0Solution = seg0?.solution;
    }

    if (seg0Solution?.routes && Object.keys(seg0Solution.routes).length > 0) {
      state.routes = JSON.parse(JSON.stringify(seg0Solution.routes));
      const trajInfo = Object.entries(state.routes).map(([did, rd]) => `D${did}:${rd.trajectory?.length || 0}`).join(', ');
      appendDebugLine(`🔄 Reset: Loaded seg-0 FULL trajectory [${trajInfo}]`);
    } else {
      state.routes = {};
      appendDebugLine(`🔄 Reset: No seg-0 routes found`);
    }
    Object.keys(state.routes).forEach(did => {
      state.trajectoryVisible[did] = true;
    });

    // Store drone loss info for animation to use when extending trajectories
    // Check missionReplay, segmentedImport, AND segmentManager for lostDrones
    const droneLostAtSegment = {};
    const segCount = missionReplay.getSegmentCount();
    // Also check segmentManager if available
    const smActive = typeof segmentManager !== 'undefined' && segmentManager?.isActive?.();
    const smSegCount = smActive ? segmentManager.getSegmentCount?.() : 0;
    const maxSegCount = Math.max(segCount, smSegCount || 0);
    for (let segIdx = 1; segIdx < maxSegCount; segIdx++) {
      // Check missionReplay first
      let lostDrones = missionReplay.getSegment(segIdx)?.lostDrones || [];
      // Fallback to segmentedImport
      if (lostDrones.length === 0 && segmentedImport.isActive()) {
        const importSeg = segmentedImport.getSegment?.(segIdx);
        lostDrones = importSeg?.lostDrones || [];
      }
      // Fallback to segmentManager
      if (lostDrones.length === 0 && smActive) {
        const smSeg = segmentManager.getSegment?.(segIdx);
        lostDrones = smSeg?.lostDrones || [];
        if (lostDrones.length > 0) {
        }
      }
      if (lostDrones.length > 0) {
        lostDrones.forEach(did => {
          if (droneLostAtSegment[did] === undefined) {
            droneLostAtSegment[did] = segIdx;
          }
        });
      }
    }
    // Store for animation segment switch code to use
    missionState.droneLostAtSegment = droneLostAtSegment;

    // NOTE: Trajectory truncation for lost drones happens during ANIMATION, not at Reset
    // At Reset, we show seg-0 trajectories. Animation will extend them and truncate lost drones.
    // On Reset: Clear visited targets - checkmarks will appear during animation as targets are passed
    clearVisited();
    // Clear cut positions - white diamonds appear during animation when checkpoint is reached
    // NOT after Reset (user wants to see the mission from post-Accept state)
    state.pendingCutPositions = null;

    // Reset segmented import manager to segment 0
    segmentedImport._currentSegmentIndex = 0;

    // RESET = replay the WHOLE original mission as ONE continuous flight. state.routes
    // already holds seg-0's FULL airport→end solution (loaded above), so DROP the cut
    // segments: with no next segment, animate's checkSegmentBoundary finds nothing to
    // switch to and the drone flies straight through the old cut points instead of
    // stopping at the first one. (User exports before RESET if they want to keep cuts.)
    if (missionReplay.truncateAfter) missionReplay.truncateAfter(0);
    missionReplay.setCurrentSegmentIndex(0);

    // Set env to display targets - collect ALL targets from ALL segments
    // This ensures targets added in later segments are available for animation
    const seg0 = missionReplay.getSegment(0);
    if (seg0?.env) {
      state.env = JSON.parse(JSON.stringify(seg0.env));

      // Collect all targets from all segments
      const allTargetsMap = new Map();
      for (let i = 0; i < missionReplay.getSegmentCount(); i++) {
        const seg = missionReplay.getSegment(i);
        if (seg?.env?.targets) {
          seg.env.targets.forEach(t => {
            if (!allTargetsMap.has(t.id)) {
              allTargetsMap.set(t.id, t);
            }
          });
        }
      }
      const allTargets = Array.from(allTargetsMap.values());

      // Store all targets in initialEnvSnapshot for segment switch to use during animation
      state.initialEnvSnapshot = JSON.parse(JSON.stringify(state.env));
      state.initialEnvSnapshot.targets = allTargets;

      // For display at Reset, show ONLY segment 0's targets (post-Accept state)
      // New targets from later segments appear during animation when checkpoint is reached
      // state.initialEnvSnapshot has ALL targets for the animation to use
      const seg0Targets = seg0.env?.targets || [];
      state.env.targets = JSON.parse(JSON.stringify(seg0Targets));
    }
    // Load drone_configs from segment 0 (the source of truth)
    if (seg0 && seg0.drone_configs) {
      state.droneConfigs = JSON.parse(JSON.stringify(seg0.drone_configs));
      if (state.env) state.env.drone_configs = state.droneConfigs;
      refreshDroneConfigUI();  // Update the UI table
    } else if (state.env) {
      state.droneConfigs = JSON.parse(JSON.stringify(state.env.drone_configs || {}));
      state.env.drone_configs = state.droneConfigs;
    }
    missionState.acceptedEnv = JSON.parse(JSON.stringify(state.env));

    setMissionMode(MissionMode.READY_TO_ANIMATE, "segmented mission reset - ready to animate from segment 0");
    if (typeof window !== "undefined") window.weatherPreview?.resetToStart?.();
    appendDebugLine("🔄 Segmented mission reset - ready to replay from beginning");
    updateAirportDropdowns();
    updateAnimationButtonStates();
    drawEnvironment();
    return;
  }

  // For segmentInfo workflow (imported segmented missions), clear ALL solutions
  // This ensures Reset returns to import state - no solutions, no markers
  const isSegmentInfoWorkflow = state.importedSegmentCuts && state.importedSegmentCuts.length > 0;
  if (isSegmentInfoWorkflow) {
    missionReplay.clearAllSolutions();
  }

  // =====================================================
  // 4. Restore environment and initialEnvSnapshot
  // For replay, we need ALL targets visible (from initialEnvSnapshot)
  // =====================================================
  const firstSegment = missionReplay.getSegment(0);

  // First, ensure we have initialEnvSnapshot with ALL targets
  // Collect all unique targets from all segments
  const allTargetsMap = new Map();
  for (let i = 0; i < missionReplay.getSegmentCount(); i++) {
    const seg = missionReplay.getSegment(i);
    if (seg && seg.env && seg.env.targets) {
      seg.env.targets.forEach(t => {
        if (!allTargetsMap.has(t.id)) {
          allTargetsMap.set(t.id, t);
        }
      });
    }
  }
  const allTargetsFromSegments = Array.from(allTargetsMap.values());
  const existingSnapshotTargetCount = state.initialEnvSnapshot?.targets?.length || 0;
  // Only update initialEnvSnapshot if we collected MORE targets than it already has
  // (The original snapshot from first solve should have all targets)
  if (allTargetsFromSegments.length > existingSnapshotTargetCount && firstSegment && firstSegment.env) {
    const fullEnv = JSON.parse(JSON.stringify(firstSegment.env));
    fullEnv.targets = allTargetsFromSegments;
    state.initialEnvSnapshot = fullEnv;
  } else if (existingSnapshotTargetCount > 0) {
  } else if (firstSegment && firstSegment.env) {
    // No existing snapshot and no better source - use segment targets
    const fullEnv = JSON.parse(JSON.stringify(firstSegment.env));
    fullEnv.targets = allTargetsFromSegments;
    state.initialEnvSnapshot = fullEnv;
  }

  // Set state.env from segment 0 - show only segment 0's targets after Reset
  // During animation, targets will appear as the drone enters each segment
  if (firstSegment && firstSegment.env) {
    state.env = JSON.parse(JSON.stringify(firstSegment.env));
    missionState.acceptedEnv = JSON.parse(JSON.stringify(firstSegment.env));

    // Load drone_configs from segment 0 (the source of truth)
    if (firstSegment.drone_configs) {
      state.droneConfigs = JSON.parse(JSON.stringify(firstSegment.drone_configs));
      state.env.drone_configs = state.droneConfigs;
      refreshDroneConfigUI();  // Update the UI table
    }

    // Show ALL targets on Reset — unvisited targets should be visible for re-solving
    if (!isSegmentInfoWorkflow) {
      // Use initialEnvSnapshot targets if available (has all targets including unvisited)
      if (state.initialEnvSnapshot && state.initialEnvSnapshot.targets) {
        state.env.targets = JSON.parse(JSON.stringify(state.initialEnvSnapshot.targets));
        missionState.acceptedEnv.targets = state.env.targets;
      }
    }
  }

  // =====================================================
  // 5. Restore first segment's routes (without drawing)
  // =====================================================
  // For segmentInfo workflow, we've cleared all solutions - start fresh with no routes
  if (isSegmentInfoWorkflow) {
    state.sequences = {};
    state.routes = {};
    state.wrappedPolygons = [];
    state.allocations = {};
    state.allocationStrategy = null;
    state.distanceMatrix = null;
  } else if (firstSegment && firstSegment.solution) {
    const solution = firstSegment.solution;
    // Manually restore routes without calling applyDraftSolutionToUI (which draws)
    state.sequences = JSON.parse(JSON.stringify(solution.sequences || {}));
    state.routes = JSON.parse(JSON.stringify(solution.routes || {}));
    state.wrappedPolygons = JSON.parse(JSON.stringify(solution.wrappedPolygons || []));
    state.allocations = JSON.parse(JSON.stringify(solution.allocations || {}));
    state.allocationStrategy = solution.allocationStrategy || null;
    state.distanceMatrix = solution.distanceMatrix ? JSON.parse(JSON.stringify(solution.distanceMatrix)) : null;
  } else {
    // Fallback to old system
    const oldFirstSegment = missionState.committedSegments[0];
    if (oldFirstSegment && oldFirstSegment.solution) {
      const solution = oldFirstSegment.solution;
      state.sequences = JSON.parse(JSON.stringify(solution.sequences || {}));
      state.routes = JSON.parse(JSON.stringify(solution.routes || {}));
      state.wrappedPolygons = JSON.parse(JSON.stringify(solution.wrappedPolygons || []));
      state.allocations = JSON.parse(JSON.stringify(solution.allocations || {}));
      state.allocationStrategy = solution.allocationStrategy || null;
    }
  }

  // Clear any _fullTrajectory overrides
  Object.values(state.routes).forEach(routeData => {
    if (routeData._fullTrajectory) {
      delete routeData._fullTrajectory;
    }
  });

  // Make trajectories visible for all restored routes (so user can see them before/during animation)
  Object.keys(state.routes).forEach(did => {
    state.trajectoryVisible[did] = true;
  });

  // =====================================================
  // 6. Set mode and redraw
  // =====================================================
  if (isSegmentInfoWorkflow) {
    // For segmentInfo workflow, reset to an editable state (no solution yet).
    // Was MissionMode.EDITABLE — a typo, not in the enum → setMissionMode got
    // undefined and the state machine lost its mode. EDITING_ENV is the real
    // "editable, nothing solved" mode.
    setMissionMode(MissionMode.EDITING_ENV, "segmentInfo workflow reset - solve segment 0");
    appendDebugLine("🔄 Mission reset - ready to solve segment 0");
  } else {
    setMissionMode(MissionMode.READY_TO_ANIMATE, "mission reset to start");
    appendDebugLine("🔄 Mission reset - ready to replay from beginning");
  }
  if (typeof window !== "undefined") window.weatherPreview?.resetToStart?.();
  updateAirportDropdowns();
  updateAnimationButtonStates();
  updateStatsFromRoutes();
  drawEnvironment();
}

// ----------------------------------------------------
// Utility helpers
// ----------------------------------------------------
function appendDebugLine(msg) {
  return debugOutput.prepend(msg);
}

// ----------------------------------------------------
// Mission lifecycle UI (§1a-0 / §0.6)
// Name field + status badge + End Mission, wired to the
// /api/missions endpoints. A mission persists across edits;
// it ends only by the officer pressing End Mission.
// ----------------------------------------------------
function syncMissionUi() {
  const nameInput = $("mc-mission-name");
  const statusEl = $("mc-mission-status");
  const endBtn = $("mc-end-mission");

  if (nameInput && document.activeElement !== nameInput) {
    nameInput.value = state.missionName || "";
  }
  if (statusEl) {
    if (state.missionStatus === "active") {
      statusEl.textContent = "● active";
      statusEl.style.color = "#2e7d32";
    } else if (state.missionStatus === "ended") {
      statusEl.textContent = "■ ended";
      statusEl.style.color = "#b71c1c";
    } else {
      statusEl.textContent = "";
    }
  }
  if (endBtn) {
    // Only an active mission that exists on the backend can be ended.
    endBtn.hidden = !(state.missionId && state.missionStatus === "active");
  }
}

async function endCurrentMission() {
  if (!state.missionId) {
    appendDebugLine("No active mission to end.");
    return;
  }
  try {
    const resp = await fetch(`/api/missions/${state.missionId}/end`, { method: "POST" });
    if (!resp.ok) {
      appendDebugLine(`❌ End mission failed (${resp.status}): ${await resp.text()}`);
      return;
    }
    const data = await resp.json();
    state.missionStatus = data.status || "ended";
    appendDebugLine(`🏁 Mission ended: ${data.name || state.missionId}`);
    syncMissionUi();
  } catch (e) {
    appendDebugLine(`❌ End mission error: ${e}`);
  }
}

function invalidateMission(reason) {
  // §7-0.3c / §1a-0: An edit invalidates the SOLUTION, not the MISSION.
  // A mission ends only by an explicit officer action — NOT by editing the
  // environment/config. So we keep state.missionId when a mission is active;
  // the backend (0.3a/0.3b) keeps that id stable across the edit and re-solves
  // the new env in place. Previously this nulled missionId, which silently
  // forked a brand-new mission on every config change.
  //
  // (missionId is cleared elsewhere on explicit reset / End Mission / new
  // environment import — not on a mid-mission edit.)

  // IMPORTANT: During checkpoint workflow, preserve frozen trajectories
  // Only clear routes if we don't have committed segments (no frozen trajectories to preserve)
  const hasCommittedSegments = missionState.committedSegments && missionState.committedSegments.length > 0;
  if (!hasCommittedSegments) {
    state.routes = {};
    state.trajectoryVisible = {};
  } else {
    // In checkpoint workflow - keep frozen routes/trajectories for continuity
  }

  // If config changed after a solve (READY_TO_ANIMATE or DRAFT_READY),
  // reset mode to IDLE so user can re-solve with the new settings
  if (missionState.mode === MissionMode.READY_TO_ANIMATE ||
      missionState.mode === MissionMode.DRAFT_READY) {
    missionState.draftSolution = null;
    setMissionMode(MissionMode.IDLE, reason || "config changed");
  }

  if (reason) {
    appendDebugLine("Mission invalidated: " + reason);
  }

  // Always update stats display to reflect cleared/changed routes
  updateStatsFromRoutes();
}

// ----------------------------------------------------
// Client-side SAM wrapping (no server round-trip!)
// ----------------------------------------------------
function _sampleCircle(cx, cy, r, minSeg = 0.3) {
  // Sample points around a circle for smooth polygon approximation
  // minSeg = 0.3 km gives smooth circles even for small SAMs
  if (r <= 0) return [[cx, cy]];
  let dtheta = minSeg / Math.max(r, 0.001);
  dtheta = Math.max(dtheta, Math.PI / 36); // min 5 degrees (max 72 points)
  dtheta = Math.min(dtheta, Math.PI / 12); // max 15 degrees (min 24 points)
  const nSteps = Math.max(24, Math.ceil(2 * Math.PI / dtheta));
  const thetaStep = 2 * Math.PI / nSteps;
  const pts = [];
  for (let i = 0; i < nSteps; i++) {
    const theta = i * thetaStep;
    pts.push([cx + r * Math.cos(theta), cy + r * Math.sin(theta)]);
  }
  return pts;
}

function _cross(o, a, b) {
  return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
}

function _convexHull(points) {
  const pts = [...new Set(points.map(p => JSON.stringify(p)))].map(s => JSON.parse(s));
  pts.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (pts.length <= 1) return pts;

  const lower = [];
  for (const p of pts) {
    while (lower.length >= 2 && _cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) {
      lower.pop();
    }
    lower.push(p);
  }

  const upper = [];
  for (let i = pts.length - 1; i >= 0; i--) {
    const p = pts[i];
    while (upper.length >= 2 && _cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) {
      upper.pop();
    }
    upper.push(p);
  }

  return lower.slice(0, -1).concat(upper.slice(0, -1));
}

function _distance(a, b) {
  // a and b are [lat, lng] arrays - use haversine for km distance
  return haversineKm(a[0], a[1], b[0], b[1]);
}

function _samsOverlap(sam1, sam2) {
  // Get centers in lat/lng format
  const c1 = sam1.center || {};
  const c2 = sam2.center || {};
  const lat1 = c1.lat !== undefined ? c1.lat : 0;
  const lng1 = c1.lng !== undefined ? c1.lng : 0;
  const lat2 = c2.lat !== undefined ? c2.lat : 0;
  const lng2 = c2.lng !== undefined ? c2.lng : 0;
  const r1 = sam1.range_km || sam1.range || sam1.radius || 0;
  const r2 = sam2.range_km || sam2.range || sam2.radius || 0;
  // Use haversine for distance in km
  return haversineKm(lat1, lng1, lat2, lng2) <= (r1 + r2);
}

// ----------------------------------------------------
// Checkpoint freeze key handler (C) - REMOVED, handled in main keydown listener
// ----------------------------------------------------
// Old duplicate handler removed - C key is now handled in the main window.addEventListener("keydown")
// which properly uses cutAtCheckpoint() with state machine permissions

// Reset mission key handler (R)
// ----------------------------------------------------
if (!window.__resetMissionKeyInstalled) {
  window.__resetMissionKeyInstalled = true;

  document.addEventListener(
    "keydown",
    (e) => {
      if (
        String(e.key).toLowerCase() === "r" &&
        !e.metaKey &&
        !e.ctrlKey &&
        !e.altKey
      ) {
        // Skip if user is typing in an input field
        const activeTag = document.activeElement?.tagName?.toLowerCase();
        if (activeTag === "input" || activeTag === "textarea" || activeTag === "select") {
          return;
        }

        appendDebugLine("R key pressed - attempting reset...");

        // Use state machine permissions
        const perms = getUiPermissions();
        if (perms.canReset) {
          resetMission();
        } else {
          appendDebugLine("Cannot reset in current state: " + missionState.mode);
        }
      }

      // Z key = Undo last optimizer
      if (String(e.key).toLowerCase() === "z" && !e.ctrlKey && !e.metaKey) {
        const activeTag = document.activeElement?.tagName?.toLowerCase();
        if (activeTag === "input" || activeTag === "textarea" || activeTag === "select") {
          return;
        }
        appendDebugLine("Z key pressed - attempting optimizer undo...");
        undoOptimizer();
      }
    },
    true
  );
}


function _pointInPolygon(pt, poly) {
  // Ray casting algorithm - pt is [x, y], poly is array of [x, y]
  if (!poly || poly.length < 3) return false;
  const [x, y] = pt;
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

function _pointToSegmentDistance(pt, segA, segB) {
  // Distance from point pt to line segment [segA, segB]
  const [px, py] = pt;
  const [ax, ay] = segA;
  const [bx, by] = segB;
  const abx = bx - ax;
  const aby = by - ay;
  const apx = px - ax;
  const apy = py - ay;
  const abLenSq = abx * abx + aby * aby;
  if (abLenSq === 0) return Math.hypot(px - ax, py - ay);
  const t = Math.max(0, Math.min(1, (apx * abx + apy * aby) / abLenSq));
  const closestX = ax + t * abx;
  const closestY = ay + t * aby;
  return Math.hypot(px - closestX, py - closestY);
}

function _circleIntersectsPolygon(cx, cy, r, poly) {
  // Check if circle (cx, cy, r) intersects polygon
  if (!poly || poly.length < 3) return false;
  
  // Check if center is inside polygon
  if (_pointInPolygon([cx, cy], poly)) return true;
  
  // Check if any polygon vertex is inside circle
  for (const [vx, vy] of poly) {
    if (Math.hypot(cx - vx, cy - vy) <= r) return true;
  }
  
  // Check if circle intersects any edge
  for (let i = 0; i < poly.length; i++) {
    const p1 = poly[i];
    const p2 = poly[(i + 1) % poly.length];
    if (_pointToSegmentDistance([cx, cy], p1, p2) <= r) return true;
  }
  
  return false;
}

function _clusterOverlappingSams(sams) {
  if (!sams || sams.length === 0) return [];
  const n = sams.length;
  
  // Step 1: Initial clustering based on circle-to-circle overlap
  const parent = Array.from({ length: n }, (_, i) => i);

  function find(x) {
    if (parent[x] !== x) parent[x] = find(parent[x]);
    return parent[x];
  }

  function union(x, y) {
    const px = find(x), py = find(y);
    if (px !== py) parent[px] = py;
  }

  // Union SAMs that overlap directly
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      if (_samsOverlap(sams[i], sams[j])) {
        union(i, j);
      }
    }
  }

  // Group SAMs by their root to form initial clusters
  const clustersMap = {};
  for (let i = 0; i < n; i++) {
    const root = find(i);
    if (!clustersMap[root]) clustersMap[root] = [];
    clustersMap[root].push(i);
  }
  
  // Convert to array of index arrays
  let clusters = Object.values(clustersMap);
  
  // Step 2: Iteratively merge clusters where SAM circles intersect convex hulls
  const maxIterations = n + 10;
  let iteration = 0;
  let changed = true;
  
  while (changed && iteration < maxIterations) {
    iteration++;
    changed = false;
    
    const merged = Array(clusters.length).fill(false);
    const newClusters = [];
    
    for (let i = 0; i < clusters.length; i++) {
      if (merged[i]) continue;
      
      // Build hull for cluster i
      const cluster = clusters[i];
      
      // Compute centroid for projection reference
      let sumLat = 0, sumLng = 0;
      for (const idx of cluster) {
        const sam = sams[idx];
        const c = sam.center || {};
        sumLat += c.lat || 0;
        sumLng += c.lng || 0;
      }
      const refLat = sumLat / cluster.length;
      const refLng = sumLng / cluster.length;
      
      // Sample circles in local km
      const allPoints = [];
      for (const idx of cluster) {
        const sam = sams[idx];
        const c = sam.center || {};
        const lat = c.lat !== undefined ? c.lat : 0;
        const lng = c.lng !== undefined ? c.lng : 0;
        const rangeKm = sam.range_km || sam.range || sam.radius || 0;
        const [cx, cy] = latLngToLocalXY(lat, lng, refLat, refLng);
        allPoints.push(..._sampleCircle(cx, cy, rangeKm, 0.1));
      }
      
      if (allPoints.length === 0) {
        newClusters.push(cluster);
        continue;
      }
      
      const hull = _convexHull(allPoints);
      if (hull.length < 3) {
        newClusters.push(cluster);
        continue;
      }
      
      // Start with this cluster
      let mergedCluster = [...cluster];
      
      // Check if any SAM from any other cluster intersects this hull
      for (let j = 0; j < clusters.length; j++) {
        if (i === j || merged[j]) continue;
        
        let shouldMerge = false;
        for (const idx of clusters[j]) {
          const sam = sams[idx];
          const c = sam.center || {};
          const lat = c.lat !== undefined ? c.lat : 0;
          const lng = c.lng !== undefined ? c.lng : 0;
          const rangeKm = sam.range_km || sam.range || sam.radius || 0;
          
          // Project SAM to local km for intersection test
          const [cx, cy] = latLngToLocalXY(lat, lng, refLat, refLng);
          
          if (_circleIntersectsPolygon(cx, cy, rangeKm, hull)) {
            shouldMerge = true;
            break;
          }
        }
        
        if (shouldMerge) {
          mergedCluster = mergedCluster.concat(clusters[j]);
          merged[j] = true;
          changed = true;
        }
      }
      
      newClusters.push(mergedCluster);
    }
    
    clusters = newClusters;
  }
  
  // Convert indices back to SAM objects
  return clusters.map(cluster => cluster.map(idx => sams[idx]));
}

// Every airport, target and SAM site sits on the ground: its alt_km (km above
// sea level) is the terrain elevation there, from the same terrain the 3D view
// draws (0 without terrain). A tanker holds an altitude it is given — default:
// the Edit panel's tanker altitude box — and records the ground under it in
// ground_km (for its clearance). Fills only what is missing
// (docs/architecture/ALTITUDE_MODEL.md).
async function fillMissingAltitudes(env) {
  if (!env) return;
  const ground = [];
  (env.airports || []).forEach((a) => {
    if (a.type === "tanker") {
      if (a.alt_km == null) a.alt_km = newTankerAltitudeKm();
      if (a.ground_km == null) ground.push({ obj: a, lat: a.lat, lng: a.lng, key: "ground_km" });
      return;
    }
    if (a.alt_km == null) ground.push({ obj: a, lat: a.lat, lng: a.lng });
  });
  (env.targets || []).forEach((t) => {
    if (t.alt_km == null) ground.push({ obj: t, lat: t.lat, lng: t.lng });
  });
  (env.sams || []).forEach((sam) => {
    if (sam.alt_km == null && sam.center) ground.push({ obj: sam, lat: sam.center.lat, lng: sam.center.lng });
  });
  if (!ground.length) return;
  const elevations = (typeof cesiumView !== "undefined" && cesiumView.groundElevationsKm)
    ? await cesiumView.groundElevationsKm(ground)
    : ground.map(() => 0);
  ground.forEach((g, i) => { g.obj[g.key || "alt_km"] = elevations[i]; });
  // Airport elevations decide whether a drone's cruise altitude is underground.
  if (typeof markCeiling === "function") for (let did = 1; did <= 5; did++) markCeiling(did);
  // Altitudes decide which tankers each drone can reach: redraw the refuel
  // chips (and, with them, the Tankers table).
  if (env === getEditableEnv()) populateRefuelStopsBox();
}

// After an import: the live env and every segment's env (they are exported
// again later, so they must carry altitudes too).
function fillMissingAltitudesEverywhere() {
  const envs = new Set([state.env]);
  if (typeof missionReplay !== "undefined" && missionReplay.getSegments) {
    missionReplay.getSegments().forEach((seg) => seg?.env && envs.add(seg.env));
  }
  if (typeof segmentStore !== "undefined" && segmentStore.getSegments) {
    segmentStore.getSegments().forEach((seg) => seg?.env && envs.add(seg.env));
  }
  return Promise.all([...envs].filter(Boolean).map(fillMissingAltitudes));
}

function computeWrappedPolygonsClientSide(sams) {
  if (!sams || sams.length === 0) return [];

  const clusters = _clusterOverlappingSams(sams);
  const polygons = [];

  for (const cluster of clusters) {
    // Compute cluster centroid for projection reference
    let sumLat = 0, sumLng = 0;
    cluster.forEach(sam => {
      const c = sam.center || {};
      sumLat += c.lat || 0;
      sumLng += c.lng || 0;
    });
    const refLat = sumLat / cluster.length;
    const refLng = sumLng / cluster.length;

    // Project SAMs to local km and sample circles
    const allPoints = [];
    for (const sam of cluster) {
      const c = sam.center || {};
      const lat = c.lat !== undefined ? c.lat : 0;
      const lng = c.lng !== undefined ? c.lng : 0;
      const rangeKm = sam.range_km || sam.range || sam.radius || 0;

      // Project center to local km
      const [cx, cy] = latLngToLocalXY(lat, lng, refLat, refLng);
      // Sample circle in km (minSeg = 0.1 km = 100m)
      allPoints.push(..._sampleCircle(cx, cy, rangeKm, 0.1));
    }
    if (allPoints.length === 0) continue;

    // Compute convex hull in local km
    const hull = _convexHull(allPoints);
    if (hull.length >= 3) {
      // Unproject hull vertices back to lat/lng
      const hullLatLng = hull.map(([x, y]) => {
        const [lat, lng] = localXYToLatLng(x, y, refLat, refLng);
        return [lat, lng];
      });
      polygons.push(hullLatLng);
    }
  }

  return polygons;
}

// The dashed SAM boundary is the SAM slice at a cruise altitude h — what routes
// are actually planned around: radius sqrt(r^2 - (h - e)^2), e = the SAM site's
// elevation (server/solver/altitude.py). h = 0 means "not set": full range. A SAM
// the altitude clears entirely has no slice.
function samSlicesAtAltitude(sams, h) {
  return (sams || [])
    .map((sam) => {
      const r = Number(sam.range_km || sam.range || sam.radius || 0);
      const dh = h - Number(sam.alt_km || 0);
      const slice = h > 0 ? Math.sqrt(Math.max(0, r * r - dh * dh)) : r;
      return { ...sam, range_km: slice, range: slice };
    })
    .filter((sam) => sam.range_km > 0);
}

// A drone's cruise altitude (Asset tab); 0 = ground level, SAMs at full size.
function droneCruiseAltitudeKm(cfg) {
  const own = parseFloat(cfg?.cruise_altitude_km);
  return Number.isFinite(own) && own > 0 ? own : 0;
}

// Enabled drones grouped by cruise altitude: [{ altKm, dids: ["1", "3"] }, ...].
function droneAltitudeGroups(configs = getEditableDroneConfigs()) {
  const groups = new Map();
  Object.entries(configs || {}).forEach(([did, cfg]) => {
    if (!cfg || cfg.enabled === false) return;
    const altKm = Math.round(droneCruiseAltitudeKm(cfg) * 1000) / 1000;
    if (!groups.has(altKm)) groups.set(altKm, []);
    groups.get(altKm).push(String(did));
  });
  return [...groups.entries()]
    .map(([altKm, dids]) => ({ altKm, dids: dids.sort() }))
    .sort((a, b) => a.altKm - b.altKm);
}

// When drones fly different altitudes, each altitude's slice gets its own
// outline (drones at one altitude share it). With one altitude: state.wrappedPolygons.
function refreshSamWrapping(env = state.env) {
  const sams = env?.sams || [];
  const groups = droneAltitudeGroups();
  const mission = samSlicesAtAltitude(sams, groups.length ? groups[0].altKm : 0);
  state.wrappedPolygons = mission.length ? computeWrappedPolygonsClientSide(mission) : [];
  state.samSliceGroups = groups.length > 1
    ? groups.map(({ altKm, dids }) => {
        const slices = samSlicesAtAltitude(sams, altKm);
        return {
          altKm,
          dids,
          polygons: slices.length ? computeWrappedPolygonsClientSide(slices) : [],
        };
      })
    : [];
}

// Update SAM wrapping instantly (client-side): imports, SAM drag, altitude change.
function updateSamWrappingClientSide() {
  refreshSamWrapping(getEditableEnv());
}

// ----------------------------------------------------
// Calculate visited targets from route and cutDistance
// Returns array of target IDs that are BEFORE the cut point
// ----------------------------------------------------
/**
 * Calculate visited targets based on the cut position on the solved trajectory.
 * Walks along the trajectory from START and marks targets as visited if they
 * appear BEFORE the cut position.
 *
 * @param {Object} segment - The accepted segment with solution
 * @param {Object} cutPositions - Object with drone ID -> [x, y] cut position
 * @param {Object} env - Environment with target positions
 * @returns {Array} List of visited target IDs
 */
function calculateVisitedTargetsFromCutPosition(segment, cutPositions, env) {
  if (!segment || !segment.solution || !cutPositions) {
    return [];
  }

  const visited = [];
  const routes = segment.solution.routes || {};
  const targets = env?.targets || [];

  // Build target position lookup (now lat/lng)
  const targetPos = {};
  targets.forEach(t => {
    if (t.lat !== undefined && t.lng !== undefined) {
      targetPos[t.id] = { lat: t.lat, lng: t.lng };
    }
  });

  Object.entries(routes).forEach(([did, routeData]) => {
    const traj = routeData.trajectory || [];
    const route = routeData.route || [];
    const cutPos = cutPositions[did];

    if (traj.length < 2 || !cutPos) return;

    // Find where the cut position is on the trajectory
    // cutPos and traj points are now [lat, lng]
    let cutPointIdx = -1;
    let minCutDist = Infinity;
    traj.forEach((pt, idx) => {
      // Use haversine distance for lat/lng
      const dist = haversineKm(pt[0], pt[1], cutPos[0], cutPos[1]);
      if (dist < minCutDist) {
        minCutDist = dist;
        cutPointIdx = idx;
      }
    });

    if (cutPointIdx < 0) return;

    // For each target in the route ORDER, check if it appears before the cut point
    for (const wp of route) {
      if (!String(wp).startsWith("T")) continue;
      if (visited.includes(wp)) continue;

      const pos = targetPos[wp];
      if (!pos) continue;

      // Find trajectory point closest to this target
      let minDist = Infinity;
      let targetIdx = -1;
      traj.forEach((pt, idx) => {
        // Use haversine distance for lat/lng
        const dist = haversineKm(pt[0], pt[1], pos.lat, pos.lng);
        if (dist < minDist) {
          minDist = dist;
          targetIdx = idx;
        }
      });

      // Target is visited if its trajectory index is BEFORE the cut point index
      if (targetIdx >= 0 && minDist < 20.0) {
        if (targetIdx < cutPointIdx) {
          visited.push(wp);
        } else {
        }
      }
    }
  });

  return visited;
}

// ----------------------------------------------------
// Polyline utilities
// points: array of [x,y]
// dist: distance along polyline in same units as coords
// ----------------------------------------------------

/**
 * Truncate a trajectory at a given target position.
 * Finds the closest point on the trajectory to the target and returns
 * all points up to and including that point (plus the target itself).
 * @param {Array} trajectory - Array of [x, y] points
 * @param {Array} targetPos - [x, y] target position to truncate at
 * @returns {Array} - Truncated trajectory ending at targetPos
 */
function truncateTrajectoryAtPosition(trajectory, targetPos) {
  if (!trajectory || trajectory.length === 0 || !targetPos || targetPos.length < 2) {
    return trajectory || [];
  }

  // Find the segment where the target position lies (closest approach)
  let bestDist = Infinity;
  let bestIdx = -1;

  for (let i = 0; i < trajectory.length - 1; i++) {
    const p1 = trajectory[i];
    const p2 = trajectory[i + 1];

    const distance = closestFlightPosition(p1, p2, targetPos).distanceKm;
    if (distance < bestDist) {
      bestDist = distance;
      bestIdx = i;
    }
  }

  if (bestIdx === -1) {
    // Couldn't find a good segment, return full trajectory
    return trajectory;
  }

  // Build truncated trajectory: all points up to bestIdx, plus the target position
  const truncated = trajectory.slice(0, bestIdx + 1);

  // If t > 0, add the interpolated point (which should be close to targetPos)
  // For simplicity, just add the target position itself to ensure trajectory ends exactly there
  truncated.push([...targetPos]);

  return truncated;
}

/**
 * Truncate a trajectory to the SUFFIX starting at a given position — the mirror of
 * truncateTrajectoryAtPosition (which returns the prefix). Finds the closest point
 * on the trajectory to targetPos and returns targetPos followed by all points AFTER
 * it. Used for the LAST segment of a replay: each imported segment stores the full
 * leg trajectory, so the last segment must keep only the part AFTER its own cut (the
 * earlier part is already covered by the preceding segments).
 * @param {Array} trajectory - Array of [x, y] points
 * @param {Array} targetPos - [x, y] position to start the suffix at
 * @returns {Array} - Suffix trajectory beginning at targetPos
 */
function truncateTrajectoryFromPosition(trajectory, targetPos) {
  if (!trajectory || trajectory.length === 0 || !targetPos || targetPos.length < 2) {
    return trajectory || [];
  }
  let bestDist = Infinity;
  let bestIdx = -1;
  for (let i = 0; i < trajectory.length - 1; i++) {
    const p1 = trajectory[i];
    const p2 = trajectory[i + 1];
    const distance = closestFlightPosition(p1, p2, targetPos).distanceKm;
    if (distance < bestDist) {
      bestDist = distance;
      bestIdx = i;
    }
  }
  if (bestIdx === -1) {
    return trajectory;
  }
  // Suffix: start exactly at targetPos, then all points AFTER the matched segment.
  return [[...targetPos], ...trajectory.slice(bestIdx + 1)];
}

function split_polyline_at_distance(points, dist, playbackCumulative) {
  // Defensive defaults
  const n = Array.isArray(points) ? points.length : 0;

  if (n === 0) {
    return {
      prefixPoints: [],
      suffixPoints: [],
      splitPoint: null,
      totalDistance: 0,
      splitIndex: -1,
      t: 0,
    };
  }

  if (n === 1) {
    // Only one point: prefix/suffix are identical
    return {
      prefixPoints: [points[0]],
      suffixPoints: [points[0]],
      splitPoint: points[0],
      totalDistance: 0,
      splitIndex: 0,
      t: 0,
    };
  }

  // Live playback supplies its authoritative timeline, including holds and
  // altitude-adjusted distances. Geometric callers may omit it. Never silently
  // reinterpret a malformed playback timeline as ground distance.
  const hasPlayback = playbackCumulative != null;
  if (hasPlayback && (!Array.isArray(playbackCumulative) ||
      playbackCumulative.length !== n || playbackCumulative[0] !== 0 ||
      playbackCumulative.some((value, i) => !Number.isFinite(value) ||
        (i > 0 && value < playbackCumulative[i - 1])))) {
    throw new Error('Route playback timeline does not match its trajectory.');
  }
  const cum = hasPlayback ? playbackCumulative : [0];
  if (!hasPlayback) {
    for (let i = 1; i < n; i++) {
      cum.push(cum[i - 1] + haversineKm(
        points[i - 1][0], points[i - 1][1], points[i][0], points[i][1]));
    }
  }
  const total = cum[n - 1];

  // Clamp dist into [0, total]
  let d = dist;
  if (!Number.isFinite(d)) d = 0;
  d = Math.max(0, Math.min(d, total));

  // Edge: split at start
  if (d === 0) {
    const sp = points[0];
    return {
      prefixPoints: [sp],
      suffixPoints: [sp, ...points.slice(1)],
      splitPoint: sp,
      totalDistance: total,
      splitIndex: 0,
      t: 0,
    };
  }

  // Edge: split at end
  if (d === total) {
    const sp = points[n - 1];
    return {
      prefixPoints: [...points],
      suffixPoints: [sp],
      splitPoint: sp,
      totalDistance: total,
      splitIndex: n - 2, // last segment
      t: 1,
    };
  }

  // Find segment containing distance d:
  // cum[i] <= d < cum[i+1]
  let seg = 0;
  for (let i = 0; i < n - 1; i++) {
    if (cum[i] <= d && d < cum[i + 1]) {
      seg = i;
      break;
    }
  }

  const segStart = points[seg];
  const segEnd = points[seg + 1];
  const segLen = cum[seg + 1] - cum[seg];

  // Interpolation parameter along the segment
  const t = segLen > 0 ? (d - cum[seg]) / segLen : 0;

  const splitPoint = interpolateFlightPosition(segStart, segEnd, t);

  // Construct prefix:
  // include points[0..seg] plus splitPoint (unless it equals points[seg] exactly)
  const prefixPoints = points.slice(0, seg + 1);
  const lastPrefix = prefixPoints[prefixPoints.length - 1];
  if (lastPrefix[0] !== splitPoint[0] || lastPrefix[1] !== splitPoint[1]) {
    prefixPoints.push(splitPoint);
  }

  // Construct suffix:
  // start with splitPoint, then include points[seg+1..end]
  const suffixPoints = [splitPoint, ...points.slice(seg + 1)];

  return {
    prefixPoints,
    suffixPoints,
    splitPoint,
    totalDistance: total,
    splitIndex: seg,
    t,
  };
}


// ----------------------------------------------------
// Canvas sizing
// ----------------------------------------------------
function resizeCanvasToContainer() {
  const canvas = $("env-canvas");
  if (!canvas) return;

  // Keep fixed 800x800 internal resolution for quality
  // The CSS will handle visual scaling
  canvas.width = 800;
  canvas.height = 800;

  drawEnvironment();
}

// ----------------------------------------------------
// Environment drawing
// ----------------------------------------------------

// Helper: Draw a diamond cut marker with optional label
// isLost: if true, draw a larger solid red diamond (drone lost at this checkpoint)
function drawCutMarker(ctx, mx, my, label, isLost = false) {
  const r = isLost ? 12 : 8;  // Larger sizes: lost=12, normal=8

  ctx.beginPath();
  ctx.moveTo(mx, my - r);      // top
  ctx.lineTo(mx + r, my);      // right
  ctx.lineTo(mx, my + r);      // bottom
  ctx.lineTo(mx - r, my);      // left
  ctx.closePath();

  if (isLost) {
    // Lost drone: red diamond outline (not filled)
    ctx.strokeStyle = "#ef4444";  // red-500
    ctx.lineWidth = 3;
    ctx.stroke();
  } else {
    // Normal: white diamond outline
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 2.5;
    ctx.stroke();
  }

  // Optional label
  if (label) {
    ctx.font = "bold 10px system-ui";
    ctx.fillStyle = isLost ? "#ef4444" : "#ffffff";
    ctx.fillText(label, mx + r + 4, my + 4);
  }
}

// Coalesced redraw: collapse a burst of drawEnvironment() calls in one tick into a
// single draw on the next animation frame. The cut/accept pipeline fires dozens of
// synchronous redraws (config reads, dropdown rebuilds, state resets); without this
// each ran a full canvas clear + marker pass, flooding the frame and stalling the UI.
// Use requestDraw() for non-animation redraws; the animation loop still calls
// drawEnvironment(true) directly so its per-frame cadence is untouched.
let _drawScheduled = false;
function requestDraw() {
  if (_drawScheduled) return;
  _drawScheduled = true;
  requestAnimationFrame(() => {
    _drawScheduled = false;
    drawEnvironment();
  });
}

function drawTimeSyncMarkers(ctx, targets, w2c, hybridIconSize) {
  const sync = getActiveTimeSync();
  if (!sync || !sync.enabled) return;
  const status = String(sync.status || "");
  if (status !== "synchronized" && status !== "infeasible") return;

  const members = Array.isArray(sync.synchronized_waypoints)
    ? sync.synchronized_waypoints
    : [];
  if (!members.length) return;

  const targetById = new Map();
  const addTargets = (items) => {
    (items || []).forEach((t) => {
      const id = String(t.id || t.label || "");
      if (id && t.lat !== undefined && t.lng !== undefined && !targetById.has(id)) {
        targetById.set(id, t);
      }
    });
  };
  addTargets(targets);
  addTargets(state.env?.targets);
  addTargets(state.initialEnvSnapshot?.targets);

  const color = status === "synchronized" ? "#06b6d4" : "#f59e0b";
  const fill = status === "synchronized"
    ? "rgba(6, 182, 212, 0.16)"
    : "rgba(245, 158, 11, 0.14)";
  const label = status === "synchronized" ? "SYNC" : "SYNC BLOCKED";
  const pulse = status === "synchronized"
    ? (Math.sin(Date.now() / 220) + 1) * 0.5
    : 0.35;
  const baseRadius = hybridIconSize(13, 10, 26);

  ctx.save();
  members.forEach((member) => {
    const target = targetById.get(String(member.waypoint));
    if (!target) return;
    const [x, y] = w2c(target.lat, target.lng);
    const outer = baseRadius + pulse * 7;
    const inner = Math.max(6, baseRadius * 0.62);

    ctx.beginPath();
    ctx.arc(x, y, outer, 0, Math.PI * 2);
    ctx.fillStyle = fill;
    ctx.fill();
    ctx.strokeStyle = color;
    ctx.lineWidth = status === "synchronized" ? 2.5 : 2;
    if (status === "infeasible") ctx.setLineDash([5, 4]);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.beginPath();
    ctx.arc(x, y, inner, 0, Math.PI * 2);
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.stroke();

    const did = member.drone_id != null ? ` D${member.drone_id}` : "";
    const text = `${label}${did}`;
    ctx.font = "bold 9px system-ui";
    const width = ctx.measureText(text).width;
    const bx = x - width / 2 - 4;
    const by = y - outer - 16;
    ctx.fillStyle = "rgba(2, 6, 23, 0.84)";
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.roundRect(bx, by, width + 8, 14, 3);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = status === "synchronized" ? "#cffafe" : "#fde68a";
    ctx.fillText(text, bx + 4, by + 10);
  });
  ctx.restore();
}

// Cut diamonds to draw, keyed by position: { pos, label, isLost, did }.
// One list for the 2D canvas and the 3D view.
function collectCheckpointMarkers() {
  const isAtAirport = (lat, lng) => {
    const airports = state.env?.airports || [];
    const thresholdKm = 0.1;  // 100 meters - only skip if VERY close to airport
    return airports.some(a => {
      if (a.lat === undefined || a.lng === undefined) return false;
      const dist = haversineKm(lat, lng, a.lat, a.lng);
      if (dist < thresholdKm) {
        dbg(`[isAtAirport] Position [${lat.toFixed(5)},${lng.toFixed(5)}] is ${dist.toFixed(3)} km from airport ${a.id} at [${a.lat},${a.lng}]`);
        return true;
      }
      return false;
    });
  };

  // Collect all markers to draw (deduplicated by position)
  const markersToDrawMap = new Map(); // key: "x,y" -> { label, isLost, did }

  // Draw markers from missionReplay segments (segments 1+ have cutPositions for C1, C2, etc.)
  // Only draw markers up to the current segment we're working on
  const totalSegs = missionReplay.getSegmentCount();
  const currentSegIdx = missionReplay.getCurrentSegmentIndex();

  // During animation, only show markers up to currentSegIdx (progressive reveal).
  // After RESET (before animation starts), hide all markers — they appear as drones reach them.
  // In other modes (CHECKPOINT, EDITING, etc.), show all.
  const isAnimatingMode =
    missionState.mode === MissionMode.ANIMATING ||
    missionState.mode === MissionMode.PAUSED_MID_ANIMATION;
  const isPreAnimationMode = (missionState.mode === MissionMode.READY_TO_ANIMATE);
  // During a full-mission REPLAY the drone re-flies past every old cut, so show ALL
  // cut diamonds (currentSegIdx is rewound to 0, which would otherwise hide them via
  // the progressive-reveal rule).
  const showAllCheckpoints =
    (!isAnimatingMode && !isPreAnimationMode) || !!state.animation.continuousReplay;

  // DEBUG: Log marker drawing context
  dbg(`[drawCheckpointMarkers] totalSegs=${totalSegs}, currentSegIdx=${currentSegIdx}, showAll=${showAllCheckpoints}`);

  for (let segIdx = 1; segIdx < totalSegs; segIdx++) {
    // Don't draw markers beyond the current segment (e.g., after Reset)
    // UNLESS we're in READY_TO_ANIMATE mode (all segments accepted)
    if (!showAllCheckpoints && segIdx > currentSegIdx) {
      dbg(`[drawCheckpointMarkers] Skipping seg ${segIdx}: beyond currentSegIdx ${currentSegIdx}`);
      continue;
    }

    const seg = missionReplay.getSegment(segIdx);
    // Support both 'cutPosition' (V2 export format) and 'cutPositions' (legacy)
    const segCutPositions = seg?.cutPosition || seg?.cutPositions;
    if (!segCutPositions) {
      dbg(`[drawCheckpointMarkers] Skipping seg ${segIdx}: no cutPositions`);
      continue;
    }

    // Only draw if previous segment has a solution (trajectory exists up to this cut)
    const prevSeg = missionReplay.getSegment(segIdx - 1);
    const prevHasSolution = prevSeg && Object.keys(prevSeg.solution?.routes || {}).length > 0;
    if (!prevHasSolution) {
      dbg(`[drawCheckpointMarkers] Skipping seg ${segIdx}: prevSeg has no solution (routes=${Object.keys(prevSeg?.solution?.routes || {}).length})`);
      continue;
    }

    dbg(`[drawCheckpointMarkers] Processing seg ${segIdx}: cutPositions=${JSON.stringify(segCutPositions)}`);

    const lostDrones = seg.lostDrones || [];
    Object.entries(segCutPositions).forEach(([did, pos]) => {
      if (!pos || pos.length < 2) return;
      if (isAtAirport(pos[0], pos[1])) {
        dbg(`[drawCheckpointMarkers] Skipping D${did} at seg ${segIdx}: at airport`);
        return;
      }
      const key = `${pos[0].toFixed(4)},${pos[1].toFixed(4)}`;
      const isLost = lostDrones.includes(did);
      markersToDrawMap.set(key, { pos, label: `C${segIdx}`, isLost, did });
      dbg(`[drawCheckpointMarkers] Added marker C${segIdx} for D${did} at [${pos[0].toFixed(1)}, ${pos[1].toFixed(1)}]`);
    });
  }

  // Draw PENDING cut positions (after CUT but before Accept)
  // These are not yet in missionReplay, so we draw them separately
  if (state.pendingCutPositions && Object.keys(state.pendingCutPositions).length > 0) {
    // Label from the CUT COUNTER (the authoritative cut number — matches the route's
    // "C{n}-{did}" checkpoint id), NOT totalSegs which can drift (e.g. a multi-stop
    // solve leaving an extra segment made the first cut show C2).
    const nextSegIdx = state.checkpointSegmentCounter || totalSegs;
    const pendingLost = state.pendingLostDrones || [];
    const liveEditDisabled = state.liveEditDisabledDrones || [];
    Object.entries(state.pendingCutPositions).forEach(([did, pos]) => {
      if (!pos || pos.length < 2) return;
      if (isAtAirport(pos[0], pos[1])) return;
      const key = `pending_${pos[0].toFixed(4)},${pos[1].toFixed(4)}`;
      // Mark as lost if in pendingLostDrones OR in liveEditDisabledDrones
      const isLost = pendingLost.includes(did) || liveEditDisabled.includes(did);
      markersToDrawMap.set(key, { pos, label: `C${nextSegIdx}`, isLost, did });
      dbg(`[drawCheckpointMarkers] Added PENDING marker C${nextSegIdx} for D${did} at [${pos[0].toFixed(4)}, ${pos[1].toFixed(4)}]${isLost ? ' (LOST/DISABLED)' : ''}`);
    });
  }

  return markersToDrawMap;
}

let _drawEnvFrameCount = 0;
function drawEnvironment(fromAnimationLoop = false) {
  if (!fromAnimationLoop) {
    syncTankerAltBox();
    renderMapLegend();
    updateCategoryCounts();
    // Many paths set state.trajectoryVisible and then redraw: the Show boxes
    // follow it here, where the trajectories are drawn from it.
    updateTrajectoryButtonStates();
  }
  // 3D view has no animation sync yet (static viewer only) — re-render on
  // genuine state-changing redraws (solve, edit, import), not on every
  // animation frame, so it doesn't rebuild all Cesium entities at 60fps.
  if (typeof cesiumView !== "undefined" && cesiumView.isActive() && !fromAnimationLoop) {
    cesiumView.render();
  }

  const canvas = $("env-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  // During animation, ONLY allow draws from the animation loop itself
  // This prevents redraw storms from other sources (event handlers, timers, etc.)
  const isAnimating = missionState.mode === MissionMode.ANIMATING || state.animation.active;
  if (isAnimating && !fromAnimationLoop) {
    return; // Skip non-animation-loop draws during animation
  }

  _drawEnvFrameCount++;
  if (_drawEnvFrameCount % 120 === 1 && state.visitedTargets.length > 0) {
  }

  // CRITICAL: Clear the ENTIRE canvas before drawing anything
  // Use identity transform to ensure we clear the full canvas regardless of any transforms
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.restore();

  // If no environment yet, draw placeholder grid
  if (!state.env) {
    ctx.fillStyle = "#020617";
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    ctx.strokeStyle = "#1f2937";
    ctx.lineWidth = 0.5;
    for (let x = 0; x < canvas.width; x += 30) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, canvas.height);
      ctx.stroke();
    }
    for (let y = 0; y < canvas.height; y += 30) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(canvas.width, y);
      ctx.stroke();
    }
    return;
  }

  // Draw from the SAME env the edit UI mutates (staged during a live edit, else
  // state.env). This makes staged-added objects draw as first-class + keeps the
  // yellow selection ring (which indexes this array) in sync with hitTestPixel.
  // NOTE: the separate faint staged-overlay (~5534) still runs for now, so staged
  // objects will render twice until that redundant block is removed (step 2).
  const _editEnv = getEditableEnv() || state.env;
  const airports = _editEnv.airports || [];
  const targets = _editEnv.targets || [];
  const sams = _editEnv.sams || [];

  // ============================================================
  // Unified Coordinate Transformation (Web Mercator projection)
  // Storage is native lat/lng; transform directly to canvas pixels
  // ============================================================

  function getPixelsPerDegree() {
    return 256 * Math.pow(2, viewState.zoomLevel) / 360;
  }

  // Drawing and hit-testing share exactly the same geographic projection.
  function w2c(lat, lng) {
    return mapPointToCanvas(canvas, lat, lng);
  }

  // Get meters per pixel at current zoom level (for geographic scaling)
  // Scale a km radius to pixels (for SAM circles - geographic scaling)
  // rangeKm is in kilometers, centerLat is the SAM center latitude
  function worldToPixelRadius(rangeKm, centerLat) {
    // Convert km to degrees at the given latitude
    // 1 degree longitude = ~111.32 * cos(lat) km
    const kmPerDegLng = 111.32 * Math.cos((centerLat || viewState.centerLat) * Math.PI / 180);
    const degRadius = rangeKm / kmPerDegLng;
    // Convert degrees to pixels at current zoom
    const pixelsPerDeg = getPixelsPerDegree();
    return degRadius * pixelsPerDeg;
  }

  // Hybrid scaling for icons: base size with zoom adjustment, clamped
  function hybridIconSize(baseSize, minPx, maxPx) {
    // Scale based on zoom level (zoom 13 is our baseline)
    const zoomFactor = Math.pow(2, viewState.zoomLevel - 13);
    const scaled = baseSize * Math.sqrt(zoomFactor);  // Use sqrt for gentler scaling
    return Math.max(minPx, Math.min(maxPx, scaled));
  }

  // Background — skip in map mode (canvas is transparent, map shows through)
  if (!mapState.active) {
    ctx.fillStyle = "#020617";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }

  // Grid — skip in map mode (map provides visual reference)
  // In non-map mode, we just use the dark background without grid lines
  // since coordinates are now lat/lng-based rather than 0-100 grid

  // Helper to find lat/lng position by label
  function findWaypointPosition(label) {
    const id = String(label);
    const a = airports.find((x) => String(x.id) === id);
    if (a && a.lat !== undefined && a.lng !== undefined) return { lat: a.lat, lng: a.lng };
    const t = targets.find((x) => String(x.id) === id);
    if (t && t.lat !== undefined && t.lng !== undefined) return { lat: t.lat, lng: t.lng };
    return null;
  }

  // SAMs
  sams.forEach((sam, idx) => {
    const center = sam.center;
    if (!center || center.lat === undefined || center.lng === undefined) return;
    const [sx, sy] = w2c(center.lat, center.lng);
    const rangeKm = Number(sam.range_km || sam.range || 0);
    // SAM radius scales with zoom (geographic area, now in km)
    const rpx = worldToPixelRadius(rangeKm, center.lat);

    ctx.beginPath();
    ctx.arc(sx, sy, rpx, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(234,179,8,0.18)";
    ctx.strokeStyle = "#f59e0b";
    ctx.lineWidth = 1;
    ctx.fill();
    ctx.stroke();

    // SAM center triangle: hybrid scaling (min 4px, max 12px)
    const size = hybridIconSize(7, 4, 12);
    ctx.beginPath();
    ctx.moveTo(sx, sy - size);
    ctx.lineTo(sx - size, sy + size);
    ctx.lineTo(sx + size, sy + size);
    ctx.closePath();
    ctx.fillStyle = "#f97316";
    ctx.strokeStyle = "#c2410c";
    ctx.fill();
    ctx.stroke();

    // Draw SAM number label (fixed size - always readable, with outline for map visibility)
    ctx.font = "bold 10px system-ui";
    const samLabel = `S${idx}`;
    const samLabelX = sx + size + 3;
    const samLabelY = sy + 4;
    // Dark outline for visibility on light map backgrounds
    ctx.strokeStyle = "#000000";
    ctx.lineWidth = 3;
    ctx.strokeText(samLabel, samLabelX, samLabelY);
    ctx.fillStyle = "#fbbf24";  // Yellow for visibility on both backgrounds
    ctx.fillText(samLabel, samLabelX, samLabelY);

    // Highlight if selected - show dashed boundary
    if (
      state.selectedObject &&
      state.selectedObject.kind === "sam" &&
      state.selectedObject.index === idx
    ) {
      ctx.beginPath();
      ctx.arc(sx, sy, rpx, 0, Math.PI * 2);
      ctx.strokeStyle = "#facc15";
      ctx.lineWidth = 2;
      ctx.setLineDash([6, 4]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  });

  // Wrapped SAM Polygons - dashed yellow outline around the SAM slices drones
  // plan around: one per altitude the drones fly (refreshSamWrapping), all the
  // same colour — several drones can share an altitude. Points are [lat, lng].
  const sliceOutlines = (state.samSliceGroups && state.samSliceGroups.length > 1)
    ? state.samSliceGroups.flatMap((g) => g.polygons.map((polygon) => ({ polygon, color: "#facc15" })))
    : (state.wrappedPolygons || []).map((polygon) => ({ polygon, color: "#facc15" }));
  if (sliceOutlines.length > 0) {
    sliceOutlines.forEach(({ polygon, color }) => {
      if (!polygon || polygon.length < 3) return;

      ctx.beginPath();
      appendMapPath(ctx, polygon, canvas, true);

      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.setLineDash([5, 3]);
      ctx.stroke();
      ctx.setLineDash([]);
    });
  }

  // Airports and Checkpoint Markers (synthetic starts)
  airports.forEach((a, idx) => {
    if (a.lat === undefined || a.lng === undefined) return;
    const [x, y] = w2c(a.lat, a.lng);

    // TANKER: mid-air refuel point. Draw an ORANGE DIAMOND (distinct from the blue
    // airport SQUARE) so it reads as "not a runway, refuel-only".
    if (a.type === "tanker") {
      const tsize = hybridIconSize(9, 5, 26);
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(Math.PI / 4);  // square → diamond
      ctx.fillStyle = "#f97316";   // orange
      ctx.strokeStyle = "#c2410c";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.rect(-tsize, -tsize, tsize * 2, tsize * 2);
      ctx.fill();
      ctx.stroke();
      ctx.restore();
      // Label (upright, not rotated).
      ctx.font = "bold 10px system-ui";
      ctx.strokeStyle = "#000000";
      ctx.lineWidth = 3;
      ctx.strokeText(String(a.id), x + tsize + 3, y - tsize - 2);
      ctx.fillStyle = "#fb923c";
      ctx.fillText(String(a.id), x + tsize + 3, y - tsize - 2);
      // Selection highlight.
      if (state.selectedObject && state.selectedObject.kind === "airport" &&
          state.selectedObject.index === idx) {
        ctx.strokeStyle = "#facc15";
        ctx.lineWidth = 2;
        ctx.strokeRect(x - tsize - 4, y - tsize - 4, (tsize * 2) + 8, (tsize * 2) + 8);
      }
      return;
    }

    // Check if this is a synthetic start (checkpoint marker)
    const isSyntheticStart = a.id && (a.id.includes("_START") || a.id.startsWith("D") && a.id.endsWith("_START"));

    // Use hybrid scaling: airports min 6px, max 30px; checkpoints smaller
    const baseSize = isSyntheticStart ? 5 : 10;
    const size = hybridIconSize(baseSize, isSyntheticStart ? 3 : 6, isSyntheticStart ? 15 : 30);
    const fillColor = "#3b82f6";  // Same blue for both
    const strokeColor = isSyntheticStart ? "#ffffff" : "#1d4ed8";  // White border for checkpoints
    const strokeWidth = isSyntheticStart ? 2 : 1;  // Thicker white border for checkpoints

    ctx.fillStyle = fillColor;
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = strokeWidth;
    ctx.beginPath();
    ctx.rect(x - size, y - size, size * 2, size * 2);
    ctx.fill();
    ctx.stroke();

    // Draw label (fixed size - always readable, with outline for map visibility)
    // For real airports, append runway type "(L/M/S)" so compatibility is visible at a glance.
    ctx.font = isSyntheticStart ? "bold 8px system-ui" : "bold 10px system-ui";
    const runwayTag = isSyntheticStart
      ? ""
      : ` (${(a.runway || "L").toUpperCase()})`;
    const labelText = String(a.id) + runwayTag;
    const labelX = x + size + 2;
    const labelY = y - size - 2;
    // Dark outline for visibility on light map backgrounds
    ctx.strokeStyle = "#000000";
    ctx.lineWidth = 3;
    ctx.strokeText(labelText, labelX, labelY);
    ctx.fillStyle = "#fbbf24";  // Yellow for visibility on both backgrounds
    ctx.fillText(labelText, labelX, labelY);

    // Highlight if selected
    if (
      state.selectedObject &&
      state.selectedObject.kind === "airport" &&
      state.selectedObject.index === idx
    ) {
      ctx.strokeStyle = "#facc15";
      ctx.lineWidth = 2;
      ctx.strokeRect(x - size - 3, y - size - 3, (size * 2) + 6, (size * 2) + 6);
    }
  });

  // Helper function to check if a point (lat/lng) is inside any SAM polygon
  function isInsideAnySAM(lat, lng) {
    for (const sam of sams) {
      const center = sam.center;
      if (!center || center.lat === undefined || center.lng === undefined) continue;

      const rangeKm = Number(sam.range_km || sam.range || 0);

      // Calculate distance using haversine (km)
      const distance = haversineKm(lat, lng, center.lat, center.lng);

      // If inside this SAM's range, return true
      if (distance < rangeKm) {
        return true;
      }
    }
    return false;
  }

  // ===========================================================================
  // DETECTION RADIUS CIRCLES (Animation only)
  // Draw dashed circles around targets showing detection range for approaching drones
  // ===========================================================================
  const droneColors = {
    "1": "#3b82f6",
    "2": "#f97316",
    "3": "#22c55e",
    "4": "#ef4444",
    "5": "#a855f7",
  };

  if (state.animation.active) {
    // For each animating drone, draw detection radius circles around its targets
    Object.entries(state.animation.drones || {}).forEach(([did, droneState]) => {
      if (!droneState.animating && !droneState._lostAtCutDistance) return;
      if (!droneState._currentLat || !droneState._currentLng) return;

      const droneConfig = state.droneConfigs[did] || {};
      const detectionRadiusConfig = droneConfig.detection_radius || {};

      // Get targets in this drone's route
      const routeInfo = state.routes[did];
      if (!routeInfo || !routeInfo.route) return;

      const routeTargetIds = new Set();
      for (const wp of routeInfo.route) {
        if (wp && String(wp).startsWith('T')) {
          routeTargetIds.add(String(wp));
        }
      }

      // Draw circles for unvisited targets with detection_radius > 0
      const allTargets = getAllKnownTargets();
      for (const target of allTargets) {
        if (!target.id) continue;
        if (!routeTargetIds.has(target.id)) continue;
        if (state.visitedTargets && state.visitedTargets.includes(target.id)) continue;

        const targetType = (target.type || 'a').toLowerCase();
        const detectionRadius = detectionRadiusConfig[targetType] || 0;

        if (detectionRadius > 0) {
          const [tx, ty] = w2c(target.lat, target.lng);
          const radiusPx = worldToPixelRadius(detectionRadius, target.lat);

          ctx.beginPath();
          ctx.arc(tx, ty, radiusPx, 0, Math.PI * 2);
          ctx.setLineDash([5, 5]);
          ctx.strokeStyle = droneColors[did] || "#22c55e";
          ctx.lineWidth = 2;
          ctx.globalAlpha = 0.6;
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.globalAlpha = 1.0;
        }
      }
    });
  }

  // Targets (screen positions kept for the hover label)
  targetScreenPositions = [];
  targets.forEach((t, idx) => {
    if (t.lat === undefined || t.lng === undefined) return;
    const [x, y] = w2c(t.lat, t.lng);
    // Target radius: hybrid scaling (min 3px, max 12px)
    const r = hybridIconSize(4, 3, 12);
    const type = (t.type || "a").toLowerCase();
    const color = categoryColor(type);
    targetScreenPositions.push({ t, x, y });

    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.strokeStyle = "#111827";
    ctx.lineWidth = 1;
    ctx.fill();
    ctx.stroke();

    // Label: fixed size - always readable, with outline for map visibility
    ctx.font = "bold 9px system-ui";
    const priority = t.priority || 5;
    const targetLabel = `${t.id}-${priority}`;
    const targetLabelX = x + r + 2;
    const targetLabelY = y + 3;
    // Dark outline for visibility on light map backgrounds
    ctx.strokeStyle = "#000000";
    ctx.lineWidth = 3;
    ctx.strokeText(targetLabel, targetLabelX, targetLabelY);
    ctx.fillStyle = "#fbbf24";  // Yellow for visibility on both backgrounds
    ctx.fillText(targetLabel, targetLabelX, targetLabelY);

    // Draw green X if target is visited (completed in previous checkpoint segment)
    const isVisited = state.visitedTargets && state.visitedTargets.includes(t.id);
    // X marker size: hybrid scaling
    const xSize = hybridIconSize(10, 6, 20);
    if (isVisited) {
      ctx.strokeStyle = "#22c55e";  // green
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(x - xSize, y - xSize);
      ctx.lineTo(x + xSize, y + xSize);
      ctx.moveTo(x + xSize, y - xSize);
      ctx.lineTo(x - xSize, y + xSize);
      ctx.stroke();
    }
    // Draw red X if target is inside a SAM polygon (no-fly zone)
    else if (isInsideAnySAM(t.lat, t.lng)) {
      ctx.strokeStyle = "#ef4444";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(x - xSize * 0.8, y - xSize * 0.8);
      ctx.lineTo(x + xSize * 0.8, y + xSize * 0.8);
      ctx.moveTo(x + xSize * 0.8, y - xSize * 0.8);
      ctx.lineTo(x - xSize * 0.8, y + xSize * 0.8);
      ctx.stroke();
    }

    if (
      state.selectedObject &&
      state.selectedObject.kind === "target" &&
      state.selectedObject.index === idx
    ) {
      ctx.beginPath();
      ctx.arc(x, y, r + 5, 0, Math.PI * 2);
      ctx.strokeStyle = "#facc15";
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  });

  drawTimeSyncMarkers(ctx, targets, w2c, hybridIconSize);

  // (Removed the staged-edits overlay. It diffed stagedEnv against the LIVE env to
  // draw pending adds faint + pending removes with a red X. Now that the main draw
  // reads getEditableEnv() — the same staged env — staged objects render as normal,
  // selectable, draggable first-class objects, so the overlay only double-drew them
  // and its live-vs-staged diff no longer made sense. One source of truth.)

  // Routes (per drone) - only draw if trajectory is visible
  const colors = {
    "1": "#3b82f6",
    "2": "#f97316",
    "3": "#22c55e",
    "4": "#ef4444",
    "5": "#a855f7",
  };


  // §5/§8.8: draw the FROZEN (already-flown, past) trajectory as static history —
  // a dimmed solid line — so the flown A→diamond path stays visible even though the
  // animated route is now segment-only (diamond→end). Frozen = past, display-only.
  const _frozen = (typeof missionReplay !== "undefined" && missionReplay.getAllFrozenTrajectories)
    ? missionReplay.getAllFrozenTrajectories() : null;
  if (_frozen) {
    Object.entries(_frozen).forEach(([did, pts]) => {
      if (!Array.isArray(pts) || pts.length < 2) return;
      if (state.trajectoryVisible[did] === false) return;
      ctx.save();
      ctx.beginPath();
      appendMapFlightPath(ctx, pts, canvas);
      ctx.strokeStyle = colors[did] || "#22c55e";
      ctx.globalAlpha = 0.55;   // dimmed so past history still reads as past
      ctx.lineWidth = 2;        // same as the active route — uniform trajectory line
      ctx.setLineDash([4, 3]);  // dashed to match the active route
      ctx.stroke();
      ctx.restore();
    });
  }

  Object.entries(state.routes || {}).forEach(([did, info]) => {
    // Check if this trajectory should be visible
    if (!state.trajectoryVisible[did]) return;

    // Use trajectory if available (contains SAM-avoiding paths), otherwise use route waypoints
    const trajectory = info.trajectory || [];
    const route = info.route || [];

    ctx.beginPath();

    const points = trajectory.length ? trajectory : route.map(findWaypointPosition)
      .filter(Boolean).map(pos => [pos.lat, pos.lng]);
    appendMapFlightPath(ctx, points, canvas);

    ctx.strokeStyle = colors[did] || "#22c55e";
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 3]);
    ctx.stroke();
    ctx.setLineDash([]);
  });

  // Draw animated drones (active animation OR frozen at checkpoint OR drones positioned after replan)
  const shouldDrawDrones = state.animation.active ||
                          state.checkpoint?.active ||
                          (state.animation.drones && Object.keys(state.animation.drones).length > 0);
  if (shouldDrawDrones && state.animation.drones) {
    Object.entries(state.animation.drones).forEach(([did, droneState]) => {
      // Draw if animating OR frozen at checkpoint OR drone has a valid position (including lost drones)
      const isLostDrone = droneState._lostAtCutDistance !== undefined;
      if (!droneState.animating && !state.checkpoint?.active && !isLostDrone && droneState.distanceTraveled === undefined) return;
      if (!state.trajectoryVisible[did]) return;

      const routeInfo = state.routes[did];
      // Allow lost drones to be drawn even without routeInfo
      if (!routeInfo && !isLostDrone) return;

      // Use trajectory if available (SAM-avoiding path), otherwise fall back to route waypoints
      const trajectory = routeInfo?.trajectory || [];
      const route = routeInfo?.route || [];
      const progress = droneState.progress;

      let droneX, droneY;

      // If drone is lost (stopped at cut marker), draw at the saved cut position (matches marker)
      if (isLostDrone) {
        if (droneState._lostAtPosition) {
          // Use saved cut position to match the red diamond marker
          droneX = droneState._lostAtPosition[0];
          droneY = droneState._lostAtPosition[1];
        } else if (trajectory.length > 0) {
          // Fallback: use end of truncated trajectory
          const lastPt = trajectory[trajectory.length - 1];
          droneX = lastPt[0];
          droneY = lastPt[1];
        } else {
          // Try to get position from pending cut positions or missionReplay segment
          const pendingPos = state.pendingCutPositions?.[did];
          if (pendingPos) {
            droneX = pendingPos[0];
            droneY = pendingPos[1];
          } else {
            // Try to find cut position from missionReplay segments
            // Support both 'cutPosition' (V2 export format) and 'cutPositions' (legacy)
            const segCount = missionReplay.getSegmentCount();
            for (let segIdx = 1; segIdx < segCount; segIdx++) {
              const seg = missionReplay.getSegment(segIdx);
              const segCutPos = seg?.cutPosition || seg?.cutPositions;
              if (seg?.lostDrones?.includes(did) && segCutPos?.[did]) {
                droneX = segCutPos[did][0];
                droneY = segCutPos[did][1];
                break;
              }
            }
            if (droneX === undefined) return; // No position to draw at
          }
        }
      // If frozen at checkpoint (interactive cut or replay pause), draw drone at checkpoint
      } else if (state.checkpoint?.active && state.checkpoint.segments[did]?.splitPoint) {
        const sp = state.checkpoint.segments[did].splitPoint;
        droneX = sp[0];
        droneY = sp[1];
      } else if (state.animation.checkpointPause?.active && state.pendingCutPositions?.[did]) {
        // During replay checkpoint pause, draw drone at the cut position (inside diamond)
        droneX = state.pendingCutPositions[did][0];
        droneY = state.pendingCutPositions[did][1];
      } else if (trajectory.length >= 2) {
        // Animate along the full SAM-avoiding trajectory using distance-based interpolation
        // This ensures uniform speed regardless of segment count (SAM circles have many short segments)
        const cumulativeDistances = droneState.cumulativeDistances || [];
        const totalDistance = droneState.totalDistance || 0;

        if (totalDistance > 0 && cumulativeDistances.length === trajectory.length) {
          // Find position based on distance traveled (uniform speed)
          const targetDistance = droneState.distanceTraveled;

          // Binary search for the segment containing this distance
          let segmentIdx = 0;
          for (let i = 1; i < cumulativeDistances.length; i++) {
            if (cumulativeDistances[i] >= targetDistance) {
              segmentIdx = i - 1;
              break;
            }
            segmentIdx = i - 1;
          }

          const fromPoint = trajectory[segmentIdx];
          const toPoint = trajectory[segmentIdx + 1] || fromPoint;
          const segmentStartDist = cumulativeDistances[segmentIdx];
          const segmentEndDist = cumulativeDistances[segmentIdx + 1] || segmentStartDist;
          const segmentLength = segmentEndDist - segmentStartDist;

          const segmentProgress = segmentLength > 0
            ? (targetDistance - segmentStartDist) / segmentLength
            : 0;

          [droneX, droneY] = interpolateFlightPosition(fromPoint, toPoint, segmentProgress);
        } else {
          // Fallback to segment-based (old behavior)
          const totalSegments = trajectory.length - 1;
          const currentSegmentFloat = progress * totalSegments;
          const currentSegment = Math.min(Math.floor(currentSegmentFloat), totalSegments - 1);
          const segmentProgress = currentSegmentFloat - currentSegment;

          const fromPoint = trajectory[currentSegment];
          const toPoint = trajectory[currentSegment + 1] || fromPoint;

          [droneX, droneY] = interpolateFlightPosition(fromPoint, toPoint, segmentProgress);
        }
      } else if (route.length >= 2) {
        // Fallback: animate along waypoint route (straight lines)
        const totalSegments = route.length - 1;
        const currentSegmentFloat = progress * totalSegments;
        const currentSegment = Math.floor(currentSegmentFloat);
        const segmentProgress = currentSegmentFloat - currentSegment;

        if (currentSegment >= totalSegments) return;

        const fromLabel = route[currentSegment];
        const toLabel = route[currentSegment + 1];
        const fromPos = findWaypointPosition(fromLabel);
        const toPos = findWaypointPosition(toLabel);

        if (!fromPos || !toPos) return;

        [droneX, droneY] = interpolateFlightPosition(
          [fromPos.lat, fromPos.lng], [toPos.lat, toPos.lng], segmentProgress);
      } else {
        return;
      }

      // §8.8 one-shot diagnostic: where is the drone actually drawn, and via which
      // inputs? Fires once per display/animate (re-armed in startAnimation +
      // applyDraftSolutionToUI). Compares the drawn (droneX,Y) to trajectory[0]
      // (should be the diamond) and the cut position.
      if (!state.animation._dbgPos) {
        state.animation._dbgPos = true;
        const t0 = (routeInfo?.trajectory && routeInfo.trajectory[0]) || [NaN, NaN];
        const cp = state.pendingCutPositions?.[did] || (state.checkpoint?.segments?.[did]?.splitPoint) || [NaN, NaN];
        appendDebugLine(`🔎 D${did} DRAW @(${droneX.toFixed(3)},${droneY.toFixed(3)}) | distTrav=${(droneState.distanceTraveled ?? NaN).toFixed?.(2)} segOff=${(state.animation.segmentStartOffset ?? NaN)} | traj0=(${t0[0]?.toFixed?.(3)},${t0[1]?.toFixed?.(3)}) cut=(${cp[0]?.toFixed?.(3)},${cp[1]?.toFixed?.(3)}) | cpActive=${!!state.checkpoint?.active} lost=${isLostDrone} cumLen=${(droneState.cumulativeDistances||[]).length} trajLen=${(routeInfo?.trajectory||[]).length}`);
      }

      // Mirror this frame's already-computed lat/lng into the 3D view's drone
      // marker, if it's open. Cheap (one entity position set, no full
      // render()/entity rebuild) so it's safe to call every animation frame.
      // distanceTraveled/totalDistance let the marker follow the SAME
      // climb/descent ramp as the static route polyline (buildRoutePositions).
      if (typeof cesiumView !== "undefined" && cesiumView.isActive()) {
        cesiumView.updateDronePosition(
          did, droneX, droneY,
          droneState.distanceTraveled || 0,
          droneState.totalDistance || 0
        );
      }

      const [cx, cy] = w2c(droneX, droneY);

      // Draw drone as a filled circle: hybrid scaling (min 4px, max 15px)
      const droneRadius = hybridIconSize(6, 4, 15);
      ctx.beginPath();
      ctx.arc(cx, cy, droneRadius, 0, Math.PI * 2);
      ctx.fillStyle = colors[did] || "#22c55e";
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 2;
      ctx.fill();
      ctx.stroke();

      // Draw drone label (fixed size - always readable)
      ctx.fillStyle = "#ffffff";
      ctx.font = "bold 8px system-ui";
      ctx.fillText(`D${did}`, cx + droneRadius + 2, cy - droneRadius - 2);
    });
  }

  // ===========================================================================
  // UNIFIED CUT MARKER DRAWING
  // Single source of truth: missionReplay segments
  // Each segment N (for N >= 1) stores cutPositions for cut C(N)
  // lostDrones in segment N = drones that were disabled at cut C(N)
  // ===========================================================================
  const markersToDrawMap = collectCheckpointMarkers();

  // Draw all collected markers (no duplicates)
  markersToDrawMap.forEach(({ pos, label, isLost }) => {
    const [mx, my] = w2c(pos[0], pos[1]);
    drawCutMarker(ctx, mx, my, label, isLost);
  });

  // The target-category legend is an HTML box over the map (renderMapLegend).
}

// ----------------------------------------------------
// Edit mode toggle
// ----------------------------------------------------
// Right-panel global edit gate.
// Sections tagged .env-edit-gated in index.html (Drone Config, Allocation)
// are locked from user interaction until state.editMode is true.
function updateEnvPanelLocked() {
  const locked = !state.editMode;
  document.querySelectorAll(".env-edit-gated").forEach((el) => {
    el.classList.toggle("env-edit-locked", locked);
  });
  // The Edit panel's On/Off switch shows the edit state.
  const sw = $("btn-toggle-edit");
  if (sw) {
    sw.classList.toggle("edit-active", !locked);
    sw.setAttribute("aria-checked", locked ? "false" : "true");
  }
  $("edit-panel")?.classList.toggle("edit-locked", locked);
  updateEditHint();
  // Re-sync the multi-stop refuel grid so its per-slot enabled state reflects the
  // new edit mode (the box's own gate is handled by .env-edit-locked above).
  if (typeof populateRefuelStopsBox === "function") populateRefuelStopsBox();
}

// Mission-busy gate.
// Sections tagged .mission-busy-gated in index.html (Post-Optimization,
// Import/Export) are usable in most states, but disabled while a mission
// is mid-flight or mid-edit, because those actions would mutate state
// that's actively being read/animated and have caused session crashes.
function updateMissionBusyLocked() {
  const busy =
    missionState.mode === MissionMode.EDITING_ENV ||
    missionState.mode === MissionMode.ANIMATING ||
    missionState.mode === MissionMode.PAUSED_MID_ANIMATION;
  document.querySelectorAll(".mission-busy-gated").forEach((el) => {
    el.classList.toggle("mission-busy-locked", busy);
  });
}

function setEditMode(on) {
  const perms = getUiPermissions();

  if (on) {
    // Entering edit mode
    if (!perms.canEnterEdit) {
      appendDebugLine("Cannot enter edit mode in current state");
      return;
    }
    enterEditMode();
  } else {
    // Exiting edit mode - if currently editing, this cancels edits
    if (missionState.mode === MissionMode.EDITING_ENV) {
      cancelEdits();
    } else {
      // Not in EDITING_ENV, just update the low-level edit flag
      state.editMode = false; updateCanvasPointerEvents();
      setAddMode(null);
      state.selectedObject = null;
      state.dragging = null;
      drawEnvironment();
    }
  }
  updateEnvPanelLocked();
}

function attachEditToggle() {
  // Start with edit mode OFF for the state machine
  state.editMode = false;
  updateCanvasPointerEvents();
  updateEnvPanelLocked();
  updateMissionBusyLocked();

  // Shared toggle handler. All Edit buttons in the GUI (toolbar
  // #btn-toggle-edit, Mission Control #mc-edit, any future ones) route
  // through here so they manipulate one global edit state.
  const onEditClick = () => {
    const perms = getUiPermissions();
    if (missionState.mode === MissionMode.EDITING_ENV) {
      // Currently editing - toggle OFF means ACCEPT edits (save changes)
      acceptEdits();
    } else if (perms.canEnterEdit) {
      enterEditMode();
    } else {
      appendDebugLine("Cannot toggle edit mode in current state");
    }
  };

  $("btn-toggle-edit")?.addEventListener("click", onEditClick);
}

// ----------------------------------------------------
// Canvas editing: hit-test, drag, add, delete
// ----------------------------------------------------
function updateAddModeButtons() {
  const buttonByMode = {
    target: $("btn-add-target"),
    airport: $("btn-add-airport"),
    tanker: $("btn-add-tanker"),
    sam: $("btn-add-sam"),
  };

  Object.entries(buttonByMode).forEach(([mode, btn]) => {
    if (!btn) return;
    const active = state.addMode === mode;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
  });

  // Drive context-sensitive dropdowns in the Edit panel via a single class.
  // CSS (.edit-panel.add-mode-target / .add-mode-airport / .add-mode-sam)
  // decides what shows.
  const editPanel = document.querySelector(".edit-panel");
  if (editPanel) {
    editPanel.classList.toggle("add-mode-target", state.addMode === "target");
    editPanel.classList.toggle("add-mode-airport", state.addMode === "airport");
    editPanel.classList.toggle("add-mode-sam", state.addMode === "sam");
    editPanel.classList.toggle("add-mode-tanker", state.addMode === "tanker");
  }
  updateEditHint();
}

// The line under the Edit tools: what a click on the map will do.
function updateEditHint() {
  const hint = $("edit-hint");
  if (!hint) return;
  const tanker = typeof selectedTanker === "function" ? selectedTanker() : null;
  $("edit-panel")?.classList.toggle("tanker-selected", Boolean(tanker));
  let text;
  if (!state.editMode) {
    text = "Edit is off: the map can't be changed. Switch it on to add, move or delete.";
  } else if (state.addMode) {
    const what = { target: "targets", airport: "an airport", tanker: "a tanker", sam: "a SAM" }[state.addMode];
    const tool = { target: "Target", airport: "Airport", tanker: "Tanker", sam: "SAM" }[state.addMode];
    text = `Click the map to place ${what}. Click ${tool} again to stop.`;
  } else if (tanker) {
    text = `${tanker.id} selected: set its altitude above, or Delete it.`;
  } else if (state.selectedObject) {
    text = "Drag to move it, or Delete it.";
  } else {
    text = "Pick a tool. With no tool: click an item to select it, drag to move it.";
  }
  hint.textContent = text;
}

function setAddMode(kind, options = {}) {
  state.addMode = kind; // "airport", "target", "sam", or null
  if (!options.preserveSelection) {
    state.selectedObject = null;
  }
  state.dragging = null;
  updateAddModeButtons();
  updateCanvasPointerEvents();
  if (kind) {
    appendDebugLine(`Add mode: ${kind.toUpperCase()} (click canvas to place multiple)`);
  } else {
    appendDebugLine("Add mode: off");
  }
}

function deleteSelected() {
  if (!state.env || !state.selectedObject) return;

  const env = getEditableEnv();
  const { kind, index } = state.selectedObject;
  if (kind === "airport") {
    env.airports.splice(index, 1);
    updateAirportDropdowns();
  } else if (kind === "target") {
    env.targets.splice(index, 1);
  } else if (kind === "sam") {
    env.sams.splice(index, 1);
    // Recalculate wrapping after SAM deletion
    updateSamWrappingClientSide();
  }

  state.selectedObject = null;
  appendDebugLine(`Deleted ${kind}.`);
  if (kind === "airport") populateTankerTable();
  drawEnvironment();
}

/**
 * Export environment to JSON file
 * Two modes based on radio button selection:
 * - Mission: exports segments with env + cut distances (no solutions), filename _Nx_
 * - Environment: exports flat env with all targets combined, filename _N1_
 */
// Export counter for unique filenames
let _exportCounter = 1;

/**
 * Generate export filename with timestamp: prefix_YYYYMMDD-HHMM_N#_#.json
 */
function generateExportFilename(prefix, segmentCount) {
  const now = new Date();
  const yyyy = String(now.getFullYear());
  const mo = String(now.getMonth() + 1).padStart(2, "0");
  const dd = String(now.getDate()).padStart(2, "0");
  const hh = String(now.getHours()).padStart(2, "0");
  const mm = String(now.getMinutes()).padStart(2, "0");
  const filename = `${prefix}_${yyyy}${mo}${dd}-${hh}${mm}_N${segmentCount}_${_exportCounter}.json`;
  _exportCounter++;
  return filename;
}

/**
 * Download JSON data as a file
 */
function downloadJsonFile(data, filename) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/**
 * Build combined environment from segments (merges targets, sams, airports)
 */
function buildCombinedEnv(segments) {
  const envSnapshot = JSON.parse(JSON.stringify(state.env));
  envSnapshot.drone_configs = JSON.parse(JSON.stringify(state.droneConfigs || envSnapshot.drone_configs || {}));

  let combinedEnv = JSON.parse(JSON.stringify(envSnapshot));
  if (segments && segments.length > 0) {
    const allTargets = new Map();
    const allSams = new Map();
    const allAirports = new Map();

    segments.forEach(seg => {
      if (!seg.env) return;
      (seg.env.targets || []).forEach(t => allTargets.set(t.id, t));
      (seg.env.sams || []).forEach((s) => {
        const c = s.center || {};
        const key = s.id || `sam_${c.lat}_${c.lng}`;
        allSams.set(key, s);
      });
      (seg.env.airports || []).forEach(a => allAirports.set(a.id, a));
    });

    combinedEnv.targets = Array.from(allTargets.values());
    combinedEnv.sams = Array.from(allSams.values());
    combinedEnv.airports = Array.from(allAirports.values());
  }

  // Force current drone_configs
  combinedEnv.drone_configs = JSON.parse(JSON.stringify(state.droneConfigs || {}));
  return combinedEnv;
}

/**
 * Export mission using segmentStore (clean, simple format)
 * @returns {boolean} True if export succeeded
 */
function exportSegmentStore() {
  if (segmentStore.count() === 0) {
    appendDebugLine(`⚠️ [SegmentStore] Cannot export - no segments`);
    return false;
  }

  try {
    const segCount = segmentStore.count();
    const filename = generateExportFilename('isr_seg', segCount);
    downloadJsonFile(segmentStore.toJSON(), filename);
    appendDebugLine(`✅ [SegmentStore] Exported ${segCount} segments as ${filename}`);
    return true;
  } catch (err) {
    console.error('[SegmentStore] Export failed:', err);
    appendDebugLine(`❌ [SegmentStore] Export failed: ${err.message}`);
    return false;
  }
}

/**
 * Export environment-only file (flat, no segments).
 * Called when Mission export is selected to also export environment.
 */
function exportEnvironmentFile() {
  try {
    const segments = segmentStore.count() > 0 ? segmentStore.getSegments() : [];
    const combinedEnv = buildCombinedEnv(segments);

    const exportObj = {
      schema: "isr_env_v1",
      is_segmented: false,
      segment_count: 1,
      env: combinedEnv,
      time_sync: readTimeSyncOptionsFromUI(),
      map_view: {
        bounds: { ...mapState.bounds },
        center: { lat: viewState.centerLat, lng: viewState.centerLng },
        zoom: viewState.zoomLevel,
      },
    };

    const filename = generateExportFilename('isr_env', 1);
    downloadJsonFile(exportObj, filename);
    appendDebugLine(`✅ Exported environment as ${filename}`);
    return true;
  } catch (err) {
    console.error('Environment export failed:', err);
    appendDebugLine(`❌ Environment export failed: ${err.message}`);
    return false;
  }
}


/**
 * Recursively delete every `start_fuel` key in an object/array tree. Used to
 * scrub the transient replan fuel from an export payload (it appears under both
 * top-level drone_configs and env.drone_configs).
 */
function stripStartFuelDeep(obj) {
  if (Array.isArray(obj)) {
    obj.forEach(stripStartFuelDeep);
  } else if (obj && typeof obj === "object") {
    if ("start_fuel" in obj) delete obj.start_fuel;
    Object.values(obj).forEach(stripStartFuelDeep);
  }
}

async function exportEnvironment(kind = "env") {
  try {
    if (!state.env) {
      alert("No environment loaded.");
      return;
    }

    // "mission" = the Export mission button; else the environment.
    const isMissionExport = kind === "mission";

    appendDebugLine(`📤 Exporting as ${isMissionExport ? "Mission" : "Environment"}...`);

    // SEGMENT STORE EXPORT: If we have segments in segmentStore, use that (simplest format)
    // When exporting Mission, we ALSO export the Environment as a separate file
    if (isMissionExport && segmentStore.count() > 0) {
      const missionExported = exportSegmentStore();
      if (missionExported) {
        // Also export environment as a separate file
        appendDebugLine(`📤 Also exporting Environment file...`);
        exportEnvironmentFile();
        return;
      }
      appendDebugLine(`⚠️ SegmentStore export failed, using legacy format...`);
    }

    // Check if MissionReplay has segments (legacy format)
    const segments = (typeof missionReplay !== "undefined" && missionReplay.getSegmentCount)
      ? missionReplay.getSegments()
      : [];

    // Build export object
    let exportObj;
    let segmentCount;

    if (isMissionExport && segments && segments.length > 1) {
      // MISSION export - export segments with env, cut distances, cut positions (NO solutions)
      segmentCount = segments.length;
      const seg0Configs = segments[0]?.drone_configs || state.droneConfigs || {};

      exportObj = {
        schema: "isr_env_v1",
        is_segmented: true,
        segment_count: segmentCount,
        drone_configs: JSON.parse(JSON.stringify(seg0Configs)),
        time_sync: readTimeSyncOptionsFromUI(),
        map_view: {
          bounds: { ...mapState.bounds },
          center: { lat: viewState.centerLat, lng: viewState.centerLng },
          zoom: viewState.zoomLevel,
        },
        segments: segments.map(s => {
          const segDroneConfigs = s.drone_configs || state.droneConfigs || {};
          return {
            index: s.index,
            env: s.env,
            cutDistance: s.cutDistance || null,
            cutPositions: s.cutPositions || null,
            lostDrones: s.lostDrones || null,
            drone_configs: JSON.parse(JSON.stringify(segDroneConfigs)),
          };
        }),
      };
      appendDebugLine(`   ${segmentCount} segments with cut distances and positions (no solutions)`);
    } else {
      // ENVIRONMENT export - combine all targets into flat env, no segments
      segmentCount = 1;
      const combinedEnv = buildCombinedEnv(segments);

      exportObj = {
        schema: "isr_env_v1",
        is_segmented: false,
        segment_count: 1,
        env: combinedEnv,
        drone_configs: JSON.parse(JSON.stringify(state.droneConfigs || {})),
        time_sync: readTimeSyncOptionsFromUI(),
        map_view: {
          bounds: { ...mapState.bounds },
          center: { lat: viewState.centerLat, lng: viewState.centerLng },
          zoom: viewState.zoomLevel,
        },
      };
    }

    // §11.10: NEVER export the transient replan `start_fuel`. It's runtime-only
    // (reduced leg-1 fuel after a cut); baking it into a saved env starves leg 1
    // on re-import and makes the solver drop targets that actually fit. Strip it
    // from every drone config in the export payload (all formats), recursively.
    stripStartFuelDeep(exportObj);

    // Cruise altitude + climb angle travel inside each drone config.

    const prefix = segmentCount > 1 ? 'isr_seg' : 'isr_env';
    const filename = generateExportFilename(prefix, segmentCount);
    downloadJsonFile(exportObj, filename);
    appendDebugLine(`✅ Exported as ${filename}`);
  } catch (err) {
    appendDebugLine(`❌ Export error: ${err.message}`);
  }
}

// canvas -> lat/lng helper for editing
// Inverse of w2c(): convert canvas pixel coordinates to lat/lng
function canvasToLatLng(canvas, clientX, clientY) {
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  const cx = (clientX - rect.left) * scaleX;
  const cy = (clientY - rect.top) * scaleY;

  const scale = 256 * Math.pow(2, viewState.zoomLevel);
  const center = mercatorPoint(viewState.centerLat, viewState.centerLng, viewState.zoomLevel);
  const x = center[0] + (cx - canvas.width / 2) / scaleX;
  const y = center[1] + (cy - canvas.height / 2) / scaleY;
  // Blank space beyond the only displayed world is not an editable location.
  // Do not wrap a click there into an airport on the opposite side of the map.
  if (x < 0 || x > scale || y < 0 || y > scale) return null;
  const lng = x / scale * 360 - 180;
  const lat = mercatorLat(y, viewState.zoomLevel);

  return { lat, lng };
}

// For backward compatibility (used in several places)
function canvasToWorld(canvas, clientX, clientY) {
  const pos = canvasToLatLng(canvas, clientX, clientY);
  return pos;
}

// hit-test on env (airport/target/sam) using pixel distance
// cx, cy are canvas pixel coordinates
function hitTestPixel(cx, cy) {
  if (!state.env) return null;
  // Single source of truth for the edit UI: hit-test the SAME env the writers
  // (add/move/delete) mutate. During a live edit that is the staged env; otherwise
  // getEditableEnv() falls back to state.env (no change outside live edit). This
  // makes newly-added staged objects selectable/draggable, and keeps the returned
  // index consistent with the array the drag/delete handlers mutate.
  const _env = getEditableEnv() || state.env;
  const airports = _env.airports || [];
  const targets = _env.targets || [];
  const sams = _env.sams || [];
  const R = 12; // threshold in pixels

  function dist2(ax, ay, bx, by) {
    const dx = ax - bx;
    const dy = ay - by;
    return dx * dx + dy * dy;
  }

  function toCanvas(lat, lng) {
    return mapPointToCanvas($("env-canvas"), lat, lng);
  }

  let best = null;
  let bestD2 = Infinity;

  airports.forEach((a, idx) => {
    if (a.lat === undefined || a.lng === undefined) return;
    const [ax, ay] = toCanvas(a.lat, a.lng);
    const d2 = dist2(cx, cy, ax, ay);
    if (d2 < bestD2 && d2 < R * R) {
      bestD2 = d2;
      best = { kind: "airport", index: idx };
    }
  });

  targets.forEach((t, idx) => {
    if (t.lat === undefined || t.lng === undefined) return;
    const [tx, ty] = toCanvas(t.lat, t.lng);
    const d2 = dist2(cx, cy, tx, ty);
    if (d2 < bestD2 && d2 < R * R) {
      bestD2 = d2;
      best = { kind: "target", index: idx };
    }
  });

  sams.forEach((s, idx) => {
    const center = s.center;
    if (!center || center.lat === undefined || center.lng === undefined) return;
    const [sx, sy] = toCanvas(center.lat, center.lng);
    const d2 = dist2(cx, cy, sx, sy);
    if (d2 < bestD2 && d2 < R * R) {
      bestD2 = d2;
      best = { kind: "sam", index: idx };
    }
  });

  return best;
}

function attachCanvasEditing() {
  const canvas = $("env-canvas");
  if (!canvas) return;

  // Helper to get canvas pixel coords from event
  function getCanvasCoords(evt) {
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    return [(evt.clientX - rect.left) * scaleX, (evt.clientY - rect.top) * scaleY];
  }

  canvas.addEventListener("mousedown", (evt) => {
    if (!state.env || !state.editMode) return;

    const pos = canvasToLatLng(canvas, evt.clientX, evt.clientY);
    if (!pos) return;
    const [cx, cy] = getCanvasCoords(evt);

    // If we are in "add mode", create an object here
    if (state.addMode) {
      if (state.addMode === "airport") {
        const env = getEditableEnv();
        const airports = env.airports || (env.airports = []);

        // Find max airport number across current env AND all segmented mission segments
        // This prevents ID collisions when merging edits forward
        let maxAirportNum = 0;

        // Check current env airports
        airports.forEach((a) => {
          const m = String(a.id).match(/^A(\d+)$/i);
          if (m) maxAirportNum = Math.max(maxAirportNum, parseInt(m[1], 10));
        });

        // Check all segments in segmented mission (if any)
        if (missionState.segmentedMission && missionState.segmentedMission.segments) {
          missionState.segmentedMission.segments.forEach((seg) => {
            (seg.env?.airports || []).forEach((a) => {
              const m = String(a.id).match(/^A(\d+)$/i);
              if (m) maxAirportNum = Math.max(maxAirportNum, parseInt(m[1], 10));
            });
          });
        }

        const nextNum = maxAirportNum + 1;
        // Read runway type from Edit-panel dropdown (default L if missing).
        const runwaySel = $("new-airport-runway");
        const runway = runwaySel ? runwaySel.value : "L";
        airports.push({ id: `A${nextNum}`, lat: pos.lat, lng: pos.lng, runway });
        appendDebugLine(`Added Airport A${nextNum} (${runway}) at (${pos.lat.toFixed(5)}, ${pos.lng.toFixed(5)})`);
        updateAirportDropdowns();
      } else if (state.addMode === "tanker") {
        // A TANKER is a mid-air refuel point: lives in the airports list with
        // type:"tanker", no runway. It can be a refuel STOP but never a drone
        // start/end. Id TKn, numbered independently of airports.
        const env = getEditableEnv();
        const airports = env.airports || (env.airports = []);
        let maxTk = 0;
        const scanTk = (list) => (list || []).forEach((a) => {
          const m = String(a.id).match(/^TK(\d+)$/i);
          if (m) maxTk = Math.max(maxTk, parseInt(m[1], 10));
        });
        scanTk(airports);
        if (missionState.segmentedMission && missionState.segmentedMission.segments) {
          missionState.segmentedMission.segments.forEach((seg) => scanTk(seg.env?.airports));
        }
        const tkNum = maxTk + 1;
        // A tanker holds the altitude it is given from the moment it exists —
        // anything drawn right after (refuel chips: which drones can reach it)
        // must never see it without one.
        airports.push({
          id: `TK${tkNum}`, lat: pos.lat, lng: pos.lng, type: "tanker", runway: null,
          alt_km: newTankerAltitudeKm(),
        });
        appendDebugLine(`Added Tanker TK${tkNum} at (${pos.lat.toFixed(5)}, ${pos.lng.toFixed(5)})`);
        updateAirportDropdowns();
      } else if (state.addMode === "target") {
        const env = getEditableEnv();
        const targets = env.targets || (env.targets = []);

        // Find max target number across current env AND all segmented mission segments
        // This prevents ID collisions when merging edits forward
        let maxTargetNum = 0;

        // Check current env targets
        targets.forEach((t) => {
          const m = String(t.id).match(/^T(\d+)$/i);
          if (m) maxTargetNum = Math.max(maxTargetNum, parseInt(m[1], 10));
        });

        // Check all segments in segmented mission (if any)
        if (missionState.segmentedMission && missionState.segmentedMission.segments) {
          missionState.segmentedMission.segments.forEach((seg) => {
            (seg.env?.targets || []).forEach((t) => {
              const m = String(t.id).match(/^T(\d+)$/i);
              if (m) maxTargetNum = Math.max(maxTargetNum, parseInt(m[1], 10));
            });
          });
        }

        const nextNum = maxTargetNum + 1;

        // Read type and priority from dropdowns
        const typeSelect = $("new-target-type");
        const prioritySelect = $("new-target-priority");
        const targetType = typeSelect ? typeSelect.value : "a";
        const targetPriority = prioritySelect ? parseInt(prioritySelect.value, 10) : 5;

        targets.push({
          id: `T${nextNum}`,
          lat: pos.lat,
          lng: pos.lng,
          priority: targetPriority,
          type: targetType,
        });
        appendDebugLine(`Added Target T${nextNum} (type=${targetType.toUpperCase()}, priority=${targetPriority}) at (${pos.lat.toFixed(5)}, ${pos.lng.toFixed(5)})`);
        updateTimeSyncMemberUI();
      } else if (state.addMode === "sam") {
        const env = getEditableEnv();
        const sams = env.sams || (env.sams = []);

        // Read range from dropdown (now in km, default 5 km)
        const rangeSelect = $("new-sam-range");
        const samRangeKm = rangeSelect ? parseFloat(rangeSelect.value) : 5.0;

        sams.push({
          center: { lat: pos.lat, lng: pos.lng },
          range_km: samRangeKm,
        });
        appendDebugLine(`Added SAM (range=${samRangeKm} km) at (${pos.lat.toFixed(5)}, ${pos.lng.toFixed(5)})`);

        // Calculate wrapping immediately (client-side) and draw
        fillMissingAltitudes(env);
        updateSamWrappingClientSide();
        drawEnvironment();
        return;
      }
      fillMissingAltitudes(getEditableEnv());  // new airport / tanker / target
      drawEnvironment();
      return;
    }

    // Otherwise: hit-test and start dragging if something is hit
    const hit = hitTestPixel(cx, cy);
    state.selectedObject = hit;
    state.dragging = null;

    if (hit) {
      appendDebugLine(`Selected ${hit.kind.toUpperCase()} index ${hit.index}`);
      // Start dragging
      state.dragging = {
        kind: hit.kind,
        index: hit.index,
        offsetX: 0,
        offsetY: 0,
      };
    }

    drawEnvironment();
  });

  canvas.addEventListener("mousemove", (evt) => {
    if (!state.env || !state.editMode) return;
    if (!state.dragging) return;

    const pos = canvasToLatLng(canvas, evt.clientX, evt.clientY);
    if (!pos) return;
    const { kind, index } = state.dragging;

    const env = getEditableEnv();
    if (kind === "airport") {
      const airports = env.airports || [];
      if (airports[index]) {
        airports[index].lat = pos.lat;
        airports[index].lng = pos.lng;
        // Ground moved: re-sampled on drop. A tanker keeps its given altitude;
        // the ground under it is re-sampled.
        if (airports[index].type !== "tanker") delete airports[index].alt_km;
        else delete airports[index].ground_km;
      }
    } else if (kind === "target") {
      const targets = env.targets || [];
      if (targets[index]) {
        targets[index].lat = pos.lat;
        targets[index].lng = pos.lng;
        delete targets[index].alt_km;  // re-sampled on drop
      }
    } else if (kind === "sam") {
      const sams = env.sams || [];
      if (sams[index]) {
        sams[index].center = { lat: pos.lat, lng: pos.lng };
        delete sams[index].alt_km;  // re-sampled on drop
        // Update wrapping instantly using client-side calculation
        updateSamWrappingClientSide();
      }
    }

    drawEnvironment();
  });

  // Use window-level mouseup so we ALWAYS end drag, even if the mouse
  // leaves the canvas or some other UI element grabs focus.
  window.addEventListener("mouseup", () => {
    // Always end drag
    const wasDragging = state.dragging !== null;
    state.dragging = null;

    // Redraw if we were dragging something (wrapping already updated client-side during drag)
    if (wasDragging) {
      fillMissingAltitudes(getEditableEnv());  // ground elevation at the new spot
      drawEnvironment();
    }
  });

  // If the window loses focus (e.g. you alt-tab), make sure we are not dragging.
  window.addEventListener("blur", () => {
    state.dragging = null;
  });

  // Note: We intentionally do NOT cancel dragging on mouseleave.
  // The window-level mouseup handler will properly end the drag
  // even if the mouse leaves the canvas, allowing wrapping to update.
}

// ----------------------------------------------------
// Zoom and Pan event handlers
// Uses lat/lng-based viewport - works identically in Grid and Map modes
// Mac trackpad: pinch-to-zoom, two-finger scroll to pan, click-drag to pan
// ----------------------------------------------------
function attachZoomPanHandlers() {
  const canvas = $("env-canvas");
  if (!canvas) return;

  // Helper: get degrees per pixel at current zoom level
  function getDegreesPerPixel() {
    const scale = 256 * Math.pow(2, viewState.zoomLevel);
    return 360 / scale;
  }

  // Trackpad/wheel handler - handles both zoom and pan
  // Mac trackpad: pinch = wheel + ctrlKey, two-finger scroll = wheel without ctrlKey
  canvas.addEventListener("wheel", (evt) => {
    // In Map mode, let Leaflet handle all gestures
    if (mapState.active) return;

    evt.preventDefault();

    // Pinch-to-zoom (ctrlKey on Mac trackpad) or Cmd+scroll
    if (evt.ctrlKey || evt.metaKey) {
      // Get cursor position in lat/lng BEFORE zoom change
      const cursor = canvasToLatLng(canvas, evt.clientX, evt.clientY);
      if (!cursor) return;

      // Smooth zoom: use deltaY magnitude for finer control
      const zoomDelta = -evt.deltaY * 0.01;  // Smaller factor for smooth pinch
      const newZoom = Math.max(viewState.minZoom, Math.min(viewState.maxZoom, viewState.zoomLevel + zoomDelta));

      if (Math.abs(newZoom - viewState.zoomLevel) > 0.01) {
        // Zoom towards cursor: adjust center so cursor position stays fixed
        const before = mercatorPoint(cursor.lat, cursor.lng, viewState.zoomLevel);
        const center = mercatorPoint(viewState.centerLat, viewState.centerLng, viewState.zoomLevel);
        const after = mercatorPoint(cursor.lat, cursor.lng, newZoom);
        viewState.centerLat = mercatorLat(after[1] - (before[1] - center[1]), newZoom);
        viewState.centerLng = (after[0] - (before[0] - center[0])) / (256 * Math.pow(2, newZoom)) * 360 - 180;
        viewState.zoomLevel = newZoom;
        constrainMapView();

        updateZoomSlider();
        drawEnvironment();
        saveViewToLocalStorage();
      }
    } else {
      // Two-finger scroll = pan
      const degPerPx = getDegreesPerPixel();
      const deltaLng = evt.deltaX * degPerPx;
      const center = mercatorPoint(viewState.centerLat, viewState.centerLng, viewState.zoomLevel);
      viewState.centerLng += deltaLng;
      viewState.centerLat = mercatorLat(center[1] + evt.deltaY, viewState.zoomLevel);
      constrainMapView();

      drawEnvironment();
      saveViewToLocalStorage();
    }
  }, { passive: false });

  // Click-drag for panning (works with trackpad click-and-drag)
  // Left-click when not editing, or middle/right-click always
  canvas.addEventListener("mousedown", (evt) => {
    // In Map mode, Leaflet handles panning
    if (mapState.active) return;

    // Left button (0) for panning when not editing, or middle/right button always
    const isLeftClick = evt.button === 0;
    const isMiddleOrRight = evt.button === 1 || evt.button === 2;

    // Allow left-click pan only when not in edit mode and not adding objects
    if (isLeftClick && (state.editMode || state.addMode)) return;

    if (isLeftClick || isMiddleOrRight) {
      evt.preventDefault();
      viewState.isPanning = true;
      viewState.panStartX = evt.clientX;
      viewState.panStartY = evt.clientY;
      viewState.panStartLat = viewState.centerLat;
      viewState.panStartLng = viewState.centerLng;
      canvas.style.cursor = "grabbing";
    }
  });

  // Prevent context menu on right-click (for panning)
  canvas.addEventListener("contextmenu", (evt) => {
    if (!mapState.active) {
      evt.preventDefault();
    }
  });

  window.addEventListener("mousemove", (evt) => {
    if (!viewState.isPanning) return;
    if (mapState.active) return;  // In Map mode, Leaflet handles panning

    const canvas = $("env-canvas");
    if (!canvas) return;

    // Calculate pixel delta
    const deltaClientX = evt.clientX - viewState.panStartX;
    const deltaClientY = evt.clientY - viewState.panStartY;

    // Convert pixel delta to lat/lng delta
    const degPerPx = getDegreesPerPixel();
    const deltaLng = -deltaClientX * degPerPx;
    const start = mercatorPoint(viewState.panStartLat, viewState.panStartLng, viewState.zoomLevel);
    viewState.centerLng = viewState.panStartLng + deltaLng;
    viewState.centerLat = mercatorLat(start[1] - deltaClientY, viewState.zoomLevel);
    constrainMapView();

    drawEnvironment();
  });

  window.addEventListener("mouseup", () => {
    if (viewState.isPanning) {
      viewState.isPanning = false;
      saveViewToLocalStorage();
      const canvas = $("env-canvas");
      if (canvas) canvas.style.cursor = "";
    }
  });

  // Double-click to reset view
  canvas.addEventListener("dblclick", () => {
    // Only reset if not in edit mode or no object is being added
    if (!state.editMode && !state.addMode) {
      resetView();
    }
  });
}

// Sync Leaflet map view to match viewState (center lat/lng + zoom level)
function syncLeafletToViewState() {
  if (!mapState.active || !mapState.leafletMap) return;
  constrainMapView();

  // Keep every listener attached: Leaflet's tiles, bounds constraint and the
  // weather area observer all depend on these same move/zoom events. The
  // FROM-sync only writes back when the world constraint changes the center,
  // so setting an ordinary view does not create a feedback loop.
  mapState.leafletMap.setView(
    [viewState.centerLat, viewState.centerLng],
    viewState.zoomLevel,
    { animate: false }
  );

  // Read back the actual view, including Leaflet's zoom snapping and rounding.
  syncViewStateFromLeaflet();
  updateZoomSlider();
}

// ----------------------------------------------------
// Drone config + sequence bar
// ----------------------------------------------------

// Update airport dropdowns when airports are added/deleted
function updateAirportDropdowns() {
  const airports = (state.env && state.env.airports) || [];
  // Tankers are mid-air refuel points — NOT valid drone start/end airports, so
  // exclude them from these dropdowns. (The Refuel Stops grid includes them.)
  const airportIds = airports
    .filter((a) => a.type !== "tanker")
    .map((a) => String(a.id));
  // Runway lookup (legacy airports without `runway` default to L = most permissive).
  const airportRunway = new Map(
    airports.map((a) => [String(a.id), (a.runway || "L").toUpperCase()])
  );
  const labelFor = (aid) => `${aid} (${airportRunway.get(aid) || "L"})`;

  for (let did = 1; did <= 5; did++) {
    const cfg = state.droneConfigs[String(did)] || {};
    const droneRwy = cfg.runway || "S";
    const compatibleIds = airportIds.filter((aid) =>
      isAirportCompatibleWithDrone(airportRunway.get(aid), droneRwy)
    );

    const startSel = $(`cfg-d${did}-start`);
    const endSel = $(`cfg-d${did}-end`);
    const refuelSel = $(`cfg-d${did}-refuel`);

    // Store current selections. SOURCE OF TRUTH is state.droneConfigs, NOT the
    // dropdown — during env load the dropdown can be empty/just-cleared, so reading
    // refuelSel.value would lose a real "-" (Any) and silently show "None" while the
    // config still carries "-" → the solver then refuels even though the UI says
    // None. Prefer the config value; fall back to the dropdown only if unset.
    const _cfgForDropdown = state.droneConfigs[String(did)] || {};
    const currentStart = _cfgForDropdown.start_airport ?? (startSel ? startSel.value : null);
    const currentEnd = _cfgForDropdown.end_airport ?? (endSel ? endSel.value : null);
    const currentRefuel = _cfgForDropdown.refuel_airport ?? (refuelSel ? refuelSel.value : null);

    // Repopulate start dropdown (compatible airports only)
    if (startSel) {
      startSel.innerHTML = "";
      compatibleIds.forEach((aid) => {
        const opt = document.createElement("option");
        opt.value = aid;
        opt.textContent = labelFor(aid);
        startSel.appendChild(opt);
      });
    }

    // Repopulate end dropdown WITH flexible "-" option (compatible airports only)
    if (endSel) {
      endSel.innerHTML = "";
      const flexOpt = document.createElement("option");
      flexOpt.value = "-";
      flexOpt.textContent = "Any";
      endSel.appendChild(flexOpt);
      compatibleIds.forEach((aid) => {
        const opt = document.createElement("option");
        opt.value = aid;
        opt.textContent = labelFor(aid);
        endSel.appendChild(opt);
      });
    }

    // Repopulate refuel dropdown: None, Any, then compatible airports
    if (refuelSel) {
      refuelSel.innerHTML = "";
      const noneOpt = document.createElement("option");
      noneOpt.value = "";
      noneOpt.textContent = "None";
      refuelSel.appendChild(noneOpt);
      const anyOpt = document.createElement("option");
      anyOpt.value = "-";
      anyOpt.textContent = "Any";
      refuelSel.appendChild(anyOpt);
      compatibleIds.forEach((aid) => {
        const opt = document.createElement("option");
        opt.value = aid;
        opt.textContent = labelFor(aid);
        refuelSel.appendChild(opt);
      });
    }

    // Restore selections if still compatible, otherwise fall back sensibly
    if (startSel) {
      if (compatibleIds.includes(currentStart)) {
        startSel.value = currentStart;
      } else if (compatibleIds.length > 0) {
        startSel.value = compatibleIds[0];
        if (state.droneConfigs[String(did)]) {
          state.droneConfigs[String(did)].start_airport = compatibleIds[0];
        }
      }
    }

    if (endSel) {
      if (currentEnd === "-" || compatibleIds.includes(currentEnd)) {
        endSel.value = currentEnd;
      } else {
        // Previous end no longer compatible — fall back to flexible "-"
        endSel.value = "-";
        if (state.droneConfigs[String(did)]) {
          state.droneConfigs[String(did)].end_airport = "-";
        }
      }
    }

    if (refuelSel) {
      if (currentRefuel === "" || currentRefuel === "-" || compatibleIds.includes(currentRefuel)) {
        refuelSel.value = currentRefuel;
      } else {
        refuelSel.value = "";
        if (state.droneConfigs[String(did)]) {
          state.droneConfigs[String(did)].refuel_airport = "";
        }
      }
    }
  }
  // Airports changed → rebuild the multi-stop refuel grid's options too.
  if (typeof populateRefuelStopsBox === "function") populateRefuelStopsBox();
}

/**
 * Runway compatibility. Airports of length R can accept drones whose minimum
 * runway requirement is R or smaller. Ordering: S < M < L.
 * Returns true if a drone with `droneRwy` can use an airport with `airportRwy`.
 */
const RUNWAY_RANK = { S: 1, M: 2, L: 3 };
function isAirportCompatibleWithDrone(airportRwy, droneRwy) {
  const a = RUNWAY_RANK[(airportRwy || "L").toUpperCase()] || RUNWAY_RANK.L;
  const d = RUNWAY_RANK[(droneRwy || "S").toUpperCase()] || RUNWAY_RANK.S;
  return a >= d;
}

// Per-drone cruise altitude (table + drawer) and climb angle boxes.
function setDroneAltitudeInputs(did, cfg) {
  [$(`cfg-d${did}-alt`), $(`cfg-d${did}-alt2`)].forEach((input) => {
    if (input) input.value = cfg?.cruise_altitude_km ?? 0;
  });
  const climbInput = $(`cfg-d${did}-climb`);
  if (climbInput) climbInput.value = cfg?.climb_angle_deg ?? DEFAULT_CLIMB_ANGLE_DEG;
  const descentInput = $(`cfg-d${did}-descent`);
  if (descentInput) descentInput.value = cfg?.descent_angle_deg ?? cfg?.climb_angle_deg ?? DEFAULT_CLIMB_ANGLE_DEG;
}

// Asset tab: ▸ opens/closes a drone's drawer; several may be open; "Collapse
// all" closes them. A disabled drone's row is dimmed and doesn't open.
function attachFleetDrawers() {
  return fleetDrawers.attachFleetDrawers();
}

function setDroneDrawer(did, open) {
  return fleetDrawers.setDroneDrawer(did, open);
}

// Write one drone's config into its Asset-tab boxes (fleet row + drawer).
// Loiter / detection grids are filled by updateLoiterTabUI /
// updateDetectionRadiusTabUI from the same config.
function showDroneConfig(did, cfg) {
  renderCategoryRows();
  if (!cfg) return;
  const cbEnabled = $(`cfg-d${did}-enabled`);
  const fuelInput = $(`cfg-d${did}-fuel`);
  const startSel = $(`cfg-d${did}-start`);
  const endSel = $(`cfg-d${did}-end`);
  const refuelSel = $(`cfg-d${did}-refuel`);
  // A list only takes a value it offers: after a cut the start is a cut node
  // (C1-1) that is not in the Start list — setting it would blank the box and
  // the next read would lose the start.
  const setSelect = (sel, v) => {
    if (sel && v != null && [...sel.options].some((o) => o.value === String(v))) sel.value = String(v);
  };
  if (cbEnabled) cbEnabled.checked = cfg.enabled !== false;
  if (fuelInput) fuelInput.value = cfg.fuel_budget || cfg.fuel_capacity || 150;
  setSelect(startSel, cfg.start_airport);
  setSelect(endSel, cfg.end_airport);
  setSelect(refuelSel, cfg.refuel_airport);
  showSpeedKmh($(`cfg-d${did}-speed`), cfg.speed);
  const runwaySel = $(`cfg-d${did}-runway`);
  if (runwaySel) runwaySel.value = cfg.runway || "S";
  setDroneAltitudeInputs(did, cfg);
  categoryIds().forEach((t) => {
    const cb = $(`cfg-d${did}-type-${t}`);
    if (cb && cfg.target_access && cfg.target_access[t] !== undefined) cb.checked = cfg.target_access[t];
  });
  const platformSel = $(`cfg-d${did}-platform`);
  if (platformSel) platformSel.value = cfg.platform_id || "";
  renderSensorChips(did);
  markEditedFields(did);
}

// ----------------------------------------------------
// Platform / sensor catalog (catalog/asset_catalog.json, nominal values).
// Picking a Type (and its sensors) fills the drone's fields — the rule lives
// on the server (server/asset_catalog.py drone_fields); every filled value
// stays editable, and a value that differs from the preset is marked.
// ----------------------------------------------------
async function loadAssetCatalog() {
  try {
    const resp = await fetch("/api/asset_catalog");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    state.assetCatalog = await resp.json();
  } catch (err) {
    state.assetCatalog = null;
    appendDebugLine(`⚠️ Asset catalog not loaded (${err.message}) — Type stays Custom`);
    return;
  }
  const platforms = state.assetCatalog.platforms || [];
  for (let did = 1; did <= 5; did++) {
    const sel = $(`cfg-d${did}-platform`);
    if (!sel) continue;
    sel.innerHTML = '<option value="">Custom</option>' + platforms
      .map((p) => `<option value="${escapeHtml(p.id)}" title="${escapeHtml(p.name || p.id)} (nominal)">${escapeHtml(p.id)}</option>`)
      .join("");
    sel.value = state.droneConfigs[String(did)]?.platform_id || "";
    renderSensorChips(did);
  }
  refreshCategoryDisplays();  // the catalog's categories and sensor sources
}

function catalogPlatform(platformId) {
  return (state.assetCatalog?.platforms || []).find((p) => p.id === platformId) || null;
}

function renderSensorChips(did) {
  const box = $(`sensor-chips-d${did}`);
  if (!box) return;
  const cfg = getEditableDroneConfigs()[String(did)] || {};
  const platform = catalogPlatform(cfg.platform_id);
  if (!platform) {
    box.innerHTML = '<span class="fleet-hint">Pick a Type to choose its sensors</span>';
    return;
  }
  const sensors = state.assetCatalog.sensors || [];
  const onBoard = new Set(cfg.sensor_ids || []);
  box.innerHTML = (platform.sensors || []).map((sid) => {
    const s = sensors.find((x) => x.id === sid) || { id: sid };
    return `<button type="button" class="sensor-chip" data-sensor="${escapeHtml(sid)}" aria-pressed="${onBoard.has(sid)}" title="${escapeHtml(s.name || sid)} (nominal)">${escapeHtml(sid)}</button>`;
  }).join("");
  if (!onBoard.size) {
    box.insertAdjacentHTML("beforeend",
      `<span class="sensor-none">No sensor on: D${did} can't observe any target type</span>`);
  }
  box.querySelectorAll(".sensor-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const now = new Set(cfg.sensor_ids || []);
      if (now.has(chip.dataset.sensor)) now.delete(chip.dataset.sensor); else now.add(chip.dataset.sensor);
      applyAssetPreset(did, cfg.platform_id, (platform.sensors || []).filter((x) => now.has(x)), { sensorsOnly: true });
    });
  });
}

// The fields a preset fills, and how to read each back from a config.
const PRESET_FIELDS = ["cruise_altitude_km", "climb_angle_deg", "descent_angle_deg", "speed", "fuel_budget", "runway"];
const PRESET_TYPE_FIELDS = ["target_access", "detection_radius", "loiter_times"];

// A category missing from one side (added after the preset) counts as off / 0.
function samePerCategory(field, a, b) {
  if (field === "target_access") return !!a === !!b;
  return Math.abs(Number(a || 0) - Number(b || 0)) < 1e-6;
}

function droneEditedFromPreset(cfg) {
  const p = cfg?.preset;
  if (!p) return false;
  const same = (a, b) => (typeof a === "number" || typeof b === "number")
    ? Math.abs(Number(a) - Number(b)) < 1e-6 : a === b;
  return PRESET_FIELDS.some((k) => !same(cfg[k], p[k])) ||
    PRESET_TYPE_FIELDS.some((k) => categoryIds().some((t) => !samePerCategory(k, (cfg[k] || {})[t], (p[k] || {})[t])));
}

// What the sensors decide (per target type); the rest comes from the aircraft.
const SENSOR_FIELDS = ["sensor_ids", "target_access", "detection_radius", "loiter_times", "loiter_enabled"];

// Apply a catalog Type + sensors to a drone: ask the server for the fields,
// fill them, and remember them as the preset (for the "edited" marks).
// sensorsOnly (a sensor chip was toggled): only the sensor-decided fields
// change — altitude, speed, fuel, angles and runway keep any edits.
async function applyAssetPreset(did, platformId, sensorIds, { sensorsOnly = false } = {}) {
  const idStr = String(did);
  const cfg = getEditableDroneConfigs()[idStr];
  if (!cfg) return;
  if (!platformId) {  // Custom: keep the values, drop the link to the catalog
    delete cfg.platform_id; delete cfg.sensor_ids; delete cfg.preset;
    showDroneConfig(did, cfg);
    invalidateMission(`Drone ${idStr} type set to Custom`);
    return;
  }
  const sensorEdited = () => {
    const p = cfg.preset || {};
    return ["target_access", "detection_radius", "loiter_times"].some((k) =>
      categoryIds().some((t) => !samePerCategory(k, (cfg[k] || {})[t], (p[k] || {})[t])));
  };
  if ((sensorsOnly ? sensorEdited() : droneEditedFromPreset(cfg)) && !confirm(sensorsOnly
    ? `D${idStr} has target-category values you edited. Replace them with the sensors' values?`
    : `D${idStr} has values you edited. Replace them with the ${platformId} preset?`)) {
    showDroneConfig(did, cfg);  // put the Type select back
    return;
  }
  try {
    const resp = await fetch("/api/asset_catalog/drone_fields", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ platform_id: platformId, sensor_ids: sensorIds, categories: categoryIds() }),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || `HTTP ${resp.status}`);
    const fields = await resp.json();
    if (sensorsOnly) {
      cfg.preset = cfg.preset || {};
      SENSOR_FIELDS.forEach((k) => {
        cfg[k] = JSON.parse(JSON.stringify(fields[k]));
        cfg.preset[k] = JSON.parse(JSON.stringify(fields[k]));
      });
    } else {
      Object.assign(cfg, JSON.parse(JSON.stringify(fields)));
      cfg.preset = JSON.parse(JSON.stringify(fields));
    }
  } catch (err) {
    appendDebugLine(`❌ D${idStr} ${platformId}: ${err.message}`);
    showDroneConfig(did, cfg);
    return;
  }
  showDroneConfig(did, cfg);
  updateLoiterTabUI();
  updateDetectionRadiusTabUI();
  populateRefuelStopsBox();
  updateSamWrappingClientSide();
  drawEnvironment();
  if (cesiumView.isActive()) cesiumView.render();
  appendDebugLine(`✈️ D${idStr} = ${platformId} with ${sensorIds.join(", ") || "no sensors"} (nominal values filled)`);
  invalidateMission(`Drone ${idStr} type ${platformId}`);
}

// Amber mark on every box whose value differs from the drone's preset.
// A drone's ceiling (km): from its settings, else its catalog aircraft.
function droneCeilingKm(cfg) {
  const c = Number(cfg?.ceiling_km ?? catalogPlatform(cfg?.platform_id)?.ceiling_km);
  return c > 0 ? c : null;
}

// Red on the Alt boxes when the altitude is above the aircraft's ceiling — a
// real problem, unlike the amber "edited" mark (it wins over it).
// The altitude boxes' limits. Red: above the aircraft's ceiling, or below the
// ground at its start/end airport (cruise is km above SEA LEVEL; a drone set to
// 1 km out of Denver, ground ~1.6 km, would fly underground). Amber: less than
// the terrain margin above that airport. 0 = "stay at airport height": no check.
// (The path between airports is not checked against terrain.)
function droneAltitudeProblem(cfg) {
  const alt = Number(cfg.cruise_altitude_km || 0);
  const ceiling = droneCeilingKm(cfg);
  if (ceiling != null && alt > ceiling + 1e-9) {
    return { level: "over-limit", text: `Above the ${cfg.platform_id || "aircraft"}'s ${ceiling} km ceiling` };
  }
  if (!(alt > 0)) return null;
  const airports = (getEditableEnv()?.airports || []).filter((a) => a.type !== "tanker");
  let high = null;
  [cfg.start_airport, cfg.end_airport].forEach((id) => {
    const a = airports.find((x) => String(x.id) === String(id));
    if (a && a.alt_km != null && (!high || Number(a.alt_km) > Number(high.alt_km))) high = a;
  });
  if (!high) return null;
  const ground = Number(high.alt_km);
  const fmt = (v) => (Math.round(v * 10) / 10).toFixed(1);
  if (alt < ground - 1e-9) {
    return { level: "over-limit", text: `Below the ground: ${high.id} is at ${fmt(ground)} km above sea level (cruise is above sea level)` };
  }
  if (alt < ground + TERRAIN_MARGIN_KM - 1e-9) {
    return { level: "low-clearance", text: `Only ${fmt(alt - ground)} km above ${high.id} (${fmt(ground)} km): under the ${TERRAIN_MARGIN_KM} km terrain margin` };
  }
  return null;
}

function markCeiling(did) {
  const cfg = getEditableDroneConfigs()[String(did)] || {};
  const problem = droneAltitudeProblem(cfg);
  [$(`cfg-d${did}-alt`), $(`cfg-d${did}-alt2`)].forEach((el) => {
    if (!el) return;
    el.classList.toggle("over-limit", problem?.level === "over-limit");
    el.classList.toggle("low-clearance", problem?.level === "low-clearance");
    // The warning replaces the box's tooltip while it lasts, then gives it back.
    if (problem) {
      if (el.dataset.altWarned !== "1") el.dataset.altTitle = el.title || "";
      el.dataset.altWarned = "1";
      el.title = problem.text;
    } else if (el.dataset.altWarned === "1") {
      el.dataset.altWarned = "";
      el.title = el.dataset.altTitle || "";
    }
  });
}

function markEditedFields(did) {
  const cfg = getEditableDroneConfigs()[String(did)] || {};
  const p = cfg.preset;
  const who = cfg.platform_id || "catalog";
  // Amber mark + a tooltip naming the nominal value; the box's own tooltip
  // comes back when it matches the preset again.
  const mark = (el, edited, nominal) => {
    if (!el) return;
    if (el.dataset.baseTitle === undefined) el.dataset.baseTitle = el.title || "";
    const on = !!(p && edited);
    el.classList.toggle("edited", on);
    el.title = on ? `Nominal ${who} value: ${nominal} — you changed it` : el.dataset.baseTitle;
  };
  const off = (a, b) => Math.abs(Number(a) - Number(b)) > 1e-6;
  const n = (v) => Math.round(Number(v || 0) * 100) / 100;
  if (!p) {
    document.querySelectorAll(`.fleet-row[data-drone-id="${did}"] .edited, #drawer-d${did} .edited`)
      .forEach((el) => mark(el, false));
    markCeiling(did);
    return;
  }
  mark($(`cfg-d${did}-alt`), off(cfg.cruise_altitude_km, p.cruise_altitude_km), `${n(p.cruise_altitude_km)} km`);
  mark($(`cfg-d${did}-alt2`), off(cfg.cruise_altitude_km, p.cruise_altitude_km), `${n(p.cruise_altitude_km)} km`);
  mark($(`cfg-d${did}-climb`), off(cfg.climb_angle_deg, p.climb_angle_deg), `${n(p.climb_angle_deg)}°`);
  mark($(`cfg-d${did}-descent`), off(cfg.descent_angle_deg, p.descent_angle_deg), `${n(p.descent_angle_deg)}°`);
  mark($(`cfg-d${did}-speed`), off(cfg.speed, p.speed), `${n(p.speed * MINUTES_PER_HOUR)} km/h`);
  mark($(`cfg-d${did}-fuel`), off(cfg.fuel_budget, p.fuel_budget), `${n(p.fuel_budget)} km`);
  mark($(`cfg-d${did}-runway`), cfg.runway !== p.runway, `runway ${p.runway}`);
  categoryIds().forEach((t) => {
    const name = categoryName(t);
    const pv = !!(p.target_access || {})[t];
    mark($(`cfg-d${did}-type-${t}`)?.parentElement, !!(cfg.target_access || {})[t] !== pv,
      pv ? `visits ${name}` : `its sensors can't see ${name}`);
    mark($(`loiter-d${did}-type-${t}`), off((cfg.loiter_times || {})[t] || 0, (p.loiter_times || {})[t] || 0),
      `${n((p.loiter_times || {})[t])} min`);
    mark($(`detect-d${did}-type-${t}`), off((cfg.detection_radius || {})[t] || 0, (p.detection_radius || {})[t] || 0),
      `${n((p.detection_radius || {})[t])} km`);
  });
  markCeiling(did);
}

function updateFleetRowStates() {
  for (let did = 1; did <= 5; did++) {
    const cfg = state.droneConfigs[String(did)] || {};
    const row = document.querySelector(`.fleet-row[data-drone-id="${did}"]`);
    const btn = document.querySelector(`[data-drone-toggle="${did}"]`);
    const off = cfg.enabled === false;
    if (row) row.classList.toggle("disabled", off);
    if (btn) btn.disabled = off;
    if (off) setDroneDrawer(String(did), false);
  }
}

/**
 * Refresh the drone config UI from state.droneConfigs.
 * Called during segment switches to update the UI to match the segment's stored configs.
 */
function refreshDroneConfigUI() {
  for (let did = 1; did <= 5; did++) {
    const idStr = String(did);
    const cfg = state.droneConfigs[idStr];
    if (!cfg) continue;

    // Every box from the config (type, sensors, speed, angles, grids too), so a
    // change made elsewhere — the agent's set_drone, a segment switch — shows.
    showDroneConfig(did, cfg);
  }
  updateLoiterTabUI();
  updateDetectionRadiusTabUI();
  // Keep the multi-stop refuel grid in sync with the loaded/edited configs.
  if (typeof populateRefuelStopsBox === "function") populateRefuelStopsBox();
  if (typeof updateTimeSyncMemberUI === "function") updateTimeSyncMemberUI();
}

/**
 * Read drone configs directly from the UI table.
 * This is the source of truth at accept time - what the user sees is what gets saved.
 * @returns {object} drone_configs object with enabled, fuel_budget, etc.
 */
// Loitering is on when any target type has a loiter time above 0.
function loiterIsOn(loiterTimes) {
  return Object.values(loiterTimes || {}).some((v) => Number(v) > 0);
}

function readDroneConfigsFromUI() {
  const configs = {};
  for (let did = 1; did <= 5; did++) {
    const idStr = String(did);
    const cbEnabled = $(`cfg-d${did}-enabled`);
    const fuelInput = $(`cfg-d${did}-fuel`);
    const startSel = $(`cfg-d${did}-start`);
    const endSel = $(`cfg-d${did}-end`);
    const refuelSel = $(`cfg-d${did}-refuel`);

    // Start with existing config as base (for fields not in UI)
    const baseConfig = state.droneConfigs[idStr] || {};

    // CUT/REPLAN CONTRACT — sanitize the END airport at the single UI read boundary.
    // A drone's end airport must be a REAL airport (or "-" = solver-chooses). After a
    // cut, the drone's start becomes a checkpoint node (C{n}-{d}); if the end dropdown
    // is blank/stale the raw value can come back empty or as a cut node, and the solver
    // then falls back end→start and loops the drone back to the cut node (ended at
    // C3-1). Resolve the real end HERE so every solve path gets a clean value: prefer a
    // real end dropdown value, else the config's real end, else this drone's own
    // airport A{did}, else flexible "-". Cut nodes (C{n}-{d}, *_START) and the empty
    // string are never accepted.
    const _realAirportIds = new Set(
      ((state.env && state.env.airports) || [])
        .filter((a) => a && a.type !== "tanker" && !a.is_synthetic)
        .map((a) => String(a.id))
        .filter((id) => !/^C\d+-\d+$/.test(id) && !id.endsWith("_START"))
    );
    const _isRealEnd = (v) => v === "-" || _realAirportIds.has(String(v));
    const _rawEnd = endSel ? endSel.value : baseConfig.end_airport;
    let _cleanEnd = _isRealEnd(_rawEnd) ? _rawEnd : null;
    if (_cleanEnd === null && _isRealEnd(baseConfig.end_airport)) _cleanEnd = baseConfig.end_airport;
    if (_cleanEnd === null) _cleanEnd = _realAirportIds.has(`A${did}`) ? `A${did}` : "-";

    const speedInput = $(`cfg-d${did}-speed`);
    const runwaySel = $(`cfg-d${did}-runway`);
    const altInput = $(`cfg-d${did}-alt`);
    const climbInput = $(`cfg-d${did}-climb`);
    const descentInput = $(`cfg-d${did}-descent`);
    const loiterTimes = readLoiterTimesForDrone(did);

    const fuelInputVal = fuelInput ? parseFloat(fuelInput.value) || 150 : 150;
    configs[idStr] = {
      ...baseConfig,
      enabled: cbEnabled ? cbEnabled.checked : true,
      // §11.10: ONE fuel value — fuel_budget IS the tank (what the fuel input sets,
      // full at the start of every leg). It is NEVER reduced/overwritten; a dynamic
      // replan passes its reduced leg-1 fuel as the transient start_fuel instead. So
      // fuel_capacity is retired (it only existed to survive the destructive
      // overwrite that no longer happens).
      fuel_budget: fuelInputVal,
      start_airport: startSel ? startSel.value : baseConfig.start_airport,
      end_airport: _cleanEnd,  // sanitized above — never a cut node / empty
      refuel_airport: refuelSel ? refuelSel.value : (baseConfig.refuel_airport || ""),
      // Multi-stop refuel: carry the user's picked stops explicitly so export +
      // re-solve preserve them (the legacy refuel_airport select was removed).
      refuel_stops: Array.isArray(baseConfig.refuel_stops) ? [...baseConfig.refuel_stops] : [],
      runway: runwaySel ? runwaySel.value : (baseConfig.runway || "S"),
      speed: speedInput ? speedKmPerMinFromInput(speedInput) : (baseConfig.speed || 1),
      // Each drone's own cruise altitude + climb angle (docs/architecture/ALTITUDE_MODEL.md).
      cruise_altitude_km: altInput ? (parseFloat(altInput.value) || 0) : (baseConfig.cruise_altitude_km ?? 0),
      climb_angle_deg: climbInput
        ? (parseFloat(climbInput.value) || DEFAULT_CLIMB_ANGLE_DEG)
        : (baseConfig.climb_angle_deg ?? DEFAULT_CLIMB_ANGLE_DEG),
      descent_angle_deg: descentInput
        ? (parseFloat(descentInput.value) || DEFAULT_CLIMB_ANGLE_DEG)
        : (baseConfig.descent_angle_deg ?? baseConfig.climb_angle_deg ?? DEFAULT_CLIMB_ANGLE_DEG),
      loiter_enabled: loiterIsOn(loiterTimes),
      loiter_times: loiterTimes,
      detection_radius: readDetectionRadiusForDrone(did),
    };
    // Keep state.droneConfigs in lockstep with what we send to the solver. The
    // animation reads detection_radius (and other fields) from state.droneConfigs;
    // if we only put the fresh UI values in the SOLVE payload and left the config
    // stale, the drone would solve with one radius but the animation would detect
    // with another (e.g. edit radius to 0 -> solve gives the center path, but the
    // animation still green-checks at the OLD 12 km). Write the built config back
    // so both read the same values.
    state.droneConfigs[idStr] = JSON.parse(JSON.stringify(configs[idStr]));
  }
  return configs;
}

function readTimeSyncOptionsFromUI() {
  const enabled = $("chk-time-sync")?.checked || false;
  const toleranceRaw = parseFloat($("time-sync-tolerance")?.value || "1");
  const strategy = $("time-sync-strategy")?.value || "launch_delay";
  const members = [];
  for (let did = 1; did <= 5; did++) {
    const include = $(`time-sync-d${did}-enabled`);
    const target = $(`time-sync-d${did}-target`);
    if (include?.checked && target?.value) {
      members.push({ drone_id: String(did), target_id: target.value });
    }
  }
  const hasSelectedMembers = members.length > 0;
  const options = {
    enabled,
    mode: hasSelectedMembers ? "selected_targets" : "first_target_arrival",
    wait_strategy: strategy === "hover_wait" ? "hover_wait" : "launch_delay",
    tolerance: Number.isFinite(toleranceRaw) ? Math.max(0, toleranceRaw) : 1,
  };
  if (hasSelectedMembers) options.members = members;
  return options;
}

function restoreTimeSyncOptionsToUI(options) {
  const restored = options && typeof options === "object" ? options : {};
  const enabled = restored.enabled === true;
  const tolerance = Number(restored.tolerance);
  const strategy =
    restored.wait_strategy === "hover_wait" || restored.strategy === "hover_wait"
      ? "hover_wait"
      : "launch_delay";

  const toggle = $("chk-time-sync");
  const toleranceInput = $("time-sync-tolerance");
  const strategySelect = $("time-sync-strategy");
  if (toggle) toggle.checked = enabled;
  if (toleranceInput) {
    toleranceInput.value = Number.isFinite(tolerance) ? Math.max(0, tolerance) : 1;
  }
  if (strategySelect) strategySelect.value = strategy;

  // Rebuild against the imported targets/configs, then clear the DOM defaults
  // before applying explicit selected drone-target pairs.
  updateTimeSyncMemberUI();
  for (let did = 1; did <= 5; did++) {
    const include = $(`time-sync-d${did}-enabled`);
    if (include) include.checked = false;
  }

  const members = Array.isArray(restored.members) ? restored.members : [];
  members.forEach((member) => {
    if (!member || typeof member !== "object") return;
    const did = String(member.drone_id ?? member.drone ?? "").replace(/^D/i, "");
    const targetId = String(
      member.target_id ?? member.waypoint ?? member.target ?? "",
    );
    const include = $(`time-sync-d${did}-enabled`);
    const target = $(`time-sync-d${did}-target`);
    if (!include || !target || !targetId) return;
    const hasTarget = Array.from(target.options).some(
      (option) => option.value === targetId,
    );
    if (!hasTarget) return;
    target.value = targetId;
    include.checked = !include.disabled;
  });
}

function updateTimeSyncMemberUI() {
  const container = $("time-sync-members");
  if (!container) return;

  const env = getEditableEnv() || state.env || {};
  const configs = getEditableDroneConfigs() || state.droneConfigs || {};
  const allTargets = (env.targets || [])
    .filter((target) => target && (target.id || target.label))
    .sort((a, b) =>
      String(a.id || a.label).localeCompare(
        String(b.id || b.label),
        undefined,
        { numeric: true },
      ),
    );
  const previous = {};
  for (let did = 1; did <= 5; did++) {
    previous[String(did)] = {
      checked: $(`time-sync-d${did}-enabled`)?.checked || false,
      target: $(`time-sync-d${did}-target`)?.value || "",
    };
  }

  container.innerHTML = "";
  for (let did = 1; did <= 5; did++) {
    const idStr = String(did);
    const cfg = configs[idStr] || {};
    const targetAccess = cfg.target_access;
    // Legacy configs without target_access predate type E and are treated by the
    // solver as supporting A-D. Configs that explicitly contain access flags use
    // exactly the types selected for that drone.
    const allowedTypes =
      targetAccess && Object.keys(targetAccess).length > 0
        ? new Set(
            Object.entries(targetAccess)
              .filter(([, enabled]) => enabled)
              .map(([type]) => String(type).toLowerCase()),
          )
        : new Set(["a", "b", "c", "d"]);
    const targets = allTargets
      .filter((target) =>
        allowedTypes.has(String(target.type || "a").toLowerCase()),
      )
      .map((target) => String(target.id || target.label));
    const noTargets = targets.length === 0;
    const disabled = cfg.enabled === false;
    const row = document.createElement("label");
    row.style.display = "grid";
    row.style.gridTemplateColumns = "auto 1fr";
    row.style.alignItems = "center";
    row.style.gap = "0.28rem";
    row.style.opacity = disabled ? "0.48" : "1";

    const checked = previous[idStr].checked && !disabled && !noTargets;
    const selectedTarget = targets.includes(previous[idStr].target)
      ? previous[idStr].target
      : targets[0] || "";
    row.innerHTML = `
      <span style="display:flex; align-items:center; gap:0.25rem;">
        <input id="time-sync-d${did}-enabled" type="checkbox" ${checked ? "checked" : ""} ${disabled || noTargets ? "disabled" : ""} />
        D${did}
      </span>
      <select id="time-sync-d${did}-target" style="width:max-content; padding:0.15rem 0.2rem; border-radius:0.35rem; border:1px solid #374151; background:#020617; color:#e5e7eb; font-size:inherit;" ${disabled || noTargets ? "disabled" : ""}>
        ${targets.map((tid) => `<option value="${escapeHtml(tid)}" ${tid === selectedTarget ? "selected" : ""}>${escapeHtml(tid)}</option>`).join("")}
      </select>
    `;
    container.appendChild(row);

    const include = $(`time-sync-d${did}-enabled`);
    const select = $(`time-sync-d${did}-target`);
    include?.addEventListener("change", () => invalidateMission(`D${did} time-sync member changed`));
    select?.addEventListener("change", () => invalidateMission(`D${did} time-sync target changed`));
  }
}

function getActiveTimeSync() {
  return state.timeSync || missionState.draftSolution?.timeSync || null;
}

function formatSyncTime(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "";
  return n.toFixed(n >= 10 ? 1 : 2);
}

function updateTimeSyncUi() {
  const badge = $("mc-sync-status");
  if (!badge) return;

  const sync = getActiveTimeSync();
  if (!sync || !sync.enabled) {
    badge.hidden = true;
    badge.textContent = "";
    badge.title = "";
    badge.classList.remove("sync-warn", "sync-muted");
    return;
  }

  const status = String(sync.status || "unknown");
  badge.hidden = false;
  badge.classList.remove("sync-warn", "sync-muted");

  if (status === "synchronized") {
    const syncTime = formatSyncTime(sync.sync_time);
    badge.textContent = syncTime ? `Sync t=${syncTime}` : "Sync active";
    const strategy = sync.wait_strategy === "hover_wait" ? "hover wait" : "launch delay";
    const scope = sync.mode === "selected_targets"
      ? "Selected target arrivals"
      : "First target arrivals";
    badge.title = `${scope} are synchronized using ${strategy}.`;
  } else if (status === "infeasible") {
    badge.textContent = "Sync infeasible";
    badge.title = (sync.warnings || []).join(" ") || "Time-on-target synchronization is infeasible for this plan.";
    badge.classList.add("sync-warn");
  } else if (status === "skipped") {
    badge.textContent = "Sync skipped";
    badge.title = sync.reason || "Synchronization was skipped.";
    badge.classList.add("sync-muted");
  } else {
    badge.textContent = `Sync ${status}`;
    badge.title = "";
    badge.classList.add("sync-muted");
  }
}

// One drone's config from what a file saved for it (missing fields get
// defaults). missionAltitude = an older file's mission-wide {altKm, climbDeg},
// used for drones that don't carry their own altitude.
function droneConfigFromSaved(saved, did, airportIds, missionAltitude = {}) {
  const defaultAirport = airportIds[0] || "A1";
  // Use saved config if available, otherwise use defaults.
  // §11.10: ONE fuel value — fuel_budget IS the tank. For legacy envs that only
  // carried fuel_capacity, fall back to it as the tank so old missions still load.
  const enabledDefault = saved.enabled !== undefined ? saved.enabled : true;
  const fuelDefault =
    saved.fuel_budget !== undefined ? saved.fuel_budget
    : (saved.fuel_capacity !== undefined ? saved.fuel_capacity : 150);
  const startDefault = saved.start_airport || (airportIds.includes(`A${did}`) ? `A${did}` : defaultAirport);
  const endDefault = saved.end_airport || (airportIds.includes(`A${did}`) ? `A${did}` : defaultAirport);
  const refuelDefault = saved.refuel_airport || "";
  // Multi-stop refuel: refuel_stops[] is the new model. ONLY adopt it when the
  // env EXPLICITLY carries it (new-format envs). A legacy single refuel_airport
  // is NOT auto-promoted yet — doing so would switch its solve from the legacy
  // two-leg path to the virtual-drone path before the UI/animation can render a
  // multi-leg route. That promotion happens when the refuel-stops UI lands.
  const refuelStopsDefault = Array.isArray(saved.refuel_stops)
    ? saved.refuel_stops.filter((s) => s && s !== "-")
    : [];
  // Every current category present: one the file lacks (added later) is
  // visited by a Custom drone and not by a catalog aircraft (its sensors have
  // no figures for it).
  const accessDefault = withAllCategories(saved.target_access, !saved.platform_id);
  const speedDefault = saved.speed !== undefined ? saved.speed : 1;
  // No loiter on/off switch any more: a loiter time above 0 is the switch. A
  // file saved with loitering off keeps its times out of the plan (as before)
  // by importing them as 0.
  const loiterTimesDefault = saved.loiter_enabled === true && saved.loiter_times
    ? withAllCategories(saved.loiter_times, 0)
    : perCategory(0);
  // Each drone's own altitude + climb; older files had one mission-wide value.
  const altitudeDefault = saved.cruise_altitude_km ?? missionAltitude?.altKm ?? 0;
  const climbDefault = saved.climb_angle_deg ?? missionAltitude?.climbDeg ?? DEFAULT_CLIMB_ANGLE_DEG;
  const detectionRadiusDefault = withAllCategories(saved.detection_radius, 0);
  // Opportunistic refuel: refuel_options = SET of allowed refuel points. Prefer
  // a saved refuel_options; else MIGRATE the legacy refuel_stops (ordered list)
  // to its unique set so old envs become opportunistic on import.
  const refuelOptionsDefault = Array.isArray(saved.refuel_options)
    ? [...new Set(saved.refuel_options.map(String).filter((s) => s && s !== "-"))]
    : [...new Set(refuelStopsDefault.map(String))];
  const maxRefuelsDefault = (saved.max_refuels != null)
    ? saved.max_refuels
    : Math.max(refuelOptionsDefault.length, 4);
  // Default S (smallest, fits all airports) so legacy configs without
  // a runway field don't accidentally become incompatible.
  const runwayDefault = saved.runway || "S";

  return {
    enabled: enabledDefault,
    fuel_budget: fuelDefault,
    start_airport: startDefault,
    end_airport: endDefault,
    refuel_airport: refuelDefault,   // legacy single-stop (kept for back-compat)
    refuel_stops: refuelStopsDefault,  // legacy prescriptive multi-stop
    refuel_options: refuelOptionsDefault,  // opportunistic: allowed refuel SET
    max_refuels: maxRefuelsDefault,        // opportunistic: refuel-count cap
    runway: runwayDefault,
    target_access: accessDefault,
    speed: speedDefault,
    loiter_enabled: loiterIsOn(loiterTimesDefault),
    loiter_times: loiterTimesDefault,
    detection_radius: detectionRadiusDefault,
    cruise_altitude_km: altitudeDefault,
    climb_angle_deg: climbDefault,
    descent_angle_deg: saved.descent_angle_deg ?? climbDefault,
    // Catalog link (aircraft type, sensors, the nominal preset the "edited"
    // marks compare against, its ceiling) — kept exactly as saved.
    ...(saved.platform_id ? {
      platform_id: saved.platform_id,
      sensor_ids: Array.isArray(saved.sensor_ids) ? [...saved.sensor_ids] : [],
      preset: saved.preset ? JSON.parse(JSON.stringify(saved.preset)) : undefined,
      ceiling_km: saved.ceiling_km ?? null,
    } : {}),
  };
}

function initDroneConfigsFromEnv() {
  const airports = (state.env && state.env.airports) || [];
  const airportIds = airports.map((a) => String(a.id));

  // Check if drone_configs are saved in the environment
  const savedConfigs = (state.env && state.env.drone_configs) || {};

  // Lookup: airport id -> runway type. Legacy airports without `runway` get L.
  const airportRunway = new Map(
    airports.map((a) => [String(a.id), (a.runway || "L").toUpperCase()])
  );
  const airportLabel = (aid) =>
    `${aid} (${airportRunway.get(aid) || "L"})`;
  const compatibleAirports = (droneRwy) =>
    airportIds.filter((aid) =>
      isAirportCompatibleWithDrone(airportRunway.get(aid), droneRwy)
    );

  function populateStartSelect(selectId, droneRwy) {
    const sel = $(selectId);
    if (!sel) return;
    sel.innerHTML = "";
    compatibleAirports(droneRwy).forEach((aid) => {
      const opt = document.createElement("option");
      opt.value = aid;
      opt.textContent = airportLabel(aid);
      sel.appendChild(opt);
    });
  }

  function populateEndSelect(selectId, droneRwy) {
    const sel = $(selectId);
    if (!sel) return;
    sel.innerHTML = "";
    // Add flexible endpoint option first
    const flexOpt = document.createElement("option");
    flexOpt.value = "-";
    flexOpt.textContent = "Any";
    sel.appendChild(flexOpt);
    // Add airport options (filtered to runway-compatible)
    compatibleAirports(droneRwy).forEach((aid) => {
      const opt = document.createElement("option");
      opt.value = aid;
      opt.textContent = airportLabel(aid);
      sel.appendChild(opt);
    });
  }

  function populateRefuelSelect(selectId, droneRwy) {
    const sel = $(selectId);
    if (!sel) return;
    sel.innerHTML = "";
    // "None" = no refueling
    const noneOpt = document.createElement("option");
    noneOpt.value = "";
    noneOpt.textContent = "None";
    sel.appendChild(noneOpt);
    // "Any" = solver picks best refuel airport
    const anyOpt = document.createElement("option");
    anyOpt.value = "-";
    anyOpt.textContent = "Any";
    sel.appendChild(anyOpt);
    // Specific airports (filtered to runway-compatible)
    compatibleAirports(droneRwy).forEach((aid) => {
      const opt = document.createElement("option");
      opt.value = aid;
      opt.textContent = airportLabel(aid);
      sel.appendChild(opt);
    });
  }

  // No closure capture of airports here — runway/dropdown repopulation
  // must read state.env.airports fresh every time, because the user can
  // add or remove airports after this function ran. The thin wrapper below
  // delegates to the (always-fresh) updateAirportDropdowns().
  state._repopulateDroneDropdowns = (_did) => {
    updateAirportDropdowns();
  };

  for (let did = 1; did <= 5; did++) {
    const idStr = String(did);
    const saved = savedConfigs[idStr] || {};

    state.droneConfigs[idStr] = droneConfigFromSaved(
      saved, did, airportIds, state.importedMissionAltitude
    );
    const runwayDefault = state.droneConfigs[idStr].runway;

    populateStartSelect(`cfg-d${did}-start`, runwayDefault);
    populateEndSelect(`cfg-d${did}-end`, runwayDefault);
    populateRefuelSelect(`cfg-d${did}-refuel`, runwayDefault);

    showDroneConfig(did, state.droneConfigs[idStr]);
  }

  // Sync the Loiter tab + multi-stop refuel grid + detection-radius grid with the
  // loaded config (import).
  updateLoiterTabUI();
  if (typeof populateRefuelStopsBox === "function") populateRefuelStopsBox();
  if (typeof updateDetectionRadiusTabUI === "function") updateDetectionRadiusTabUI();
  if (typeof updateTimeSyncMemberUI === "function") updateTimeSyncMemberUI();
}

function attachConfigListeners() {
  for (let did = 1; did <= 5; did++) {
    const idStr = String(did);

    const cbEnabled = $(`cfg-d${did}-enabled`);
    const fuelInput = $(`cfg-d${did}-fuel`);
    const startSel = $(`cfg-d${did}-start`);
    const endSel = $(`cfg-d${did}-end`);
    const refuelSel = $(`cfg-d${did}-refuel`);

    if (cbEnabled) {
      cbEnabled.addEventListener("change", () => {
        getEditableDroneConfigs()[idStr].enabled = cbEnabled.checked;
        updateLoiterTabUI();
        updateDetectionRadiusTabUI();
        populateRefuelStopsBox();
        updateTimeSyncMemberUI();
        updateSamWrappingClientSide(); // SAM outlines follow the enabled drones' altitudes
        drawEnvironment();
        invalidateMission(`Drone ${idStr} enabled changed`);
      });
    }

    if (fuelInput) {
      fuelInput.addEventListener("change", () => {
        const v = parseFloat(fuelInput.value || "0");
        const fuelVal = isNaN(v) ? 0 : v;
        const cfg = getEditableDroneConfigs()[idStr];
        // §11.10: ONE fuel value — the Assets fuel field sets fuel_budget = THE
        // TANK (full each leg). A dynamic replan does NOT touch it; it passes its
        // reduced leg-1 fuel as the transient start_fuel instead. A fresh manual
        // edit also clears any stale start_fuel so the next solve uses the new tank.
        cfg.fuel_budget = fuelVal;
        delete cfg.start_fuel;
        // Update stats to reflect new fuel budget
        updateStatsFromRoutes();
        invalidateMission(`Drone ${idStr} fuel_budget changed`);
      });
    }

    if (startSel) {
      startSel.addEventListener("change", () => {
        getEditableDroneConfigs()[idStr].start_airport = startSel.value;
        invalidateMission(`Drone ${idStr} start_airport changed`);
      });
    }

    if (endSel) {
      endSel.addEventListener("change", () => {
        getEditableDroneConfigs()[idStr].end_airport = endSel.value;
        invalidateMission(`Drone ${idStr} end_airport changed`);
      });
    }

    if (refuelSel) {
      refuelSel.addEventListener("change", () => {
        console.log(`[REFUEL] D${did} refuel changed to: "${refuelSel.value}", mode before: ${missionState.mode}`);
        getEditableDroneConfigs()[idStr].refuel_airport = refuelSel.value;
        invalidateMission(`Drone ${idStr} refuel_airport changed`);
        console.log(`[REFUEL] D${did} mode after invalidate: ${missionState.mode}, canSolve: ${getUiPermissions().canSolve}`);
        const btn = $("mc-run-planner");
        console.log(`[REFUEL] mc-run-planner disabled: ${btn?.disabled}, textContent: "${btn?.textContent}"`);
      });
    }

    const runwaySel = $(`cfg-d${did}-runway`);
    if (runwaySel) {
      runwaySel.addEventListener("change", () => {
        getEditableDroneConfigs()[idStr].runway = runwaySel.value;
        // Repopulate Start/End/Refuel to honor new compatibility constraint.
        if (typeof state._repopulateDroneDropdowns === "function") {
          state._repopulateDroneDropdowns(did);
        }
        populateRefuelStopsBox();  // grey out refuel points now too short
        invalidateMission(`Drone ${idStr} runway changed`);
      });
    }

    // Speed input
    const speedInput = $(`cfg-d${did}-speed`);
    if (speedInput) {
      speedInput.addEventListener("change", () => {
        getEditableDroneConfigs()[idStr].speed = speedKmPerMinFromInput(speedInput);
        invalidateMission(`Drone ${idStr} speed changed`);
      });
    }

    // Cruise altitude: one value in two boxes (fleet table + drawer).
    const altInputs = [$(`cfg-d${did}-alt`), $(`cfg-d${did}-alt2`)].filter(Boolean);
    altInputs.forEach((input) => {
      input.addEventListener("input", () => {
        altInputs.forEach((other) => { if (other !== input) other.value = input.value; });
      });
      input.addEventListener("change", () => {
        getEditableDroneConfigs()[idStr].cruise_altitude_km = parseFloat(input.value) || 0;
        populateRefuelStopsBox();  // tankers in reach of the new altitude
        updateSamWrappingClientSide();
        drawEnvironment();
        if (cesiumView.isActive()) cesiumView.render();
        invalidateMission(`Drone ${idStr} cruise altitude changed`);
      });
    });
    const climbInput = $(`cfg-d${did}-climb`);
    if (climbInput) {
      climbInput.addEventListener("change", () => {
        getEditableDroneConfigs()[idStr].climb_angle_deg = parseFloat(climbInput.value) || DEFAULT_CLIMB_ANGLE_DEG;
        invalidateMission(`Drone ${idStr} climb angle changed`);
      });
    }
    const descentInput = $(`cfg-d${did}-descent`);
    if (descentInput) {
      descentInput.addEventListener("change", () => {
        getEditableDroneConfigs()[idStr].descent_angle_deg = parseFloat(descentInput.value) || DEFAULT_CLIMB_ANGLE_DEG;
        invalidateMission(`Drone ${idStr} descent angle changed`);
      });
    }

    // Per-category inputs (Visit / Detect km / Loiter min) are built with the
    // drawer's category rows: renderCategoryRows -> attachCategoryInputs.
  }

  attachFleetDrawers();

  for (let did = 1; did <= 5; did++) {
    const platformSel = $(`cfg-d${did}-platform`);
    if (!platformSel) continue;
    platformSel.addEventListener("change", () => {
      const platform = catalogPlatform(platformSel.value);
      // A new Type starts with every sensor it carries.
      applyAssetPreset(did, platformSel.value, platform ? [...(platform.sensors || [])] : []);
    });
  }
  // Edits anywhere in the Drones table: re-mark values that differ from the
  // preset (the field's own listener has updated the config by then).
  document.querySelector(".fleet-table")?.addEventListener("change", () => {
    for (let did = 1; did <= 5; did++) markEditedFields(did);
  });

  // Initial sync of loiter tab + refuel-stops grid + detection-radius grid
  updateLoiterTabUI();
  populateRefuelStopsBox();
  if (typeof updateDetectionRadiusTabUI === "function") updateDetectionRadiusTabUI();
  updateTimeSyncMemberUI();

  const timeSyncToggle = $("chk-time-sync");
  const timeSyncTolerance = $("time-sync-tolerance");
  const timeSyncStrategy = $("time-sync-strategy");
  if (timeSyncToggle) {
    timeSyncToggle.addEventListener("change", () => {
      invalidateMission("time-on-target sync changed");
    });
  }
  if (timeSyncTolerance) {
    timeSyncTolerance.addEventListener("change", () => {
      invalidateMission("time-on-target sync tolerance changed");
    });
  }
  if (timeSyncStrategy) {
    timeSyncStrategy.addEventListener("change", () => {
      invalidateMission("time-on-target sync strategy changed");
    });
  }
}


/**
 * Refresh state.sequences from the current state.routes (each drone's
 * route waypoints become its sequence string), and re-populate the
 * sequence-input box for the currently-selected drone. Call this any
 * time state.routes changes outside the apply-sequence flow — e.g.,
 * on segment switches during animation, after Run Planner, after a
 * checkpoint replan.
 */
// ===========================================================================
// WHOLE-MISSION DRONE SEQUENCE (docs/architecture/UNIFIED_MISSION_OPERATIONS.md)
// A drone's sequence is everything it has flown and will fly, with every diamond
// where its plan changed:  A1,T2,T5,C1,T9,T1,C2,A3
// Everything up to the latest diamond is HISTORY (read-only, already flown);
// the rest is the REMAINING route that route edits change.
// ===========================================================================

// Index i of the trajectory piece [i, i+1] closest to pos (-1 if none).
function closestTrajectoryPiece(trajectory, pos) {
  let bestDist = Infinity;
  let bestIdx = -1;
  for (let i = 0; i < (trajectory || []).length - 1; i++) {
    const p1 = trajectory[i];
    const p2 = trajectory[i + 1];
    const distance = closestFlightPosition(p1, p2, pos).distanceKm;
    if (distance < bestDist) {
      bestDist = distance;
      bestIdx = i;
    }
  }
  return bestIdx;
}

function waypointLatLng(id) {
  const sid = String(id);
  const a = (state.env?.airports || []).find(x => String(x.id) === sid);
  if (a && a.lat !== undefined) return [a.lat, a.lng];
  const t = getAllKnownTargets().find(x => String(x.id) === sid);
  if (t && t.lat !== undefined) return [t.lat, t.lng];
  return null;
}

// Trajectory index of each route waypoint (server's waypoint_indices, else the
// nearest trajectory point to the waypoint's position).
function routeWaypointTrajectoryIndices(routeData) {
  const route = routeData?.route || [];
  const traj = routeData?.trajectory || [];
  const wi = routeData?.waypoint_indices || {};
  return route.map((wp, j) => {
    const known = wi[j] ?? wi[String(j)];
    if (known !== undefined) return Number(known);
    const pos = waypointLatLng(wp);
    if (!pos || traj.length === 0) return j === 0 ? 0 : traj.length - 1;
    let best = 0;
    let bestD = Infinity;
    traj.forEach((p, i) => {
      const d = (p[0] - pos[0]) ** 2 + (p[1] - pos[1]) ** 2;
      if (d < bestD) { bestD = d; best = i; }
    });
    return best;
  });
}

// Split a segment's route at a cut position: waypoints the drone REACHED before
// the cut, and the ones it had not reached yet.
function splitRouteAtCut(routeData, cutPos) {
  const route = (routeData?.route || []).map(String);
  const traj = routeData?.trajectory || [];
  if (!cutPos || traj.length < 2) return { reached: route.slice(0, 1), unreached: route.slice(1) };
  const piece = closestTrajectoryPiece(traj, cutPos);
  const indices = routeWaypointTrajectoryIndices(routeData);
  const reached = [];
  const unreached = [];
  route.forEach((wp, j) => {
    const ti = indices[j];
    const atCut = ti === piece + 1 &&
      Math.abs(traj[ti][0] - cutPos[0]) < 1e-7 && Math.abs(traj[ti][1] - cutPos[1]) < 1e-7;
    (ti <= piece || atCut ? reached : unreached).push(wp);
  });
  return { reached, unreached };
}

/**
 * Build drone `did`'s whole-mission sequence.
 * Returns { labels, nodes, historyLength }: labels as shown (C2), nodes as the
 * solver knows them (C2-1 / D1_START), and how many leading entries are history.
 */
function buildMissionSequence(did) {
  did = String(did);
  const segs = [];
  const count = (typeof missionReplay !== "undefined" && missionReplay.getSegmentCount)
    ? missionReplay.getSegmentCount() : 0;
  for (let k = 0; k < count; k++) {
    const seg = missionReplay.getSegment(k);
    const cutPositions = seg?.cutPosition || seg?.cutPositions;
    segs.push({
      routeData: seg?.solution?.routes?.[did] || null,
      cutPos: k > 0 ? (cutPositions?.[did] || null) : null,
      lost: (seg?.lostDrones || []).map(String).includes(did),
      label: `C${k}`,
    });
  }

  // The uncommitted part: a draft on screen, or a cut still waiting for a plan
  // (C-key cut, or a pause previewed as the cut it would become).
  const draft = missionState.draftSolution;
  if (draft && missionState.mode === MissionMode.DRAFT_READY) {
    if (count === 0 || !draft.isCheckpointReplan) {
      if (count === 0) segs.push({ routeData: draft.routes?.[did] || null, cutPos: null, lost: false, label: "" });
      else segs[segs.length - 1].routeData = draft.routes?.[did] || segs[segs.length - 1].routeData;
    } else {
      segs.push({
        routeData: draft.routes?.[did] || null,
        cutPos: draft.pendingCutPositions?.[did] || null,
        lost: (draft.lostDrones || []).map(String).includes(did),
        label: `C${count}`,
      });
    }
  } else if (count > 0) {
    let pendingPos = null;
    let pendingNode = null;
    let finished = false;  // drone already at the end of its route: no diamond
    const cpSeg = state.checkpoint?.active ? state.checkpoint.segments?.[did] : null;
    if (cpSeg?.finishedAtCut && cpSeg.splitPoint) {
      pendingPos = cpSeg.splitPoint;
      finished = true;
    } else if (state.checkpoint?.active && state.pendingCutPositions?.[did]) {
      pendingPos = state.pendingCutPositions[did];
      pendingNode = state.pendingCheckpoints?.[did]?.id || null;
    } else if (missionState.mode === MissionMode.PAUSED_MID_ANIMATION ||
               (missionState.mode === MissionMode.EDITING_ENV && missionState.wasAnimating)) {
      const ds = state.animation?.drones?.[did];
      if (ds && ds._currentLat !== undefined && !ds._lostAtPosition) {
        pendingPos = [ds._currentLat, ds._currentLng];
        finished = ds.totalDistance > 0 && ds.distanceTraveled >= ds.totalDistance - 1e-6;
      }
    }
    if (pendingPos) {
      segs.push({
        routeData: null, cutPos: finished ? null : pendingPos, handover: pendingPos,
        lost: false, label: `C${count}`, node: pendingNode, pending: true,
      });
    }
  }

  const labels = [];
  const nodes = [];
  let historyLength = 0;
  const push = (label, node) => { labels.push(label); nodes.push(node); };

  for (let k = 0; k < segs.length; k++) {
    const s = segs[k];
    const next = segs[k + 1];
    const route = (s.routeData?.route || []).map(String);
    if (k === 0 && route.length === 0) break;  // drone not in this mission
    if (!next) {
      // Last segment: its whole route is the remaining plan (k>0: start = its diamond).
      (k === 0 ? route : route.slice(1)).forEach(wp => push(wp, wp));
      break;
    }
    // Where the drone was when the next segment took over: its cut (a diamond), or
    // — with no cut — where the next segment's path starts: its landing spot after
    // flying this whole route, or this segment's start when it replanned in place.
    const handoverPos = next.cutPos || next.handover || next.routeData?.trajectory?.[0] || null;
    const { reached, unreached } = splitRouteAtCut(s.routeData, handoverPos);
    (k === 0 ? reached : reached.slice(1)).forEach(wp => push(wp, wp));
    const nextRoute = (next.routeData?.route || []).map(String);
    if (next.cutPos) push(next.label, next.node || nextRoute[0] || `C${k + 1}-${did}`);
    historyLength = labels.length;
    if (next.lost) break;  // killed at this diamond
    if (next.pending) {
      // Cut not planned yet: the remaining plan is the old route's unreached part.
      unreached.forEach(wp => push(wp, wp));
      break;
    }
  }
  return { labels, nodes, historyLength };
}

function missionSequenceText(did) {
  // Display only: never let an unexpected mission state break the caller (this
  // runs on every mode change). Fall back to the drone's current route.
  try {
    return buildMissionSequence(did).labels.join(",");
  } catch (err) {
    appendDebugLine(`⚠️ Sequence for D${did} unavailable (${err.message}); showing current route.`);
    return state.sequences?.[did] || "";
  }
}

function syncSequencesFromRoutes() {
  state.sequences = state.sequences || {};
  Object.entries(state.routes || {}).forEach(([did, routeData]) => {
    const route = routeData?.route;
    state.sequences[did] = Array.isArray(route) ? route.join(",") : "";
  });
  const cur = state.currentDroneForSeq;
  const seqInput = $("sequence-input");
  if (seqInput && cur) {
    seqInput.value = missionSequenceText(cur);
  }
}

// Show the selected drone's whole-mission sequence, unless the operator is typing.
function refreshSequenceBox() {
  const seqInput = $("sequence-input");
  const cur = state.currentDroneForSeq;
  if (!seqInput || !cur || document.activeElement === seqInput) return;
  seqInput.value = missionSequenceText(cur);
}

// Evaluate one drone's explicit route through the shared route evaluator
// (/api/apply_sequence → server/solver/route_evaluation.py).
async function evaluateDroneRoute(did, routeNodes, req) {
  const cfg = req.configsToSolve?.[did] || state.droneConfigs?.[did] || {};
  const fuelBudget = Number(cfg.start_fuel ?? cfg.fuel_budget ?? 0);
  const resp = await fetch("/api/apply_sequence", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      drone_id: String(did),
      sequence: routeNodes.join(","),
      env: req.payload.env,
      fuel_budget: fuelBudget,
      drone_config: cfg,  // detection radius etc. — same path shape as the planner
    }),
  });
  const data = await resp.json();
  if (!data.success) throw new Error(`D${did}: ${data.error || "route could not be applied"}`);
  return {
    route: data.route,
    sequence: data.route.join(","),
    points: data.points,
    distance: data.distance,
    fuel_budget: fuelBudget,
    trajectory: data.trajectory,
    waypoint_indices: data.waypoint_indices || {},
    span_distances: data.span_distances || [],
    anim_frame: data.anim_frame || undefined,  // server playback frame (flown distances)
  };
}

// The route each enabled drone flies if nobody changes it: the draft's route when
// a draft is on screen, else its current start node + the remaining part of its
// whole-mission sequence (re-evaluated from where it is now).
async function currentPlanRoutes(req, skip = {}) {
  const out = {};
  const draft = missionState.mode === MissionMode.DRAFT_READY ? missionState.draftSolution : null;
  for (const [did, cfg] of Object.entries(req.configsToSolve || {})) {
    if (!cfg || skip[did]) continue;
    if (cfg.enabled === false) {
      // Same as the solver: a disabled drone comes back with an empty route, so
      // the draft replaces (not keeps) whatever it was flying.
      out[did] = { route: [], sequence: "", trajectory: [], distance: 0, points: 0 };
      continue;
    }
    if (draft?.routes?.[did]?.route?.length) {
      out[did] = JSON.parse(JSON.stringify(draft.routes[did]));
      continue;
    }
    const seq = buildMissionSequence(did);
    const remaining = seq.nodes.slice(seq.historyLength);
    if (seq.historyLength === 0) {
      if (remaining.length >= 2) out[did] = await evaluateDroneRoute(did, remaining, req);
      continue;
    }
    const start = String(cfg.start_airport || "");
    const route = [start, ...remaining.filter(wp => wp !== start)];
    if (start && route.length >= 2) out[did] = await evaluateDroneRoute(did, route, req);
  }
  return out;
}

/**
 * Apply route edits — the ONE path for the sequence bar and the agent's route
 * edits. edits: { did: { remaining: [...] } } (waypoints after the drone's
 * current position) or { did: { route: [...] } } (a full route, fresh mission).
 * Other drones keep their current plan. The result becomes a normal draft
 * (frozen history, diamonds, Accept) through applySolveResult.
 */
async function applyRouteEdits(edits) {
  // Same gate as Run Planner and the agent: a route can change when a solve is
  // allowed (idle, draft on screen, cut waiting) or mid-flight when paused.
  const perms = getUiPermissions();
  if (!perms?.canSolve && !perms?.canAgentContinue) {
    throw new Error(perms?.isAnimating
      ? "Pause the mission before changing a route."
      : "Routes can't be changed in this state — pause during flight (or cut with C) first.");
  }
  if (perms?.canAgentContinue) {
    // Paused / live-editing mid-flight: make the cut first, exactly as Edit→Done does.
    await prepareAgentContinueCut();
  }
  const req = buildSolveRequest();
  const revisionAtRequest = missionState.revision;
  const routes = {};
  for (const [did, edit] of Object.entries(edits || {})) {
    const cfg = req.configsToSolve?.[did];
    if (!cfg || cfg.enabled === false) throw new Error(`D${did} is not an enabled drone`);
    let route;
    if (edit.route) {
      route = edit.route.map(String);
    } else {
      const start = String(cfg.start_airport || "");
      route = [start, ...(edit.remaining || []).map(String).filter(wp => wp !== start)];
    }
    routes[did] = await evaluateDroneRoute(did, route, req);
  }
  Object.assign(routes, await currentPlanRoutes(req, routes));
  if (missionState.revision !== revisionAtRequest) {
    throw new Error("the mission changed while the route was being computed — not applied");
  }
  await applySolveResult(routeEditResult(routes), req, { allowClientInsert: false });
  return routes;
}

// Package explicit routes as the result shape applySolveResult expects.
function routeEditResult(routes) {
  const sequences = {};
  const allocations = {};
  for (const [did, rd] of Object.entries(routes)) {
    sequences[did] = (rd.route || []).join(",");
    allocations[did] = (rd.route || []).filter(wp => String(wp).startsWith("T"));
  }
  return {
    routes,
    sequences,
    allocations,
    wrapped_polygons: state.wrappedPolygons || [],
    allocation_strategy: state.allocationStrategy || null,
    distance_matrix: state.distanceMatrix || null,
  };
}

function attachSequenceBar() {
  const droneSel = $("sequence-drone-select");
  const seqInput = $("sequence-input");
  const applyBtn = $("sequence-apply-btn");

  if (!droneSel || !seqInput || !applyBtn) return;

  droneSel.addEventListener("change", () => {
    state.currentDroneForSeq = droneSel.value;
    seqInput.value = missionSequenceText(state.currentDroneForSeq);
  });

  applyBtn.addEventListener("click", async () => {
    const droneId = String(state.currentDroneForSeq || "");
    const typed = seqInput.value.split(",").map(x => x.trim().toUpperCase()).filter(Boolean);
    if (!droneId || typed.length === 0) {
      appendDebugLine(`Empty sequence for D${droneId}`);
      return;
    }
    if (!state.env) {
      appendDebugLine("No environment loaded - cannot apply sequence");
      return;
    }
    // The flown part (up to the latest diamond) is history and can't be edited.
    const seq = buildMissionSequence(droneId);
    const history = seq.labels.slice(0, seq.historyLength);
    const typedHistory = typed.slice(0, seq.historyLength);
    if (history.join(",") !== typedHistory.join(",")) {
      appendDebugLine(`❌ D${droneId}: the flown part ${history.join(",")} can't be changed — edit only what comes after ${history[history.length - 1]}.`);
      seqInput.value = seq.labels.join(",");
      return;
    }
    const rest = typed.slice(seq.historyLength);
    const edit = seq.historyLength === 0 ? { route: rest } : { remaining: rest };
    appendDebugLine(`Applying route edit for D${droneId}: ${typed.join(",")}`);
    applyBtn.disabled = true;
    try {
      await applyRouteEdits({ [droneId]: edit });
      appendDebugLine(`✅ D${droneId} route applied — review, then Accept`);
    } catch (err) {
      appendDebugLine(`❌ Route edit failed: ${err.message}`);
    } finally {
      applyBtn.disabled = false;
      refreshSequenceBox();
    }
  });
}

// ----------------------------------------------------
// Optimizer Undo (one level)
// ----------------------------------------------------
let _optimizerUndo = null;

function saveOptimizerSnapshot() {
  _optimizerUndo = {
    routes: JSON.parse(JSON.stringify(state.routes || {})),
    sequences: JSON.parse(JSON.stringify(state.sequences || {})),
  };
  appendDebugLine("📸 Optimizer snapshot saved (press Z to undo)");
}

function undoOptimizer() {
  if (!_optimizerUndo) {
    appendDebugLine("⚠️ No optimizer to undo");
    return;
  }
  state.routes = _optimizerUndo.routes;
  state.sequences = _optimizerUndo.sequences;
  _optimizerUndo = null;

  // Update sequence display
  const curDrone = state.currentDroneForSeq || "1";
  const seqInput = $("sequence-input");
  if (seqInput && state.sequences[curDrone]) {
    seqInput.value = state.sequences[curDrone];
  }

  drawEnvironment();
  updateStatsFromRoutes();
  appendDebugLine("↩️ Optimizer undone — restored previous routes");
}

/**
 * Snapshot refueling metadata from current state.routes.
 * Call BEFORE overwriting state.routes with optimizer output.
 */
function snapshotRefuelMeta() {
  const meta = {};
  for (const [did, r] of Object.entries(state.routes || {})) {
    if (r.refuel_airport) {
      meta[did] = {
        refuel_airport: r.refuel_airport,
        leg1_distance: r.leg1_distance,
        leg2_distance: r.leg2_distance,
      };
    }
  }
  return meta;
}

/**
 * Recalculate refueling metadata on new routes after optimizer reorders them.
 * Finds the refuel airport in the route, splits into legs, and sums distances.
 */
function restoreRefuelMeta(routes, meta) {
  for (const [did, m] of Object.entries(meta)) {
    if (!routes[did]) continue;
    const route = routes[did].route || [];
    const refuelAp = m.refuel_airport;
    routes[did].refuel_airport = refuelAp;

    // Find refuel airport in the route (not first or last position)
    let refuelIdx = -1;
    for (let i = 1; i < route.length - 1; i++) {
      if (route[i] === refuelAp) {
        refuelIdx = i;
        break;
      }
    }

    if (refuelIdx === -1) {
      // Refuel airport not found in middle of route — keep old values
      routes[did].leg1_distance = m.leg1_distance;
      routes[did].leg2_distance = m.leg2_distance;
      continue;
    }

    // Recalculate leg distances from waypoint positions (lat/lng, distances in km)
    const waypoints = {};
    if (state.env) {
      for (const a of (state.env.airports || [])) {
        waypoints[a.id || a.label] = [a.lat, a.lng];
      }
      for (const t of (state.env.targets || [])) {
        waypoints[t.id || t.label] = [t.lat, t.lng];
      }
    }

    let leg1Dist = 0;
    for (let i = 0; i < refuelIdx; i++) {
      const from = waypoints[route[i]];
      const to = waypoints[route[i + 1]];
      if (from && to) {
        // Use haversine for km distance
        leg1Dist += haversineKm(from[0], from[1], to[0], to[1]);
      }
    }

    let leg2Dist = 0;
    for (let i = refuelIdx; i < route.length - 1; i++) {
      const from = waypoints[route[i]];
      const to = waypoints[route[i + 1]];
      if (from && to) {
        // Use haversine for km distance
        leg2Dist += haversineKm(from[0], from[1], to[0], to[1]);
      }
    }

    routes[did].leg1_distance = leg1Dist;
    routes[did].leg2_distance = leg2Dist;

    // Warn if a leg exceeds fuel budget after optimization
    const budget = Number(state.droneConfigs?.[did]?.fuel_budget || routes[did].fuel_budget || 0);
    if (budget > 0) {
      if (leg1Dist > budget) {
        appendDebugLine(`⚠️ D${did} Leg 1 exceeds fuel budget after optimization: ${leg1Dist.toFixed(1)} > ${budget}`);
      }
      if (leg2Dist > budget) {
        appendDebugLine(`⚠️ D${did} Leg 2 exceeds fuel budget after optimization: ${leg2Dist.toFixed(1)} > ${budget}`);
      }
    }
  }
}

// ----------------------------------------------------
// Loiter tab UI
// ----------------------------------------------------

// ----------------------------------------------------
// Target categories UI: the Assets-tab table, each drone's category rows, the
// map legend, the Edit tool's Category menu and the map hover label.
// ----------------------------------------------------
function categoryLetter(id) {
  return String(id || "a").toUpperCase();
}

// The mission's own list, created from the current one on first edit.
function editableCategoryList() {
  const env = getEditableEnv();
  if (!env) return null;
  if (!Array.isArray(env.target_categories) || !env.target_categories.length) {
    env.target_categories = JSON.parse(JSON.stringify(targetCategories()));
  }
  return env.target_categories;
}

function categoryTargetCount(id) {
  const key = String(id).toLowerCase();
  return getAllKnownTargets().filter((t) => String(t.type || "a").toLowerCase() === key).length;
}

// The "From" column: where a drone's figures for category t come from.
// - EOIR (etc.): its best catalog sensor for t (server/asset_catalog.py rule),
//   and the drone's values ARE that sensor's figures.
// - own value: they differ (edited, or saved from an older catalog); the
//   tooltip gives the catalog's figures. Choosing the Type again reloads them.
// - by hand: a Custom drone. off: not visited.
function categorySourceHtml(cfg, t) {
  if (!cfg?.platform_id) return '<span class="cat-src-hand">by hand</span>';
  const sensors = (state.assetCatalog?.sensors || []).filter((s) => (cfg.sensor_ids || []).includes(s.id));
  let best = null;
  sensors.forEach((s) => {
    const v = (s.types || {})[t] || (s.types || {}).all;
    if (!v) return;
    if (!best || v.detection_km > best.v.detection_km ||
        (v.detection_km === best.v.detection_km && v.loiter_min < best.v.loiter_min)) best = { s, v };
  });
  const on = !!(cfg.target_access || {})[t];
  if (!on) {
    return best
      ? `<span class="cat-src-hand" title="${escapeHtml(best.s.id)} could see it: ${best.v.detection_km} km, ${best.v.loiter_min} min">off</span>`
      : '<span class="cat-src-none">no sensor covers it</span>';
  }
  if (!best) return '<span class="cat-src-hand" title="None of its sensors covers this category">own value</span>';
  const r = Number((cfg.detection_radius || {})[t] || 0), l = Number((cfg.loiter_times || {})[t] || 0);
  const matches = Math.abs(r - best.v.detection_km) < 1e-6 && Math.abs(l - best.v.loiter_min) < 1e-6;
  return matches
    ? `<span class="cat-src" title="${escapeHtml(best.s.name || best.s.id)} (nominal)">${escapeHtml(best.s.id)}</span>`
    : `<span class="cat-src-hand" title="Catalog: ${escapeHtml(best.s.id)} ${best.v.detection_km} km, ${best.v.loiter_min} min. Choose the Type again to use it.">own value</span>`;
}

// Each drone drawer's rows: Visit / category / Detect km / Loiter min / From.
// Rebuilt only when the category list changes; values come from the config.
let _categoryRowsKey = null;
function renderCategoryRows() {
  const cats = targetCategories();
  const key = cats.map((c) => `${c.id}:${c.name}:${categoryColor(c.id)}`).join("|");
  if (key === _categoryRowsKey) return;
  _categoryRowsKey = key;
  const configs = getEditableDroneConfigs() || state.droneConfigs || {};
  for (let did = 1; did <= 5; did++) {
    const box = $(`cat-rows-d${did}`);
    if (!box) continue;
    const cfg = configs[String(did)] || {};
    box.innerHTML = `<table class="cat-drone-table"><thead><tr><th>Visit</th><th>Category</th>
        <th class="num">Detect km</th><th class="num">Loiter min</th><th>From</th></tr></thead><tbody>${
      cats.map((c) => {
        const t = String(c.id).toLowerCase();
        const on = !!(cfg.target_access || {})[t];
        return `<tr>
          <td><input id="cfg-d${did}-type-${t}" type="checkbox"${on ? " checked" : ""} title="D${did} visits ${escapeHtml(c.name)} targets" /></td>
          <td class="cat-name"><span class="cat-dot" style="background:${categoryColor(t)}"></span>${categoryLetter(t)} ${escapeHtml(c.name)}</td>
          <td class="num"><input id="detect-d${did}-type-${t}" type="number" min="0" step="0.5" class="loiter-input"
            value="${(cfg.detection_radius || {})[t] || 0}" title="Detection radius (km): the target counts as visited when D${did} passes this close. 0 = fly over it." /></td>
          <td class="num"><input id="loiter-d${did}-type-${t}" type="number" min="0" class="loiter-input"
            value="${(cfg.loiter_times || {})[t] || 0}" title="Loiter time per visit, minutes; above 0 = D${did} loiters there. Fuel = minutes × speed (the orbit it flies)." /></td>
          <td class="cat-from" id="from-d${did}-type-${t}">${categorySourceHtml(cfg, t)}</td>
        </tr>`;
      }).join("")}</tbody></table>`;
    attachCategoryInputs(did);
    if (typeof markEditedFields === "function" && cfg.preset) markEditedFields(did);
  }
}

function attachCategoryInputs(did) {
  const idStr = String(did);
  categoryIds().forEach((t) => {
    const cb = $(`cfg-d${did}-type-${t}`);
    if (cb) {
      cb.addEventListener("change", () => {
        const cfg = getEditableDroneConfigs()[idStr];
        cfg.target_access = { ...(cfg.target_access || {}), [t]: cb.checked };
        updateLoiterTabUI();
        updateDetectionRadiusTabUI();
        updateTimeSyncMemberUI();
        invalidateMission(`Drone ${idStr} target_access changed`);
      });
    }
    // A loiter time above 0 turns loitering on for that drone — no separate switch.
    const loiter = $(`loiter-d${did}-type-${t}`);
    if (loiter) {
      loiter.addEventListener("change", () => {
        const cfg = getEditableDroneConfigs()[idStr];
        cfg.loiter_times = withAllCategories(cfg.loiter_times, 0);
        cfg.loiter_times[t] = parseFloat(loiter.value) || 0;
        cfg.loiter_enabled = loiterIsOn(cfg.loiter_times);
        invalidateMission(`Drone ${idStr} loiter time changed`);
      });
    }
    const detect = $(`detect-d${did}-type-${t}`);
    if (detect) {
      detect.addEventListener("change", () => {
        const cfg = getEditableDroneConfigs()[idStr];
        cfg.detection_radius = withAllCategories(cfg.detection_radius, 0);
        cfg.detection_radius[t] = parseFloat(detect.value) || 0;
      });
    }
  });
}

// The Assets tab's categories table: rename, add, remove an unused one.
let categoriesFolded = false;
try { categoriesFolded = localStorage.getItem("isr.categoriesFolded") === "1"; } catch (e) { /* no storage */ }

function renderCategoriesTable() {
  const body = $("cat-table-body");
  if (!body) return;
  const cats = targetCategories();
  body.innerHTML = cats.map((c, i) => {
    const n = categoryTargetCount(c.id);
    return `<tr>
      <td class="cat-key"><span class="cat-dot" style="background:${categoryColor(c.id)}"></span><b>${categoryLetter(c.id)}</b></td>
      <td><input class="cat-name-input" data-cat="${i}" value="${escapeHtml(c.name || "")}" title="Rename ${categoryLetter(c.id)}" /></td>
      <td class="cat-examples">${escapeHtml(c.examples || "")}</td>
      <td class="cat-count">${n} target${n === 1 ? "" : "s"}</td>
      <td><button type="button" class="cat-remove" data-cat="${i}" ${n ? "disabled" : ""}
        title="${n ? "Targets use it: change their category first" : `Remove ${categoryLetter(c.id)}`}">✕</button></td>
    </tr>`;
  }).join("");
  body.querySelectorAll(".cat-name-input").forEach((input) => {
    input.addEventListener("input", () => {
      const list = editableCategoryList();
      if (!list) return;
      list[Number(input.dataset.cat)].name = input.value;
      refreshCategoryDisplays({ table: false });
    });
  });
  body.querySelectorAll(".cat-remove").forEach((btn) => {
    btn.addEventListener("click", () => removeTargetCategory(Number(btn.dataset.cat)));
  });
  const add = $("cat-add");
  if (add) add.disabled = cats.length >= MAX_TARGET_CATEGORIES;
  const note = $("cat-note");
  if (note) {
    note.textContent = cats.length >= MAX_TARGET_CATEGORIES
      ? `Up to ${MAX_TARGET_CATEGORIES} categories, so the map colours stay distinct.`
      : 'Sensor figures per category come from the catalog ("nominal").';
  }
  const folded = $("cat-folded");
  if (folded) {
    folded.innerHTML = cats.map((c) =>
      `<span class="cat-dot" style="background:${categoryColor(c.id)}"></span>${categoryLetter(c.id)} ${escapeHtml(c.name || "")}`).join(" &nbsp;·&nbsp; ");
    folded.hidden = !categoriesFolded;
  }
  const bodyBox = $("cat-body");
  if (bodyBox) bodyBox.hidden = categoriesFolded;
  const fold = $("cat-fold");
  if (fold) fold.textContent = categoriesFolded ? `▸ show (${cats.length})` : "▾ hide";
}

// The table's "On map" counts and ✕ buttons, as targets are added or deleted
// (without rebuilding the table, so a name being typed keeps its focus).
function updateCategoryCounts() {
  const cats = targetCategories();
  document.querySelectorAll("#cat-table-body tr").forEach((row, i) => {
    const c = cats[i];
    if (!c) return;
    const n = categoryTargetCount(c.id);
    const cell = row.querySelector(".cat-count");
    if (cell) cell.textContent = `${n} target${n === 1 ? "" : "s"}`;
    const btn = row.querySelector(".cat-remove");
    if (btn) {
      btn.disabled = n > 0;
      btn.title = n ? "Targets use it: change their category first" : `Remove ${categoryLetter(c.id)}`;
    }
  });
}

function addTargetCategory() {
  const list = editableCategoryList();
  if (!list || list.length >= MAX_TARGET_CATEGORIES) return;
  const used = new Set(list.map((c) => String(c.id).toLowerCase()));
  const id = "abcdefghijklmnopqrstuvwxyz".split("").find((l) => !used.has(l));
  const usedColors = new Set(list.map((c) => c.color));
  const color = CATEGORY_PALETTE.find((c) => !usedColors.has(c)) || CATEGORY_PALETTE[list.length % CATEGORY_PALETTE.length];
  list.push({ id, name: "New category", examples: "", color });
  // Drones: a Custom drone visits it; a catalog aircraft's sensors have no
  // figures for it, so it doesn't (until you tick it).
  Object.values(getEditableDroneConfigs() || {}).forEach((cfg) => {
    if (!cfg) return;
    cfg.target_access = { ...(cfg.target_access || {}), [id]: !cfg.platform_id };
    cfg.detection_radius = { ...(cfg.detection_radius || {}), [id]: 0 };
    cfg.loiter_times = { ...(cfg.loiter_times || {}), [id]: 0 };
  });
  refreshCategoryDisplays();
  const inputs = document.querySelectorAll("#cat-table-body .cat-name-input");
  inputs[inputs.length - 1]?.select();
}

function removeTargetCategory(index) {
  const list = editableCategoryList();
  const cat = list?.[index];
  if (!cat || categoryTargetCount(cat.id)) return;
  const id = String(cat.id).toLowerCase();
  list.splice(index, 1);
  Object.values(getEditableDroneConfigs() || {}).forEach((cfg) => {
    ["target_access", "detection_radius", "loiter_times"].forEach((k) => {
      if (cfg?.[k]) delete cfg[k][id];
    });
  });
  refreshCategoryDisplays();
}

// The map legend (HTML over the canvas's top-right corner): dot, letter, name.
let mapLegendShort = false;
try { mapLegendShort = localStorage.getItem("isr.legendShort") === "1"; } catch (e) { /* no storage */ }
let _mapLegendKey = null;
function renderMapLegend() {
  const legend = $("map-legend");
  const canvas = $("env-canvas");
  if (!legend || !canvas) return;
  const cesium = $("cesium-container");
  legend.hidden = !!cesium && cesium.style.display !== "none";
  const key = `${mapLegendShort}|` + targetCategories().map((c) => `${c.id}:${c.name}:${categoryColor(c.id)}`).join("|");
  if (key !== _mapLegendKey) {
    _mapLegendKey = key;
    legend.innerHTML = targetCategories().map((c) =>
      `<div><span class="cat-dot" style="background:${categoryColor(c.id)}"></span><b>${categoryLetter(c.id)}</b>${
        mapLegendShort ? "" : `<span class="legend-name">${escapeHtml(c.name || "")}</span>`}</div>`).join("");
    legend.classList.toggle("short", mapLegendShort);
  }
  legend.style.top = `${canvas.offsetTop + 8}px`;
  legend.style.left = `${canvas.offsetLeft + canvas.clientWidth - legend.offsetWidth - 8}px`;
}

function renderCategorySelect() {
  const sel = $("new-target-type");
  if (!sel) return;
  const keep = sel.value;
  sel.innerHTML = targetCategories().map((c) =>
    `<option value="${escapeHtml(String(c.id).toLowerCase())}">${categoryLetter(c.id)} — ${escapeHtml(c.name || "")}</option>`).join("");
  if (keep && categoryIds().includes(keep)) sel.value = keep;
}

// Everything that shows the categories, after the list changed.
function refreshCategoryDisplays({ table = true } = {}) {
  if (table) renderCategoriesTable();
  else {
    const folded = $("cat-folded");
    if (folded) folded.innerHTML = targetCategories().map((c) =>
      `<span class="cat-dot" style="background:${categoryColor(c.id)}"></span>${categoryLetter(c.id)} ${escapeHtml(c.name || "")}`).join(" &nbsp;·&nbsp; ");
  }
  renderCategoryRows();
  renderMapLegend();
  renderCategorySelect();
  updateLoiterTabUI();
  updateDetectionRadiusTabUI();
  drawEnvironment();
}

// Hovering a target on the map: "T7 · D Emitters / radar sites · priority 9".
// Listens on the page: in map mode the canvas lets the mouse through to the map.
function attachTargetHover() {
  const tip = $("target-hover");
  const canvas = $("env-canvas");
  if (!tip || !canvas) return;
  let pending = null;
  document.addEventListener("mousemove", (e) => {
    pending = e;
    requestAnimationFrame(() => {
      if (!pending) return;
      const ev = pending;
      pending = null;
      const rect = canvas.getBoundingClientRect();
      const cesium = $("cesium-container");
      const inside = rect.width > 0 && ev.clientX >= rect.left && ev.clientX <= rect.right &&
        ev.clientY >= rect.top && ev.clientY <= rect.bottom && !(cesium && cesium.style.display !== "none");
      if (!inside) { tip.hidden = true; return; }
      const sx = canvas.width / rect.width, sy = canvas.height / rect.height;
      const mx = (ev.clientX - rect.left) * sx, my = (ev.clientY - rect.top) * sy;
      let best = null, bestD = 10 * sx;
      targetScreenPositions.forEach((p) => {
        const d = Math.hypot(p.x - mx, p.y - my);
        if (d < bestD) { bestD = d; best = p; }
      });
      if (!best) { tip.hidden = true; return; }
      const t = best.t;
      const type = String(t.type || "a").toLowerCase();
      tip.innerHTML = `<b>${escapeHtml(String(t.id))}</b> · <span style="color:${categoryColor(type)}">${categoryLetter(type)} ${escapeHtml(categoryName(type))}</span> · priority ${t.priority ?? 5}`;
      tip.hidden = false;
      tip.style.left = `${canvas.offsetLeft + best.x / sx + 12}px`;
      tip.style.top = `${canvas.offsetTop + best.y / sy - 28}px`;
    });
  });
}

function attachTargetCategories() {
  $("cat-add")?.addEventListener("click", addTargetCategory);
  $("cat-fold")?.addEventListener("click", () => {
    categoriesFolded = !categoriesFolded;
    try { localStorage.setItem("isr.categoriesFolded", categoriesFolded ? "1" : "0"); } catch (e) { /* no storage */ }
    renderCategoriesTable();
  });
  $("map-legend")?.addEventListener("click", () => {
    mapLegendShort = !mapLegendShort;
    try { localStorage.setItem("isr.legendShort", mapLegendShort ? "1" : "0"); } catch (e) { /* no storage */ }
    renderMapLegend();
  });
  attachTargetHover();
  refreshCategoryDisplays();
}

function readLoiterTimesForDrone(did) {
  const times = {};
  categoryIds().forEach(t => {
    const input = $(`loiter-d${did}-type-${t}`);
    times[t] = input ? parseFloat(input.value) || 0 : 0;
  });
  return times;
}

function readDetectionRadiusForDrone(did) {
  const radii = {};
  categoryIds().forEach(t => {
    const input = $(`detect-d${did}-type-${t}`);
    radii[t] = input ? parseFloat(input.value) || 0 : 0;
  });
  return radii;
}

function updateLoiterTabUI() {
  renderCategoryRows();
  for (let did = 1; did <= 5; did++) {
    const idStr = String(did);
    const cfg = state.droneConfigs[idStr] || {};
    // Loiter times are editable whenever the drone is on (a time > 0 = loiter).
    const isActive = cfg.enabled !== false;

    // Set each category's input
    categoryIds().forEach(t => {
      const input = $(`loiter-d${did}-type-${t}`);
      if (!input) return;

      const typeEnabled = isActive && cfg.target_access && cfg.target_access[t];
      input.disabled = !typeEnabled;
      input.value = (cfg.loiter_times && cfg.loiter_times[t]) || 0;
    });
  }
  updateFleetRowStates();
}

function updateDetectionRadiusTabUI() {
  renderCategoryRows();
  for (let did = 1; did <= 5; did++) {
    const idStr = String(did);
    const cfg = state.droneConfigs[idStr] || {};
    const isActive = cfg.enabled !== false;

    // Set each category's input, and which sensor its figures come from
    categoryIds().forEach(t => {
      const input = $(`detect-d${did}-type-${t}`);
      if (!input) return;

      const typeEnabled = isActive && cfg.target_access && cfg.target_access[t];
      input.disabled = !typeEnabled;
      input.value = (cfg.detection_radius && cfg.detection_radius[t]) || 0;
      const from = $(`from-d${did}-type-${t}`);
      if (from) from.innerHTML = categorySourceHtml(cfg, t);
    });
  }
}

// ----------------------------------------------------
// Refuel-stops grid (multi-stop). 4 slots per drone; slot N+1 unlocks after N.
// ----------------------------------------------------
const REFUEL_MAX_STOPS = 4;

// Opportunistic refuel: a CHECKBOX grid. Per drone, tick which airports/tankers
// it MAY refuel at (the SET = cfg.refuel_options); the solver decides whether/
// when/how-many. Incompatible airports (runway too short) are greyed; tankers
// (no runway) are always checkable. A per-drone "Max" caps the refuel count.
function populateRefuelStopsBox(opts = {}) {
  const airports = (state.env && state.env.airports) || [];
  const tankerIds = new Set(
    airports.filter((a) => a.type === "tanker").map((a) => String(a.id))
  );
  const airportRunway = new Map(
    airports
      .filter((a) => a.type !== "tanker")
      .map((a) => [String(a.id), (a.runway || "L").toUpperCase()])
  );
  // Refuel points = every airport + tanker, stable order (airports then tankers).
  const points = airports.map((a) => String(a.id));
  const isTanker = (aid) => tankerIds.has(aid);
  // A drone may refuel at: a tanker within reach of its altitude, or an
  // airport whose runway it can use.
  const tankerAlt = new Map(
    airports.filter((a) => a.type === "tanker").map((a) => [String(a.id), a.alt_km])
  );
  const tankerInReach = (aid, cfg) => {
    const alt = tankerAlt.get(aid);
    if (alt == null) return true;
    const ceiling = droneCeilingKm(cfg);
    if (ceiling != null && Number(alt) > ceiling + 1e-9) return false;  // can't climb that high
    return Math.abs(Number(alt) - droneCruiseAltitudeKm(cfg)) <= MAX_TANKER_ALTITUDE_CHANGE_KM + 1e-9;
  };
  const compatible = (aid, droneRwy, cfg) =>
    isTanker(aid)
      ? tankerInReach(aid, cfg)
      : isAirportCompatibleWithDrone(airportRunway.get(aid), droneRwy);

  // One row of chips + Max per drone, inside its drawer (Asset tab).
  for (let did = 1; did <= 5; did++) {
    const box = $(`refuel-chips-d${did}`);
    if (!box) continue;
    const cfg = state.droneConfigs[String(did)] || {};
    const droneRwy = (cfg.runway || "S").toUpperCase();
    const selected = new Set(
      Array.isArray(cfg.refuel_options) ? cfg.refuel_options.map(String) : []
    );
    let html = "";
    for (const aid of points) {
      const ok = compatible(aid, droneRwy, cfg);
      const title = isTanker(aid)
        ? (ok ? `tanker at ${tankerAlt.get(aid) ?? "?"} km`
              : (droneCeilingKm(cfg) != null && Number(tankerAlt.get(aid)) > droneCeilingKm(cfg)
                  ? `tanker at ${tankerAlt.get(aid)} km: above this drone's ${droneCeilingKm(cfg)} km ceiling`
                  : `tanker at ${tankerAlt.get(aid)} km: more than ${MAX_TANKER_ALTITUDE_CHANGE_KM} km from this drone's ${droneCruiseAltitudeKm(cfg)} km cruise`))
        : (ok ? `runway ${airportRunway.get(aid)}`
              : `runway ${airportRunway.get(aid)} too short for this drone (needs ${droneRwy})`);
      html += `<button type="button" class="refuel-chip${isTanker(aid) ? " tanker" : ""}" `
        + `data-did="${did}" data-ap="${escapeHtml(aid)}" aria-pressed="${selected.has(aid) && ok}" `
        + `title="${escapeHtml(title)}" ${(!cfg.enabled || !ok) ? "disabled" : ""}>${escapeHtml(aid)}</button>`;
    }
    if (!points.length) html += '<span class="fleet-hint">No airports or tankers yet</span>';
    const maxVal = (cfg.max_refuels != null) ? cfg.max_refuels : 4;
    html += `<label class="refuel-max-label" title="Most refuels D${did} may make">Max `
      + `<input type="number" min="0" step="1" class="config-input refuel-max" data-did="${did}" `
      + `value="${maxVal}" ${cfg.enabled ? "" : "disabled"}></label>`;
    box.innerHTML = html;

    box.querySelectorAll(".refuel-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        const on = chip.getAttribute("aria-pressed") !== "true";
        chip.setAttribute("aria-pressed", String(on));
        onRefuelOptionToggle(chip.dataset.did, chip.dataset.ap, on);
      });
    });
    box.querySelectorAll(".refuel-max").forEach((inp) => {
      inp.addEventListener("change", () => onRefuelMaxChange(inp.dataset.did, inp.value));
    });
  }
  // Tankers come and go with the same env changes that rebuild the chips
  // (skipped while a tanker's own altitude box is being typed in).
  if (opts.tankers !== false) populateTankerTable();
}

// ----------------------------------------------------
// Tankers: holding altitude (km above sea level) + clearance above the ground
// under them. One altitude per tanker, shown in the Asset-tab Tankers table and,
// while that tanker is selected, in the Edit panel's altitude box.
// ----------------------------------------------------
// Terrain clearance margin: 2,000 ft, the FAA minimum over mountainous areas
// (14 CFR 91.177). One setting for every terrain check.
const TERRAIN_MARGIN_KM = 0.6;

function newTankerAltitudeKm() {
  const box = $("tanker-alt");
  const v = box ? parseFloat(box.value) : NaN;
  return Number.isFinite(v) && v >= 0 ? v : 4;
}

function selectedTanker() {
  const sel = state.selectedObject;
  if (!sel || sel.kind !== "airport") return null;
  const a = (getEditableEnv()?.airports || [])[sel.index];
  return a && a.type === "tanker" ? a : null;
}

function tankerClearanceHtml(t) {
  if (t.alt_km == null || t.ground_km == null) return '<span class="fleet-hint">–</span>';
  const c = Math.round((Number(t.alt_km) - Number(t.ground_km)) * 10) / 10;
  if (c < 0) return `<span class="clearance-bad">${c.toFixed(1)} km · below ground</span>`;
  if (c < TERRAIN_MARGIN_KM) {
    return `<span class="clearance-warn">${c.toFixed(1)} km · under ${TERRAIN_MARGIN_KM} km margin</span>`;
  }
  return `${c.toFixed(1)} km`;
}

// Change one tanker's altitude from either box; the other box follows.
function setTankerAltitude(tanker, value) {
  const v = parseFloat(value);
  if (!tanker || !Number.isFinite(v) || v < 0) return;
  tanker.alt_km = v;
  const row = document.querySelector(`#tanker-table [data-tanker="${CSS.escape(String(tanker.id))}"]`);
  if (row) {
    const input = row.querySelector("input");
    if (input && document.activeElement !== input) input.value = v;
    const cell = row.querySelector(".tanker-clearance");
    if (cell) cell.innerHTML = tankerClearanceHtml(tanker);
  }
  const box = $("tanker-alt");
  if (box && selectedTanker() === tanker && document.activeElement !== box) box.value = v;
  populateRefuelStopsBox({ tankers: false });  // which drones can reach it
  if (cesiumView.isActive()) cesiumView.render();
  invalidateMission(`Tanker ${tanker.id} altitude changed`);
}

function populateTankerTable() {
  const table = $("tanker-table");
  if (!table) return;
  const note = $("tanker-margin-note");
  if (note) note.textContent = `terrain margin ${TERRAIN_MARGIN_KM} km`;
  const tankers = (getEditableEnv()?.airports || []).filter((a) => a.type === "tanker");
  if (!tankers.length) {
    table.innerHTML = '<div class="tanker-empty">No tankers. Place one with the Edit panel\'s Tanker button.</div>';
    return;
  }
  table.innerHTML = `<table class="fleet-table"><thead><tr><th>Tanker</th><th>Alt km</th><th>Above ground</th></tr></thead><tbody>
    ${tankers.map((t) => `<tr data-tanker="${escapeHtml(String(t.id))}">
      <td class="fleet-name"><span class="tanker-diamond"></span>${escapeHtml(String(t.id))}</td>
      <td><input type="number" min="0" step="0.1" class="config-input" style="max-width:4.2rem" value="${t.alt_km ?? ""}" title="${escapeHtml(String(t.id))} holding altitude (km above sea level)"></td>
      <td class="tanker-clearance">${tankerClearanceHtml(t)}</td></tr>`).join("")}
  </tbody></table>`;
  table.querySelectorAll("tr[data-tanker]").forEach((row) => {
    const tanker = tankers.find((t) => String(t.id) === row.dataset.tanker);
    row.querySelector("input").addEventListener("input", (e) => setTankerAltitude(tanker, e.target.value));
  });
}

// The Edit panel's altitude box: the selected tanker's altitude, else the
// altitude for the next tanker placed.
function attachTankerAltitudeBox() {
  const box = $("tanker-alt");
  if (!box) return;
  box.addEventListener("input", () => {
    const tanker = selectedTanker();
    if (tanker) setTankerAltitude(tanker, box.value);
  });
}

let _tankerBoxShows = null;
function syncTankerAltBox() {
  updateEditHint();
  const box = $("tanker-alt");
  if (!box) return;
  const tanker = selectedTanker();
  if (tanker === _tankerBoxShows) return;
  _tankerBoxShows = tanker;
  if (tanker && tanker.alt_km != null && document.activeElement !== box) box.value = tanker.alt_km;
  box.title = tanker
    ? `${tanker.id} altitude (km above sea level)`
    : "Altitude for the next tanker you place (km above sea level)";
}

function onRefuelOptionToggle(did, ap, checked) {
  const cfg = getEditableDroneConfigs()[String(did)];
  if (!cfg) return;
  if (!Array.isArray(cfg.refuel_options)) cfg.refuel_options = [];
  const set = new Set(cfg.refuel_options.map(String));
  if (checked) set.add(String(ap)); else set.delete(String(ap));
  cfg.refuel_options = [...set];
  // Opportunistic grid is the source of truth; clear legacy prescriptive fields
  // so they don't also drive the solve.
  cfg.refuel_stops = [];
  cfg.refuel_airport = "";
  invalidateMission(`Drone ${did} refuel_options changed`);
}

function onRefuelMaxChange(did, value) {
  const cfg = getEditableDroneConfigs()[String(did)];
  if (!cfg) return;
  const n = Math.max(0, parseInt(value, 10) || 0);
  cfg.max_refuels = n;
  invalidateMission(`Drone ${did} max_refuels changed`);
}

// ----------------------------------------------------
// Stats update
// ----------------------------------------------------

/**
 * Returns true if drone `did` is recorded as lost in any segment of
 * the current missionReplay (i.e., disabled/killed mid-mission).
 *
 * Operationally: a lost drone forfeits its ENTIRE assigned fuel tank,
 * not just the fuel burned to the kill position — the unburned remainder
 * goes down with the drone. The stats functions use this to charge full
 * budget for lost drones in the mission total.
 */
function isLostDroneById(did) {
  const idStr = String(did);
  if (typeof missionReplay === "undefined" || !missionReplay) return false;
  const segCount = missionReplay.getSegmentCount();
  for (let i = 1; i <= segCount; i++) {
    const seg = missionReplay.getSegment(i);
    if (seg?.lostDrones && seg.lostDrones.includes(idStr)) return true;
  }
  return false;
}

// ===========================================================================
// VISITED-TARGET WRITERS (§unify Phase 2). A target is visited iff the drone
// passed its vertex on the canonical distance frame; the green X reads ONLY from
// state.visitedTargets. These four functions are the ONLY writers — no raw
// `state.visitedTargets = / .push` or `state.visitedTargetByDrone = ` elsewhere.
// Each keeps the owner map in sync inline, so callers never need a separate
// rebuild.
// ===========================================================================

// (1) Single incremental writer — the drone reached this target's vertex.
function markTargetVisitedByDrone(targetId, droneId) {
  const tid = String(targetId);
  const did = String(droneId);
  if (!state.visitedTargets.includes(tid)) {
    state.visitedTargets.push(tid);
  }
  state.visitedTargetByDrone[tid] = did;
}

// (2) Reset both maps to empty (fresh mission / reset / import / replay start).
function clearVisited() {
  state.visitedTargets = [];
  state.visitedTargetByDrone = {};
}

// (3) Seed already-passed targets from stored SEGMENT data (segment switch /
// accept restore / import). Merges into the current set (idempotent) and derives
// each target's owner from state.routes, so the owner map stays correct without a
// separate rebuild call.
function seedVisitedFromSegment(targetIds) {
  const ids = Array.isArray(targetIds) ? targetIds : [];
  // Owner lookup: first drone whose route contains the target.
  const ownerOf = (tid) => {
    for (const [did, routeInfo] of Object.entries(state.routes || {})) {
      if ((routeInfo?.route || []).some((wp) => String(wp) === tid)) return String(did);
    }
    return undefined;
  };
  ids.forEach((raw) => {
    const tid = String(raw);
    if (!state.visitedTargets.includes(tid)) state.visitedTargets.push(tid);
    if (state.visitedTargetByDrone[tid] === undefined) {
      const owner = ownerOf(tid);
      if (owner !== undefined) state.visitedTargetByDrone[tid] = owner;
    }
  });
}

// (4) Mark EVERY target in the env as visited (final accept of a whole mission).
function markAllVisited(targets) {
  const list = Array.isArray(targets) ? targets : [];
  list.forEach((t) => {
    const tid = String(t.id || t.label || t);
    if (!tid || tid === "undefined") return;
    if (!state.visitedTargets.includes(tid)) state.visitedTargets.push(tid);
  });
  // Owner map follows from routes (a target may be on any drone's route).
  rebuildVisitedTargetOwnersFromRoutes();
}

function rebuildVisitedTargetOwnersFromRoutes() {
  const owners = {};
  for (const [did, routeInfo] of Object.entries(state.routes || {})) {
    for (const wp of (routeInfo?.route || [])) {
      const tid = String(wp);
      if (tid.toUpperCase().startsWith("T") && state.visitedTargets.includes(tid) && owners[tid] === undefined) {
        owners[tid] = String(did);
      }
    }
  }
  state.visitedTargetByDrone = owners;
}

function getVisitedTargetsForDrone(did, route) {
  const didStr = String(did);
  const routeTargets = new Set(
    (route || [])
      .map((wp) => String(wp))
      .filter((wp) => wp.toUpperCase().startsWith("T"))
  );

  const owned = Object.entries(state.visitedTargetByDrone || {})
    .filter(([, owner]) => String(owner) === didStr)
    .map(([tid]) => tid);

  if (owned.length > 0) {
    return owned;
  }

  return state.visitedTargets
    .map((tid) => String(tid))
    .filter((tid) => routeTargets.has(tid));
}

// Hit tolerance (km) for "drone did visit this target": the drone's detection
// radius for the target's type (the planner slides the waypoint up to r off the
// centre), with a small margin; 0 = the default vertex tolerance. The ONE rule
// every visited check uses (animation, cut, segment switch, resume).
function targetHitToleranceKm(target, did) {
  const radii = state.droneConfigs?.[String(did)]?.detection_radius || {};
  const r = Number(radii[(target?.type || "a").toLowerCase()] || 0);
  return r > 0 ? r * 1.05 : 0;
}

function getAllKnownTargets() {
  const byId = new Map();
  const addTargets = (targets) => {
    (targets || []).forEach((t) => {
      const id = String(t.id || t.label || "");
      if (id && !byId.has(id)) byId.set(id, t);
    });
  };

  addTargets(state.initialEnvSnapshot?.targets);
  addTargets(state.env?.targets);
  if (typeof missionReplay !== "undefined" && missionReplay) {
    for (let i = 0; i < missionReplay.getSegmentCount(); i++) {
      addTargets(missionReplay.getSegment(i)?.env?.targets);
    }
  }
  if (typeof segmentedImport !== "undefined" && segmentedImport?.getAllTargets) {
    addTargets(segmentedImport.getAllTargets());
  }
  return Array.from(byId.values());
}

function getRouteTargetLabels(routeInfo) {
  // Targets this drone is PLANNED to visit = the targets in its route. The route
  // is the solver's authoritative assignment, so use membership, not trajectory
  // proximity. The old version scanned for a trajectory point within 0.2 km of
  // each target center; that (a) could steal a target onto the wrong drone when
  // paths pass nearby, and (b) BROKE with detection_radius — the taut-string
  // routes the path to the circle EDGE (~r km off center), so no point lands
  // within 0.2 km and every radius target dropped out of the stats. A real
  // target id is "T" + digits (T1, T12); tankers are "TK…" and are excluded.
  const isTargetId = (s) => /^T\d+$/i.test(s);
  const seen = new Set();
  const out = [];
  for (const label of (routeInfo?.route || [])) {
    const id = String(label);
    if (isTargetId(id) && !seen.has(id)) {
      seen.add(id);
      out.push(id);
    }
  }
  return out;
}

// ----------------------------------------------------
// Env tab drone table (index.html #tab-env): one row per drone with its
// targets, points, fuel and P/F, the Show / Anim checkboxes, and a detail row
// (its targets, and each fuel leg when it refuels).
// ----------------------------------------------------
// Up to this many legs the fuel bar has one section per tank; above it the
// sections get too thin, so it is one bar with a tick at each refuel.
const FUEL_BAR_MAX_SECTIONS = 6;
const FUEL_WARN_SHARE = 0.9;  // a leg above 90% of its tank is amber

function setDroneTargetsPoints(rowId, targets, points) {
  setText(`stat-${rowId}-targets`, targets);
  setText(`stat-${rowId}-points`, points);
}

/** Where each fuel leg starts and ends: the route's non-target stops
 *  (start, refuel points, landing). Null when they don't match the legs. */
function fuelLegEnds(route, numLegs) {
  const stops = (route || []).map(String).filter((wp) => !/^T\d/.test(wp));
  return stops.length === numLegs + 1 ? stops : null;
}

/** The fuel cell: "used / range" text and a bar with one section per tank
 *  (legs = km flown on each tank; one leg = no refuel). */
function setDroneFuel(did, legs, tank, { lost = false, route = null } = {}) {
  const cell = $(`stat-d${did}-fuel`);
  if (!cell) return;
  const km = legs.map((v) => Number(v) || 0);
  const used = km.reduce((a, b) => a + b, 0);
  const n = km.length;
  const ends = fuelLegEnds(route, n);
  const legName = (i) => (ends ? `${ends[i]} → ${ends[i + 1]}, ` : "");
  const level = (r) => (lost || r > 1 + 1e-6 ? " over" : r > FUEL_WARN_SHARE ? " warn" : "");
  const fill = (r) => `<i style="width:${Math.min(100, 100 * r).toFixed(1)}%"></i>`;
  let bar;
  if (lost) {
    bar = `<span class="fuel-bars"><span class="fuel-bar over">${fill(1)}</span></span>`;
  } else if (n > FUEL_BAR_MAX_SECTIONS) {
    const ticks = km.slice(0, -1).map((_, i) =>
      `<s style="left:${((100 * (i + 1)) / n).toFixed(1)}%"></s>`).join("");
    const worst = tank > 0 ? Math.max(...km) / tank : 0;
    bar = `<span class="fuel-bars ticked"><span class="fuel-bar${level(worst)}">` +
      `${fill(tank > 0 ? used / (tank * n) : 0)}</span>${ticks}</span>`;
  } else {
    bar = `<span class="fuel-bars">${km.map((k) =>
      `<span class="fuel-bar${level(tank > 0 ? k / tank : 0)}">${fill(tank > 0 ? k / tank : 0)}</span>`).join("")}</span>`;
  }
  const T = Math.round(tank);
  const text = lost ? `💀 ${T} / ${T}` : n > 1 ? `⛽ ${Math.round(used)} / ${n}×${T}` : `${Math.round(used)} / ${T}`;
  cell.innerHTML = `<span class="fuel-cell">${bar}<span>${text}</span></span>`;
  cell.title = lost ? "Drone lost: its whole tank"
    : km.map((k, i) => `Leg ${i + 1}: ${legName(i)}${Math.round(k)} of ${T} km`).join("\n");

  const legsLine = $(`drone-legs-d${did}`);
  if (legsLine) {
    legsLine.innerHTML = !lost && n > 1
      ? km.map((k, i) => `<b>L${i + 1}</b> ${ends ? `${ends[i]}→${ends[i + 1]} ` : ""}${Math.round(k)}`).join(" · ")
      : "";
  }
}

// Detail rows the user opened or closed by clicking (overrides "Show targets").
const envRowToggled = new Set();

function refreshDroneDetailRows() {
  const showAll = $("chk-show-targets")?.checked !== false;
  for (let did = 1; did <= 5; did++) {
    const row = $(`drone-row-d${did}`);
    const detail = $(`drone-detail-d${did}`);
    if (!row || !detail) continue;
    const hasContent = Boolean($(`drone-targets-d${did}`)?.textContent);
    const open = hasContent && (showAll !== envRowToggled.has(did));
    detail.hidden = !open;
    row.classList.toggle("has-detail", hasContent);
    row.classList.toggle("open", open);
  }
}

function attachDroneTable() {
  $("chk-show-targets")?.addEventListener("change", () => {
    envRowToggled.clear();
    refreshDroneDetailRows();
  });
  for (let did = 1; did <= 5; did++) {
    $(`drone-row-d${did}`)?.addEventListener("click", (e) => {
      if (e.target.tagName === "INPUT") return;
      if (envRowToggled.has(did)) envRowToggled.delete(did);
      else envRowToggled.add(did);
      refreshDroneDetailRows();
    });
  }
  $("allocation-strategy")?.addEventListener("change", updateAllocationBadge);
}

function updateStatsFromRoutes() {
  let missionPoints = 0;
  let missionFuel = 0;
  let missionBudget = 0;
  let missionVisited = 0;

  // Target-id → priority lookup. We sum priorities of route targets to
  // compute total mission points, rather than relying on routeInfo.points
  // (which can be 0 in replanned / cleared-route scenarios).
  // For segmented imports, prefer initialEnvSnapshot — state.env may only
  // carry the displayed segment's targets.
  const priorityById = {};
  const fullTargetSource =
    state.initialEnvSnapshot?.targets ||
    (state.env && state.env.targets) ||
    [];
  fullTargetSource.forEach((t) => {
    priorityById[String(t.id || t.label)] = Number(t.priority || 5);
  });

  for (let did = 1; did <= 5; did++) {
    const idStr = String(did);
    const routeInfo = state.routes[idStr] || {};
    const droneConfig = state.droneConfigs[idStr] || {};
    const route = routeInfo.route || [];
    const distance = Number(routeInfo.distance || 0);
    // Use fuel budget from droneConfigs (Config tab) as the source of truth
    // Fall back to route info if config not available
    const budget = Number(droneConfig.fuel_budget || routeInfo.fuel_budget || 0);

    const targetLabels = getRouteTargetLabels(routeInfo);
    const targetCount = targetLabels.length;
    // Total points the drone WILL collect on this planned route
    // (sum of priorities). Used for both per-drone and mission display.
    const plannedPoints = targetLabels.reduce(
      (sum, lbl) => sum + (priorityById[lbl] || 0), 0
    );

    // A lost drone forfeits its entire tank — unburned fuel doesn't return.
    const isLost = isLostDroneById(did);
    const hasRefuel = routeInfo.refuel_airport;

    // §11.10: ONE fuel value — fuel_budget IS the per-leg tank (budget above is
    // droneConfig.fuel_budget ?? routeInfo.fuel_budget). No fuel_capacity.
    // used = Σ leg_used; budget = tank × num_legs; P/F = points / used.
    const tank = budget;
    const fuelStats = computeFuelStats(routeInfo, tank, plannedPoints);
    // §8.8: after cut(s), routeInfo.trajectory is the SEGMENT-ONLY remainder, so
    // computeFuelStats only counts the post-cut distance. Add the ALREADY-FLOWN
    // frozen history (Start→C1→…→current cut) so fuel reflects the whole mission/
    // leg. (Frozen distance is the flown past; SAM-aware enough via haversine of
    // the rendered path.)
    let frozenFlownKm = 0;
    if (typeof missionReplay !== "undefined" && missionReplay.getFrozenTrajectory) {
      const fp = missionReplay.getFrozenTrajectory(did) || [];
      for (let k = 1; k < fp.length; k++) {
        frozenFlownKm += haversineKm(fp[k-1][0], fp[k-1][1], fp[k][0], fp[k][1]);
      }
    }
    const fuelUsed = fuelStats.used + frozenFlownKm;
    const pf = fuelUsed > 0 ? plannedPoints / fuelUsed : 0;

    setDroneTargetsPoints(`d${did}`, targetCount, plannedPoints);
    // Lost: the full tank (drone went down). Multi-leg (refuel): the solver's
    // per-leg distances (getLegDistances). Single leg: whole-mission fuel used.
    setDroneFuel(did, fuelStats.numLegs > 1 ? fuelStats.legs : [fuelUsed], tank, {
      lost: isLost, route,
    });
    setText(`stat-d${did}-pf`, pf.toFixed(2));

    // Mission totals: used = Σ leg_used; budget = Σ (tank × num_legs).
    if ((route && route.length > 0) || isLost) {
      missionPoints += plannedPoints;
      if (isLost) {
        missionFuel += tank;       // forfeited tank
        missionBudget += tank;
      } else {
        missionFuel += fuelUsed;
        missionBudget += fuelStats.budget;
      }
      missionVisited += targetCount;
    }
  }

  const missionPf = missionFuel > 0 ? missionPoints / missionFuel : 0;
  setDroneTargetsPoints("mission", missionVisited, missionPoints);
  setText(
    "stat-mission-fuel",
    `${Math.round(missionFuel)} / ${Math.round(missionBudget)}`
  );
  setText("stat-mission-pf", missionPf.toFixed(2));
  updateAllocationDisplay(null, state.allocationStrategy);
}

/**
 * Update stats from actual animation state (real-time distance traveled)
 * This shows the actual fuel/distance used during animation, not the planned values
 */

function updateStatsFromAnimation() {
  if (!state.animation || !state.animation.drones) return;

  let missionDistanceTraveled = 0;
  let missionBudget = 0;
  // Mission targets / points = the SUM of the drone rows (each shows its plan
  // until it has flown, then what it visited). Every visited target has one
  // owner drone (visitedTargetByDrone), so the sum also holds across cuts.
  let missionPoints = 0;
  let missionVisited = 0;

  // Build a target-id → priority lookup so we can compute "points actually
  // collected" from the visitedTargets list. Falls back to 5 if a target's
  // priority is missing. We sum priorities for VISITED targets, not the
  // planned route's points — so the post-animation total reflects what
  // each drone really collected (relevant for lost drones, replanned
  // missions, and any case where routeInfo.points has been cleared).
  //
  // For segmented imports state.env only carries the CURRENTLY-DISPLAYED
  // segment's targets, so we prefer initialEnvSnapshot (all targets).
  const priorityById = {};
  const fullTargetSource =
    state.initialEnvSnapshot?.targets ||
    (state.env && state.env.targets) ||
    [];
  fullTargetSource.forEach((t) => {
    priorityById[String(t.id || t.label)] = Number(t.priority || 5);
  });

  for (let did = 1; did <= 5; did++) {
    const idStr = String(did);
    const droneConfig = state.droneConfigs[idStr] || {};
    const routeInfo = state.routes[idStr] || {};
    const route = routeInfo.route || [];
    const budget = Number(droneConfig.fuel_budget || routeInfo.fuel_budget || 0);
    // §11.10: ONE fuel value — fuel_budget IS the tank, CONSTANT across cuts (a
    // replan no longer overwrites it; it passes a transient start_fuel instead). So
    // budget IS the tank denominator. fuel_capacity retired.
    const tank = budget;
    const isLost = isLostDroneById(did);
    const droneState = state.animation.drones?.[idStr] || {};

    // Visited targets owned by this drone. The owner map is updated by the
    // same animation checks that mark targets on the grid, so per-drone stats
    // stay aligned with the visualization even after segment switches/replans.
    const routeTargetLabels = getRouteTargetLabels(routeInfo);
    const visitedLabels = getVisitedTargetsForDrone(did, routeTargetLabels);
    const visitedCount = visitedLabels.length;
    // Points = sum of priorities of visited targets (so partial visits and
    // lost drones contribute correctly).
    const collectedPoints = visitedLabels.reduce(
      (sum, lbl) => sum + (priorityById[lbl] || 0), 0
    );
    // "Planned points" = sum of priorities of all targets on this drone's
    // route. This is what we display on the right side of "visited / planned".
    const plannedPoints = routeTargetLabels.reduce(
      (sum, lbl) => sum + (priorityById[lbl] || 0), 0
    );

    // consumed = fuel used SINCE LEG START = legBurnedDistance. This is the
    // cut-neutral cumulative: it accumulates straight across cuts (detours on the
    // way to the airport still count toward the same leg) and resets only at a
    // REFUEL crossing. distanceTraveled resets to 0 at every segment switch, so it
    // is SEGMENT-only after a cut and must NOT be used for whole-leg fuel.
    // Prefer the DURABLE leg-fuel store (survives the startAnimation wipe and cuts);
    // fall back to the live drone value during active flight.
    const legBurned = Number(
      state.legFuel[idStr] != null ? state.legFuel[idStr] : (droneState.legBurnedDistance || 0)
    );
    const animDist = Number(droneState.distanceTraveled || 0);
    const plannedDist = Number(routeInfo.distance || 0);
    // Single-leg consumed prefers legBurnedDistance; falls back to planned distance
    // before any animation has run.
    const distanceForDisplay = legBurned > 0 ? legBurned : plannedDist;

    // Finalized past legs (frozen at each refuel). The ACTIVE leg = legBurned.
    const legHistory = Array.isArray(state.legFuelHistory[idStr])
      ? state.legFuelHistory[idStr]
      : [];
    // Whole-mission consumed = Σ finalized legs + active leg. This is what P/F and
    // the mission total charge against (a refuel drone burns one full mission of
    // fuel across all its legs, not just the current one).
    const historySum = legHistory.reduce((s, v) => s + (Number(v) || 0), 0);
    const routeLegDists = getLegDistances(routeInfo);

    // Has the drone actually FLOWN this mission yet? (currently animating, paused
    // mid-flight, or any fuel burned / leg finalized). Distinguishes the LIVE view
    // (count what was flown) from the pristine PLANNED-solve view (show the plan).
    const _hasFlown =
      state.animation?.active === true ||
      missionState.mode === MissionMode.PAUSED_MID_ANIMATION ||
      legHistory.length > 0 ||
      legBurned > 0.05;

    // Fuel CONSUMED for P/F + mission total:
    //  - flown  → Σ finalized legs + the active leg (what the drone really burned,
    //    accumulates across cuts, cut-neutral).
    //  - planned → the route's own total distance (the solve).
    const wholeConsumed = _hasFlown
      ? (historySum + legBurned)
      : (routeLegDists.length ? routeLegDists.reduce((s, v) => s + (Number(v) || 0), 0) : plannedDist);
    const fuelChargedForDisplay = isLost ? tank : wholeConsumed;
    const displayTargetCount = _hasFlown ? visitedCount : routeTargetLabels.length;
    const displayPoints = _hasFlown ? collectedPoints : plannedPoints;
    const pf = fuelChargedForDisplay > 0 ? displayPoints / fuelChargedForDisplay : 0;

    // Multi-leg (show the ⛽ split) if the plan refuels or a leg has been finalized.
    const routeIsMultiLeg =
      (routeInfo.refuel_airport && routeInfo.refuel_airport !== "-") ||
      (Array.isArray(routeLegDists) && routeLegDists.length > 1);
    const hasRefuel = routeIsMultiLeg || legHistory.length > 0;

    // Per-drone display
    setDroneTargetsPoints(`d${did}`, displayTargetCount, displayPoints);
    missionVisited += displayTargetCount;
    missionPoints += displayPoints;
    if (isLost) {
      setDroneFuel(did, [tank], tank, { lost: true, route });
    } else if (_hasFlown) {
      // LIVE view (animating / flown): fuel counts what the drone has ACTUALLY flown.
      // A leg accumulates across CUTS (a cut is fuel-neutral) and advances to a new
      // "+" leg ONLY when the drone crosses a REFUEL stop. So: finalized legs
      // (legFuelHistory, appended at each real refuel crossing) + the ACTIVE leg
      // (legBurned, resets to 0 at a refuel). At Animate-start this is just ⛽0 and
      // grows; visiting the tanker turns it into ⛽<leg1>+0 and a fresh count. The
      // PLANNED solver legs (routeLegDists) are NOT used here — they'd wrongly show
      // the full plan (⛽55+58+62+32) before the drone has flown it.
      const perLeg = [...legHistory, legBurned];  // + the active (in-progress) leg
      setDroneFuel(did, perLeg, tank, { route });
    } else if (Array.isArray(routeLegDists) && routeLegDists.length > 1) {
      // PLANNED view (solved, not yet animated): show the solver's per-leg plan.
      setDroneFuel(did, routeLegDists, tank, { route });
    } else {
      // Single leg, planned: whole distance / full tank.
      setDroneFuel(did, [distanceForDisplay], tank, { route });
    }
    setText(`stat-d${did}-pf`, pf.toFixed(2));

    // Mission totals: Σ consumed (all drones) / Σ budget (all drones). Use the SAME
    // whole-mission source as the per-drone box so the Mission row and D1 agree.
    if ((route && route.length > 0) || isLost) {
      // Consumed = the SAME wholeConsumed the per-drone box charges: live flown
      // (Σ finalized legs + active) while animating, else the planned route total.
      // So the Mission row always matches the D1 box.
      missionDistanceTraveled += isLost ? tank : wholeConsumed;
      // Budget = tank × legs-so-far. LIVE: legs flown so far = finalized + 1 active
      // (budget grows only as the drone refuels — 40, then 80, then 120…). PLANNED:
      // tank × the solve's planned leg count.
      if (isLost) {
        missionBudget += tank;
      } else if (_hasFlown) {
        missionBudget += tank * (legHistory.length + 1);
      } else if (Array.isArray(routeLegDists) && routeLegDists.length > 1) {
        missionBudget += tank * routeLegDists.length;
      } else {
        missionBudget += tank;
      }
    }
  }

  const missionPf = missionDistanceTraveled > 0 ? missionPoints / missionDistanceTraveled : 0;
  setDroneTargetsPoints("mission", missionVisited, missionPoints);
  setText("stat-mission-fuel", `${Math.round(missionDistanceTraveled)} / ${Math.round(missionBudget)}`);
  setText("stat-mission-pf", missionPf.toFixed(2));
  updateAllocationDisplay(null, state.allocationStrategy);
}

// Which strategy made the allocation on screen, so a changed dropdown can say
// the results are from another strategy.
let shownAllocation = null;  // { strategy, allocated, total }

function strategyLabel(value) {
  const opt = [...($("allocation-strategy")?.options || [])].find((o) => o.value === value);
  return opt ? opt.textContent.replace(/\s*\(.*\)$/, "") : value;
}

function updateAllocationBadge() {
  const badge = $("allocation-badge");
  if (!badge) return;
  if (!shownAllocation || !shownAllocation.total) {
    badge.hidden = true;
    return;
  }
  const { strategy, allocated, total } = shownAllocation;
  const selected = $("allocation-strategy")?.value;
  const stale = Boolean(strategy && selected && strategy !== selected);
  badge.hidden = false;
  badge.classList.toggle("stale", stale);
  const who = strategy ? strategyLabel(strategy) : "Agent";
  badge.textContent = stale
    ? `Results from ${who} · Solve again`
    : `Results: ${who} · ${allocated}/${total}`;
}

function updateAllocationDisplay(allocations, strategy) {
  // Handle null/undefined allocations
  const allocs = allocations || {};
  const totalTargets = (state.env && state.env.targets) ? state.env.targets.length : 0;

  // Build allocation from routes if allocations is empty but routes exist
  let effectiveAllocs = allocs;
  if (Object.keys(allocs).length === 0 && state.routes && Object.keys(state.routes).length > 0) {
    effectiveAllocs = {};
    for (const [did, routeData] of Object.entries(state.routes)) {
      effectiveAllocs[did] = getRouteTargetLabels(routeData);
    }
  }

  let totalAllocated = 0;
  for (let did = 1; did <= 5; did++) {
    const idStr = String(did);
    const cfg = state.droneConfigs[idStr] || {};
    // Cuts replace the remaining plan, but completed visits still belong to
    // their drone. Keep that history even if the drone was subsequently lost.
    const visited = getVisitedTargetsForDrone(did, getRouteTargetLabels(state.routes[idStr]));
    const planned = cfg.enabled === false ? [] : effectiveAllocs[idStr] || [];
    const targets = [...new Set([...visited, ...planned])].filter(tid => {
      const owner = state.visitedTargetByDrone?.[tid];
      return owner === undefined || String(owner) === idStr;
    });
    totalAllocated += targets.length;
    setText(`drone-targets-d${did}`, targets.join(", "));
    const row = $(`drone-row-d${did}`);
    if (row) {
      row.classList.toggle("idle", targets.length === 0);
      row.classList.toggle("drone-off", cfg.enabled === false);
    }
  }
  refreshDroneDetailRows();

  shownAllocation = { strategy: strategy || null, allocated: totalAllocated, total: totalTargets };
  updateAllocationBadge();
}

// ----------------------------------------------------
// Import / Export
// ----------------------------------------------------

/**
/**
 * Check if JSON data is a segmented mission file (vs plain environment)
 * Detection: data.segments is array and data.segments[0].env exists
 */
function isSegmentedMissionJson(data, filename = "") {
  // Check if data has valid segments array structure (mission export format)
  const hasSegmentsArray = data && Array.isArray(data.segments) && data.segments.length > 0 && data.segments[0].env;
  // Also check for version field which is present in mission exports
  const hasVersionField = data && data.version;
  const isSegmentedByFile = isSegmentedByFilename(filename);
  console.log(`[isSegmentedMissionJson] hasSegmentsArray=${hasSegmentsArray}, hasVersionField=${hasVersionField}, isSegmentedByFilename=${isSegmentedByFile}, filename=${filename}`);
  // Accept if data structure indicates mission format (segments array with version)
  // OR if filename indicates multiple segments
  return hasSegmentsArray && (hasVersionField || isSegmentedByFile);
}

/**
 * Extract segment count from filename pattern _Nx_ where x is the number
 * Returns the segment count (x) or 1 if not found (non-segmented)
 */
function getSegmentCountFromFilename(filename) {
  if (!filename) return 1;
  // Match pattern _Nx_ where x is one or more digits
  const match = filename.match(/_N(\d+)_/);
  if (match) {
    return parseInt(match[1], 10);
  }
  return 1; // Default to non-segmented
}

/**
 * Check if file should be treated as segmented based on filename pattern _Nx_ where x > 1
 */
function isSegmentedByFilename(filename) {
  return getSegmentCountFromFilename(filename) > 1;
}

/**
 * Check if JSON data has segmentInfo with cut points (replay format)
 * This format contains the base env + segmentInfo.segmentCuts for replay
 * Detects by: type="segmented" AND segmentCuts array with entries
 * OR filename has _Nx_ pattern with x > 1
 */
function isSegmentInfoJson(data, filename = "") {
  const hasSegmentInfoStructure = data && data.type === "segmented" && data.segmentInfo && Array.isArray(data.segmentInfo.segmentCuts);
  if (!hasSegmentInfoStructure) return false;

  // If segmentCuts has entries, it's definitely a segmented file
  const hasSegmentCuts = data.segmentInfo.segmentCuts.length > 0;
  const hasFilenamePattern = isSegmentedByFilename(filename);

  return hasSegmentCuts || hasFilenamePattern;
}

/**
 * Load a segmented mission from segmentInfo format (replay format)
 * Uses SegmentedImportManager as single source of truth
 */
function loadSegmentInfoFromJson(data, filename = "") {
  appendDebugLine(`📥 Loading segmentInfo format from ${filename}`);

  // 1. Load into the segmented import manager (single source of truth)
  segmentedImport.loadFromSegmentInfo(data);

  // 2. Set up global state from the manager
  const fullEnv = segmentedImport.getFullEnv();
  state.env = fullEnv;
  state.droneConfigs = JSON.parse(JSON.stringify(fullEnv.drone_configs || {}));
  state.env.drone_configs = state.droneConfigs;

  // Store initial snapshot (ALL targets - never overwrite this)
  state.initialEnvSnapshot = JSON.parse(JSON.stringify(fullEnv));

  // 3. Reset mission state
  missionReplay.clear();
    segmentStore.clear();    // Clear segmentStore for new import
  missionState.committedSegments = [];
  missionState.draftSolution = null;
  clearVisited();  // Segment 0 starts with no visited targets
  state.importedSegmentCuts = null;  // We use segmentedImport now, not this
  state.wrappedPolygons = [];  // Clear ghost SAM boundaries from previous imports

  // 4. Create MissionReplay segments (placeholder solutions to be filled on Solve)
  const totalSegments = segmentedImport.getTotalSegments();
  appendDebugLine(`📊 Creating ${totalSegments} segments`);

  // Check if the imported JSON has solutions (for replay) or not (requires re-solve)
  const hasSolutions = segmentedImport.hasSolutions();
  if (hasSolutions) {
    appendDebugLine(`📦 Imported mission has solutions - replay ready`);
  } else {
    appendDebugLine(`📦 Imported mission has no solutions - requires solving each segment`);
  }

  for (let i = 0; i < totalSegments; i++) {
    const cutDistance = segmentedImport.getCutDistanceForSegment(i);
    const cutPositions = segmentedImport.getCutPositionForSegment(i);
    const storedSolution = segmentedImport.getSolutionForSegment(i);

    // Log segment info with more detail
    const visited = segmentedImport.getVisitedTargetsForSegment(i);
    const hasRoutes = storedSolution && Object.keys(storedSolution.routes || {}).length > 0;
    const cutDistStr = cutDistance !== null ? cutDistance.toFixed(1) : 'null';
    const cutPosStr = cutPositions ? Object.entries(cutPositions).map(([d,p]) => `D${d}:[${Array.isArray(p) ? p.map(v=>v.toFixed(1)).join(',') : 'obj'}]`).join(' ') : 'null';
    appendDebugLine(`   Segment ${i}: cutDist=${cutDistStr}, cutPos=${cutPosStr}, visited=[${visited.join(",")}], hasRoutes=${hasRoutes}`);

    missionReplay.addSegment({
      solution: storedSolution || { routes: {}, sequences: {} },
      env: fullEnv,  // All segments see all targets; filtering happens in solver
      cutDistance: i === 0 ? null : cutDistance,
      cutPositions: hasSolutions ? cutPositions : null,  // Only use cutPositions if we have solutions
      isCheckpointReplan: i > 0,
    });
  }

  // 5. Reset to start
  missionReplay.resetToStart();
  missionState.currentBuildSegmentIndex = 0;

  // 6. NO cut marker on import - markers appear after you Accept solutions
  state.pendingCutPositions = null;
  state.pendingLostDrones = [];

  // 7. CRITICAL: If we have solutions, populate missionState.fullSolutionsPerSegment
  // This is needed for Reset and animation to work correctly with FULL trajectories
  if (hasSolutions) {
    missionState.fullSolutionsPerSegment = {};
    for (let i = 0; i < totalSegments; i++) {
      const storedSolution = segmentedImport.getSolutionForSegment(i);
      if (storedSolution && Object.keys(storedSolution.routes || {}).length > 0) {
        missionState.fullSolutionsPerSegment[i] = JSON.parse(JSON.stringify(storedSolution));
        if (i === 0) {
          missionState.seg0FullSolution = JSON.parse(JSON.stringify(storedSolution));
          appendDebugLine(`📦 Saved seg-0 FULL solution for Reset (${Object.keys(storedSolution.routes).length} drones)`);
        }
      }
    }
  }

  // Restore map view if saved in the import data
  if (data && data.map_view) {
    restoreMapView(data.map_view);
  }

  if (hasSolutions) {
    // Has solutions - ready to animate
    appendDebugLine(`✅ Loaded ${segmentedImport.getTotalSegments()} segments with solutions. Ready to Reset & Animate.`);
    setMissionMode(MissionMode.IDLE, "segmentInfo loaded with solutions - ready to animate");
  } else {
    // No solutions - needs solving
    appendDebugLine(`✅ Loaded ${segmentedImport.getTotalSegments()} segments. Solve segment 0 first.`);
    setMissionMode(MissionMode.IDLE, "segmentInfo loaded - solve segment 0");
  }
}

/**
 * Load a segmented mission from JSON - strips solutions, keeps only env + cut distances
 * User must solve each segment sequentially before animating
 * NOW USES SegmentedImportManager for clean state management
 */
function loadSegmentedMissionFromJson(data, filename = "") {
  appendDebugLine(`📥 Loading segmented mission from ${filename}`);
  console.log(`[loadSegmentedMissionFromJson] Starting load for ${filename}`);

  // 1. Load into the segmented import manager (single source of truth)
  const loadResult = segmentedImport.loadFromSegmentsArray(data);
  console.log(`[loadSegmentedMissionFromJson] loadFromSegmentsArray returned ${loadResult}`);
  console.log(`[loadSegmentedMissionFromJson] segmentedImport.isActive()=${segmentedImport.isActive()}`);
  console.log(`[loadSegmentedMissionFromJson] segmentedImport.getTotalSegments()=${segmentedImport.getTotalSegments()}`);

  // 2. Set up global state - show only segment 0's targets on import
  const displayEnv = segmentedImport.getEnvForDisplay();  // Only segment 0's targets
  const fullEnv = segmentedImport.getFullEnv();           // All targets for snapshot
  state.env = displayEnv;

  // Derive segment 0's droneConfigs from C1 cutPositions (which drones actually flew segment 0)
  // The JSON's per-segment drone_configs may be buggy (often shows wrong enabled drones)
  const baseConfigs = JSON.parse(JSON.stringify(displayEnv.drone_configs || {}));
  const c1Positions = segmentedImport.getCutPositionForSegment(1);  // C1 = end of segment 0

  if (c1Positions && Object.keys(c1Positions).length > 0) {
    // Disable all, then enable only drones that have C1 positions
    Object.keys(baseConfigs).forEach(did => {
      if (baseConfigs[did]) baseConfigs[did].enabled = false;
    });
    Object.keys(c1Positions).forEach(did => {
      if (baseConfigs[did]) baseConfigs[did].enabled = true;
    });
  } else {
  }
  state.droneConfigs = baseConfigs;
  state.env.drone_configs = state.droneConfigs;

  // Store initial snapshot (ALL targets - never overwrite this)
  state.initialEnvSnapshot = JSON.parse(JSON.stringify(fullEnv));
  // 3. Reset mission state
  missionReplay.clear();
    segmentStore.clear();    // Clear segmentStore for new import
  missionState.committedSegments = [];
  missionState.draftSolution = null;
  clearVisited();  // Segment 0 starts with no visited targets
  state.missionId = null;
  state.importedSegmentCuts = null;  // We use segmentedImport now, not this
  state.wrappedPolygons = [];  // Clear ghost SAM boundaries from previous imports
  missionState.segmentedMission = null;

  // 4. Create MissionReplay segments (placeholder solutions to be filled on Solve)
  const totalSegments = segmentedImport.getTotalSegments();
  appendDebugLine(`📊 Creating ${totalSegments} segments`);

  // First, collect all segment drone_configs so we can derive lostDrones
  const segmentDroneConfigs = [];
  for (let i = 0; i < totalSegments; i++) {
    const configs = segmentedImport.getDroneConfigsForSegment(i) || {};
    segmentDroneConfigs.push(configs);
  }

  // Check if imported JSON has pre-solved routes
  const hasPresolvedRoutes = segmentedImport.hasSolutions();

  for (let i = 0; i < totalSegments; i++) {
    const cutDistance = segmentedImport.getCutDistanceForSegment(i);
    const storedSolution = segmentedImport.getSolutionForSegment(i);

    // Derive lostDrones: drones enabled in segment i-1 but disabled in segment i
    let lostDrones = [];
    if (i > 0) {
      const prevConfigs = segmentDroneConfigs[i - 1];
      const currConfigs = segmentDroneConfigs[i];
      Object.entries(prevConfigs).forEach(([did, cfg]) => {
        if (cfg.enabled && !currConfigs[did]?.enabled) {
          lostDrones.push(did);
        }
      });
    }

    // Log segment info
    const visited = segmentedImport.getVisitedTargetsForSegment(i);
    const cutDistStr = cutDistance !== null ? cutDistance.toFixed(1) : 'null';
    const hasRoutes = storedSolution && Object.keys(storedSolution.routes || {}).length > 0;
    appendDebugLine(`   Segment ${i}: cutDist=${cutDistStr}, visited=[${visited.join(",")}], hasRoutes=${hasRoutes}`);

    // Use stored solution if available, otherwise empty solution
    const solution = hasRoutes ? storedSolution : { routes: {}, sequences: {} };

    // Build sequences from routes if not present
    if (hasRoutes && !solution.sequences) {
      solution.sequences = {};
      Object.entries(solution.routes).forEach(([did, routeData]) => {
        if (routeData.route && routeData.route.length > 0) {
          solution.sequences[did] = routeData.route;
        }
      });
    }

    // Get cut positions from segmented import for this segment
    const segmentCutPositions = segmentedImport.getCutPositionForSegment(i);

    missionReplay.addSegment({
      solution: solution,
      env: fullEnv,  // All segments see all targets; filtering happens in solver
      cutDistance: i === 0 ? null : cutDistance,
      cutPositions: segmentCutPositions || null,  // Store cutPositions for lost drone positioning
      isCheckpointReplan: i > 0,
      lostDrones: lostDrones,  // Drones that were lost at this checkpoint
    });

    // ALSO populate segmentStore so "Export → Mission" round-trips WITH the solved
    // routes. Export prefers segmentStore.toJSON() (version 2.0: segments[] with
    // `routes` + singular `cutPosition`), which is exactly the format this importer
    // reads — so importing a solved mission then exporting it yields an identical,
    // immediately-animatable file. Without this, an imported mission left segmentStore
    // empty and export fell back to the legacy strip-solutions skeleton (no routes,
    // null plural cutPositions) that could not re-animate.
    //
    // CRITICAL: each segment must carry its OWN targets (the ones revealed at that
    // cut), NOT the full target set — otherwise re-import shows every target at once
    // instead of progressively at each cut. The loaded segment's `activeTargets` is
    // that per-segment target list.
    // Best-effort: this only feeds Export → Mission; it must NEVER break import.
    try {
      // Each segment's OWN target list (the ones revealed at that cut) lives in the
      // manager's per-segment array _segmentTargets[i]. Using it (not fullEnv) keeps
      // the progressive reveal on re-import. Fall back to fullEnv only if absent.
      const segTargets =
        (Array.isArray(segmentedImport._segmentTargets) && segmentedImport._segmentTargets[i])
          ? segmentedImport._segmentTargets[i]
          : (fullEnv.targets || []);
      const perSegmentEnv = {
        ...fullEnv,
        targets: JSON.parse(JSON.stringify(segTargets)),
      };
      segmentStore.addSegment({
        index: i,
        env: perSegmentEnv,
        drone_configs: JSON.parse(JSON.stringify(segmentDroneConfigs[i] || state.droneConfigs || {})),
        routes: hasRoutes ? (solution.routes || {}) : {},
        frozenTargets: segmentedImport.getVisitedTargetsForSegment(i) || [],
        cutPosition: segmentCutPositions || null,   // SINGULAR — matches the importer
        lostDrones: lostDrones,
      });
    } catch (e) {
      console.warn(`[import] segmentStore population for export skipped (seg ${i}):`, e);
      appendDebugLine(`⚠️ export-store seg ${i} skipped: ${e.message}`);
    }
  }

  // 5. Reset to start
  missionReplay.resetToStart();
  missionState.currentBuildSegmentIndex = 0;

  // 6. Set up state based on whether we have pre-solved routes
  if (hasPresolvedRoutes) {
    // Pre-solved mission: show segment 0 routes, ready to animate
    const seg0Solution = segmentedImport.getSolutionForSegment(0);
    if (seg0Solution && seg0Solution.routes) {
      state.routes = {};
      state.sequences = {};
      Object.entries(seg0Solution.routes).forEach(([did, routeData]) => {
        state.routes[did] = routeData;
        if (Array.isArray(routeData.route)) {
          state.sequences[did] = routeData.route.join(",");
        }
        // Make trajectory visible
        state.trajectoryVisible[did] = true;
      });
    }
    state.pendingCutPositions = null;
    state.pendingLostDrones = [];
    appendDebugLine(`✅ Loaded ${segmentedImport.getTotalSegments()} segments with pre-solved routes. Click Animate to play.`);
  } else {
    // No pre-solved routes: user must solve each segment
    state.pendingCutPositions = null;
    state.pendingLostDrones = [];
    appendDebugLine(`✅ Loaded ${segmentedImport.getTotalSegments()} segments. Solve segment 0 first.`);
  }

  // Restore map view if saved in the import data
  if (data && data.map_view) {
    restoreMapView(data.map_view);
  }

  // Set mode based on whether we have pre-solved routes
  if (hasPresolvedRoutes) {
    setMissionMode(MissionMode.READY_TO_ANIMATE, `segmented mission loaded with routes - ready to animate`);
  } else {
    setMissionMode(MissionMode.IDLE, `segmented mission loaded - solve segment 0 first`);
  }

  // Refresh the sequence input box for the currently-selected drone.
  syncSequencesFromRoutes();
}

/**
 * Load environment or segmented mission from JSON data
 * Centralizes all import logic for both plain env and segmented mission files
 */
function loadFromJson(data, filename = "") {
  data = migrateLegacyMapCoordinates(data);
  invalidateMissionAsyncWork();
  // Full reset for any import
  state.envFilename = filename || "";

  // Older files carry ONE mission-wide cruise altitude + climb angle (top-level,
  // data.env, or data.segments[0].env). Drones now have their own; a drone
  // without one takes the file's mission value on import.
  state.importedMissionAltitude = {
    altKm: data?.cruise_altitude_km
      ?? data?.env?.cruise_altitude_km
      ?? data?.segments?.[0]?.env?.cruise_altitude_km
      ?? 0,
    climbDeg: data?.cesium_climb_angle_deg
      ?? data?.env?.cesium_climb_angle_deg
      ?? data?.segments?.[0]?.env?.cesium_climb_angle_deg
      ?? DEFAULT_CLIMB_ANGLE_DEG,
  };
  state.missionId = null;
  state.missionStatus = null;
  state.routes = {};
  state.sequences = {};
  state.trajectoryVisible = {};
  state.timeSync = null;
  updateTimeSyncUi();
  clearVisited();
  state.initialEnvSnapshot = null;
  state.missionHistory = [];
  state.targetSegmentMap = {};
  state.currentCutSegment = 1;
  state.previouslyAssignedTargets = [];
  state.importedSegmentCuts = [];  // Clear segmented import state
  state.wrappedPolygons = [];  // Clear any ghost SAM polygons from previous imports
  state.checkpoint = { active: false, pct: 0.5, segments: {} };
  state.pendingCutDistance = null;
  state.allocations = {};
  state.allocationStrategy = null;
  state.distanceMatrix = null;
  state.pendingCutPositions = null;  // Clear any ghost markers from previous imports
  state.pendingCheckpoints = null;  // Clear checkpoints from previous imports
  state.checkpointSegmentCounter = 0;  // Reset checkpoint counter on import
  state._cutCounterClaimed = false;    // next cut claims a fresh number
  state.pendingLostDrones = [];  // Clear lost drones from previous imports

  // Stop animation if running
  if (state.animation) {
    if (state.animation.animationId) {
      cancelAnimationFrame(state.animation.animationId);
      state.animation.animationId = null;
    }
    state.animation.active = false;
    state.animation.drones = {};
    state.animation.elapsedMissionSeconds = 0;
    state.animation.missionComplete = false;
    state.animation.continuousReplay = false;
    state.animation.frameClock = null;
    state.animation.missionDistance = 0;
    state.animation.segmentStartOffset = 0;
    state.animation.checkpointPause = null;
    state.legFuel = {};  // fresh env load: clear durable per-leg fuel store
    state.legFuelHistory = {};  // and the finalized per-leg history
    // §11.10: clear transient replan start_fuel on import (fresh leg-1 = full tank).
    Object.values(state.droneConfigs || {}).forEach((c) => { if (c) delete c.start_fuel; });
  }

  // Reset replay + draft + segmented mission state
  missionReplay.clear();
    segmentedImport.clear();  // CRITICAL: Clear segmented import manager to avoid ghost targets/SAMs
  segmentStore.clear();    // Clear segmentStore for new mission
  state.wrappedPolygons = [];  // Clear ghost SAM boundaries from previous imports
  missionState.draftSolution = null;
  missionState.draftEnv = null;
  missionState.acceptedEnv = null;
  missionState.committedSegments = [];
  missionState.seg0FullSolution = null;
  missionState.currentSegmentIndex = -1;
  missionState.pauseContext = null;
  missionState.droneLostAtSegment = {};
  missionState.wasAnimating = false;
  missionState.stagedEnv = null;
  missionState.stagedDroneConfigs = null;
  missionState.checkpointSource = null;
  missionState.segmentedMission = null;
  missionState.currentBuildSegmentIndex = 0;

  // --- Case 1: SegmentInfo format (type: "segmented" with segmentInfo.segmentCuts) ---
  // Check this FIRST because some files may have both segments array AND segmentInfo
  if (isSegmentInfoJson(data, filename)) {
    loadSegmentInfoFromJson(data, filename);
    initDroneConfigsFromEnv();
    restoreTimeSyncOptionsToUI(data.time_sync);
    fillMissingAltitudesEverywhere();
    updateSamWrappingClientSide();
    drawEnvironment();
    const envNameEl = $("env-filename");
    if (envNameEl) envNameEl.textContent = filename || "(imported)";
    return;
  }

  // --- Case 1b: Segmented mission JSON (segments array format) ---
  if (isSegmentedMissionJson(data, filename)) {
    loadSegmentedMissionFromJson(data, filename);
    initDroneConfigsFromEnv();
    restoreTimeSyncOptionsToUI(data.time_sync);
    fillMissingAltitudesEverywhere();
    updateSamWrappingClientSide();
    drawEnvironment();
    const envNameEl = $("env-filename");
    if (envNameEl) envNameEl.textContent = filename || "(imported)";
    return;
  }

  // --- Case 2: New non-segmented format (schema: isr_env_v1, is_segmented: false) ---
  if (data && data.schema === "isr_env_v1" && data.is_segmented === false && data.env) {
    state.env = JSON.parse(JSON.stringify(data.env));
    // Top-level drone_configs takes priority over env.drone_configs (latest export pattern)
    if (data.drone_configs) {
      state.env.drone_configs = JSON.parse(JSON.stringify(data.drone_configs));
    }
    setMissionMode(MissionMode.IDLE, "new environment loaded");
    appendDebugLine(`Imported environment from ${filename}`);

  } else if (data && data.environment) {
    // --- Case 3: Wrapped format (data.environment) ---
    state.env = data.environment;
    setMissionMode(MissionMode.IDLE, "new environment loaded");
    appendDebugLine(`Imported environment from ${filename}`);

  } else {
    // --- Case 4: Plain env JSON (legacy/backward compatible) ---
    state.env = data;
    setMissionMode(MissionMode.IDLE, "new environment loaded");
    appendDebugLine(`Imported environment from ${filename}`);
  }

  // Initialize drone configs (from env if present)
  initDroneConfigsFromEnv();
  restoreTimeSyncOptionsToUI(data && data.time_sync);

  // §11.10: STRIP the transient replan `start_fuel` from EVERY imported drone
  // config. start_fuel is runtime-only (the reduced leg-1 fuel after a cut) and
  // must NEVER persist in a saved env — an exported file baked it in, so on
  // import it would starve leg 1 (e.g. 15 instead of the full 50 tank) and make
  // the solver drop targets that actually fit. Cleared HERE (after
  // initDroneConfigsFromEnv rebuilds the configs) so it covers every import case;
  // an earlier clear was undone when the file's drone_configs were loaded.
  Object.values(state.droneConfigs || {}).forEach((c) => { if (c) delete c.start_fuel; });
  if (state.env && state.env.drone_configs) {
    Object.values(state.env.drone_configs).forEach((c) => { if (c) delete c.start_fuel; });
  }

  Object.entries(state.droneConfigs || {}).forEach(([did, cfg]) => {
    if (cfg.enabled) {
      appendDebugLine(`   D${did}: ${cfg.start_airport}→${cfg.end_airport}, fuel=${cfg.fuel_budget}`);
    }
  });

  // Restore map view if saved in the import data
  if (data && data.map_view) {
    restoreMapView(data.map_view);
  }

  // Recompute SAM wrapping and redraw
  fillMissingAltitudesEverywhere();
  updateSamWrappingClientSide();
  refreshCategoryDisplays();  // the file's own categories (or the catalog's)
  updateStatsFromRoutes();  // Clear stale stats from previous solve
  updateMissionControlState();  // status line: "Environment loaded…"

  // Update filename label
  const envNameEl = $("env-filename");
  if (envNameEl) envNameEl.textContent = filename || "(imported)";
}

function attachIOHandlers() {
  const fileInput = $("file-input");
  const btnImport = $("btn-import");
  const btnExport = $("btn-export");

  // If there's no file input, we can't import anything
  if (!fileInput) {
    appendDebugLine("attachIOHandlers: no file-input element found; import disabled.");
    return;
  }

  // Wire up the Import button if present
  if (btnImport) {
    btnImport.addEventListener("click", () => {
      fileInput.value = "";
      fileInput.click();
    });
  } else {
    appendDebugLine("attachIOHandlers: no btn-import element found; import button disabled.");
  }

  // File selection / JSON import
  fileInput.addEventListener("change", (evt) => {
    const file = evt.target.files && evt.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const text = e.target.result;
        const data = JSON.parse(text);
        loadFromJson(data, file.name);
      } catch (err) {
        appendDebugLine("Error parsing JSON: " + err);
        alert("Error parsing JSON: " + err);
      }
    };

    reader.readAsText(file);
  });

  // Wire up Export button if present
  if (btnExport) {
    btnExport.addEventListener("click", () => exportEnvironment("env"));
    $("btn-export-mission")?.addEventListener("click", () => exportEnvironment("mission"));
  } else {
    appendDebugLine("attachIOHandlers: no btn-export element found; export disabled.");
  }

  // Agent tab Import/Export buttons (duplicate functionality for convenience)
  const agentImportBtn = $("agent-import-btn");
  const agentExportBtn = $("agent-export-btn");

  if (agentImportBtn) {
    agentImportBtn.addEventListener("click", () => {
      fileInput.value = "";
      fileInput.click();
    });
  }

  if (agentExportBtn) {
    agentExportBtn.addEventListener("click", () => exportEnvironment("env"));
  }
}

// ----------------------------------------------------
// Optimizer helpers for frozen trajectory respect
// ----------------------------------------------------

/**
 * Get the frozen trajectory length for each drone.
 * Works for both segmented import and checkpoint workflow.
 * The frozen portion is everything up to the cut marker.
 * Returns: { droneId: frozenPointCount }
 */
function getFrozenTrajectoryCounts() {
  const counts = {};

  // Determine cut distance from either segmented import or checkpoint workflow
  let cutDistance = null;

  if (segmentedImport.isActive()) {
    const segIdx = segmentedImport.getCurrentSegmentIndex();
    if (segIdx === 0) {
      // First segment - nothing frozen yet
      return counts;
    }
    cutDistance = segmentedImport.getCutDistanceForSegment(segIdx);
  } else if (state.checkpoint?.active && state.checkpoint.segments) {
    // Checkpoint workflow - get cut distance from checkpoint data
    // Use the first drone's checkpoint distance as the cut distance
    const firstDroneData = Object.values(state.checkpoint.segments)[0];
    cutDistance = firstDroneData?.checkpointDist || state.pendingCutDistance;
  } else if (missionState.draftSolution?.isCheckpointReplan) {
    // Draft solution has checkpoint data (after solve but before accept)
    // Try direct cutDistance first, then fall back to checkpointSegments
    cutDistance = missionState.draftSolution.cutDistance;
    if (!cutDistance && missionState.draftSolution.checkpointSegments) {
      const firstDroneData = Object.values(missionState.draftSolution.checkpointSegments)[0];
      cutDistance = firstDroneData?.checkpointDist;
    }
  } else if (state.pendingCutDistance) {
    // Fallback to pending cut distance
    cutDistance = state.pendingCutDistance;
  }

  if (!cutDistance || cutDistance <= 0) {
    return counts;
  }

  // Calculate frozen point count for each drone based on cut distance
  Object.entries(state.routes || {}).forEach(([did, routeData]) => {
    const traj = routeData.trajectory || [];
    if (traj.length < 2) {
      counts[did] = 0;
      return;
    }

    // Find how many points are within the cut distance (trajectories are lat/lng, distances in km)
    let cumulativeDist = 0;
    let frozenCount = 1; // At least the first point
    for (let i = 1; i < traj.length; i++) {
      // Trajectory points are [lat, lng] - use haversine for km distance
      const segDist = haversineKm(traj[i-1][0], traj[i-1][1], traj[i][0], traj[i][1]);
      cumulativeDist += segDist;
      if (cumulativeDist <= cutDistance) {
        frozenCount = i + 1;
      } else {
        break;
      }
    }
    counts[did] = frozenCount;
  });

  return counts;
}

/**
 * Calculate which targets are visited within a given distance on a set of routes.
 * This uses the ACTUAL trajectory and cut distance, NOT the JSON's visitedTargets.
 * @param {Object} routes - { droneId: { trajectory: [...], route: [...] } }
 * @param {number} cutDistance - Distance threshold for considering targets visited
 * @returns {Array} Array of target IDs that are visited within the cut distance
 */
function calculateVisitedTargetsFromRoutes(routes, cutDistance) {
  console.log(`[calculateVisitedTargets] cutDistance=${cutDistance}, routes=${Object.keys(routes || {}).length}`);
  if (!cutDistance || cutDistance <= 0) {
    console.log(`[calculateVisitedTargets] Early return: cutDistance is ${cutDistance}`);
    return [];
  }

  const visitedTargets = new Set();

  // Get all target positions - try segmentedImport first, fall back to state.env
  let allTargets = segmentedImport.getAllTargets();
  if (!allTargets || allTargets.length === 0) {
    allTargets = state.env?.targets || [];
    console.log(`[calculateVisitedTargets] Using state.env.targets (${allTargets.length} targets)`);
  } else {
    console.log(`[calculateVisitedTargets] Using segmentedImport.getAllTargets() (${allTargets.length} targets)`);
  }
  const targetPositions = {};
  allTargets.forEach(t => {
    // Targets now use lat/lng
    targetPositions[t.id] = [t.lat, t.lng];
  });

  // For each drone, find which targets are visited within the cut distance
  // IMPORTANT: Check ALL targets against the trajectory, not just the sequence
  // This is because frozenRoutes may have cumulative trajectory but only last segment's sequence
  Object.entries(routes || {}).forEach(([droneId, routeData]) => {
    const traj = routeData.trajectory || [];

    if (traj.length < 2) return;

    // Calculate cumulative distance at each trajectory point (lat/lng, distances in km)
    const distances = [0];
    for (let i = 1; i < traj.length; i++) {
      // Trajectory points are [lat, lng] - use haversine for km distance
      const segDist = haversineKm(traj[i-1][0], traj[i-1][1], traj[i][0], traj[i][1]);
      distances.push(distances[i-1] + segDist);
    }

    // For each target, check if it's on the trajectory within the cut distance
    // We check ALL targets (not just sequence) because frozenRoutes may have
    // cumulative trajectory from multiple segments but only last segment's route
    allTargets.forEach(target => {
      const targetId = target.id;
      const targetPos = targetPositions[targetId];
      if (!targetPos) return;

      // Find if this target is on the trajectory within the cut distance
      for (let i = 0; i < traj.length; i++) {
        // Both trajectory points and targetPos are [lat, lng] - use haversine for km distance
        const dist = haversineKm(traj[i][0], traj[i][1], targetPos[0], targetPos[1]);

        // If trajectory point is close to target (within tolerance)
        if (dist < 0.1) {  // 0.1 km (100m) tolerance for lat/lng
          // Check if this trajectory point is within the cut distance
          if (distances[i] <= cutDistance) {
            visitedTargets.add(targetId);
            console.log(`[calculateVisitedTargets] D${droneId}: Found ${targetId} at traj[${i}], dist=${distances[i].toFixed(1)} <= cutDist=${cutDistance.toFixed(1)}`);
          } else {
            console.log(`[calculateVisitedTargets] D${droneId}: Found ${targetId} at traj[${i}], dist=${distances[i].toFixed(1)} > cutDist=${cutDistance.toFixed(1)} (NOT visited)`);
          }
          break;
        }
      }
    });
  });

  console.log(`[calculateVisitedTargets] Result: [${Array.from(visitedTargets).join(",")}]`);
  return Array.from(visitedTargets);
}

/**
 * Calculate which targets are in the frozen portion of the trajectory.
 * This is based on the actual trajectory and cut distance, NOT the JSON's visitedTargets.
 * Returns: array of target IDs that are within the frozen trajectory portion.
 */
function getActualFrozenTargets() {
  if (!segmentedImport.isActive()) {
    return [];
  }

  const segIdx = segmentedImport.getCurrentSegmentIndex();
  if (segIdx === 0) {
    return [];
  }

  const cutDistance = segmentedImport.getCutDistanceForSegment(segIdx);
  if (!cutDistance || cutDistance <= 0) return [];

  const frozenTargets = new Set();

  // Get all target positions - try segmentedImport first, fall back to state.env
  let allTargets = segmentedImport.getAllTargets();
  if (!allTargets || allTargets.length === 0) {
    allTargets = state.env?.targets || [];
  }
  const targetPositions = {};
  allTargets.forEach(t => {
    // Targets now use lat/lng
    targetPositions[t.id] = [t.lat, t.lng];
  });

  // For each drone, find which targets are visited within the frozen trajectory portion
  // IMPORTANT: Check ALL targets against the trajectory, not just the sequence
  // This is because state.routes may have cumulative trajectory but only last segment's sequence
  Object.entries(state.routes || {}).forEach(([, routeData]) => {
    const traj = routeData.trajectory || [];

    if (traj.length < 2) return;

    // Calculate cumulative distance at each trajectory point (lat/lng, distances in km)
    const distances = [0];
    for (let i = 1; i < traj.length; i++) {
      // Trajectory points are [lat, lng] - use haversine for km distance
      const segDist = haversineKm(traj[i-1][0], traj[i-1][1], traj[i][0], traj[i][1]);
      distances.push(distances[i-1] + segDist);
    }

    // For each target, check if it's on the trajectory within the frozen distance
    // We check ALL targets (not just sequence) because state.routes may have
    // cumulative trajectory from multiple segments but only last segment's route
    allTargets.forEach(target => {
      const targetId = target.id;
      const targetPos = targetPositions[targetId];
      if (!targetPos) return;

      // Find if this target is on the trajectory within the cut distance
      for (let i = 0; i < traj.length; i++) {
        // Both trajectory points and targetPos are [lat, lng] - use haversine for km distance
        const dist = haversineKm(traj[i][0], traj[i][1], targetPos[0], targetPos[1]);

        // If trajectory point is close to target (within tolerance)
        if (dist < 0.1) {  // 0.1 km (100m) tolerance for lat/lng
          // Check if this trajectory point is within the frozen distance
          if (distances[i] <= cutDistance) {
            frozenTargets.add(targetId);
          }
          break;
        }
      }
    });
  });

  return Array.from(frozenTargets);
}

/**
 * Get routes with only the unfrozen portion (for optimizers).
 * This extracts only the current segment's trajectory, excluding frozen prefix.
 */
function getUnfrozenRoutes() {
  const frozenCounts = getFrozenTrajectoryCounts();
  const unfrozenRoutes = {};

  Object.entries(state.routes || {}).forEach(([did, routeData]) => {
    const frozenCount = frozenCounts[did] || 0;
    const fullTraj = routeData.trajectory || [];
    const fullRoute = routeData.route || [];

    if (frozenCount === 0) {
      // No frozen portion - use full route
      unfrozenRoutes[did] = JSON.parse(JSON.stringify(routeData));
    } else {
      // Extract only the unfrozen portion
      // The trajectory after the frozen prefix
      const unfrozenTraj = fullTraj.slice(frozenCount - 1); // Include the cut point as new start

      // For route sequence, we need to find which targets are after the cut
      // This is complex - route contains node IDs, not trajectory points
      // For now, we'll use the full route and let the optimizer handle it
      // But mark the frozen targets so they're excluded
      unfrozenRoutes[did] = {
        ...routeData,
        trajectory: unfrozenTraj,
        route: fullRoute, // Keep full route for now, optimizer will filter by visitedTargets
        _frozenPointCount: frozenCount,
        _originalTrajectory: fullTraj,
      };
    }
  });

  return unfrozenRoutes;
}

/**
 * Get environment with only unfrozen targets (for optimizers).
 * Uses ACTUAL frozen targets based on cut distance, not JSON's visitedTargets.
 *
 * The key insight: state.visitedTargets comes from the JSON and may include targets
 * that are AFTER the cut marker in the current trajectory. We need to calculate
 * which targets are actually in the frozen portion based on the trajectory distance.
 */
function getUnfrozenEnv() {
  const env = JSON.parse(JSON.stringify(state.env || {}));

  // CRITICAL: Merge state.droneConfigs to preserve UI-configured values (end_airport, fuel_budget, enabled)
  // state.env.drone_configs comes from the imported JSON, but user may have changed these in the UI
  const droneConfigs = JSON.parse(JSON.stringify(env.drone_configs || {}));
  Object.entries(state.droneConfigs || {}).forEach(([did, cfg]) => {
    if (!droneConfigs[did]) {
      // Drone not in original config - add it
      droneConfigs[did] = JSON.parse(JSON.stringify(cfg));
    } else {
      // Merge UI config over imported config
      if (cfg.end_airport) droneConfigs[did].end_airport = cfg.end_airport;
      if (cfg.start_airport) droneConfigs[did].start_airport = cfg.start_airport;
      if (cfg.fuel_budget !== undefined) droneConfigs[did].fuel_budget = cfg.fuel_budget;
      if (cfg.enabled !== undefined) droneConfigs[did].enabled = cfg.enabled;
      if (cfg.target_access) droneConfigs[did].target_access = cfg.target_access;
      if (cfg.speed !== undefined) droneConfigs[did].speed = cfg.speed;
    }
  });
  env.drone_configs = droneConfigs;

  // Determine frozen targets:
  // - For segmented import: use actual trajectory-based calculation
  // - For checkpoint workflow: use state.visitedTargets directly
  let frozenTargets;
  if (segmentedImport.isActive() && segmentedImport.getCurrentSegmentIndex() > 0) {
    frozenTargets = getActualFrozenTargets();
  } else if (state.visitedTargets && state.visitedTargets.length > 0) {
    frozenTargets = state.visitedTargets;
  } else {
    frozenTargets = [];
  }
  const frozenSet = new Set(frozenTargets);

  // Filter out frozen targets
  env.targets = (env.targets || []).filter(t => !frozenSet.has(t.id));

  return env;
}

/**
 * Merge optimized routes back with frozen prefix.
 * Takes the optimizer output and prepends the frozen trajectory.
 * IMPORTANT: Also preserves any existing routes that the optimizer didn't process.
 */
function mergeWithFrozenRoutes(optimizedRoutes) {
  const frozenCounts = getFrozenTrajectoryCounts();
  const mergedRoutes = {};

  // First, preserve ALL existing routes from state.routes
  // This ensures we don't lose routes for drones that the optimizer didn't touch
  Object.entries(state.routes || {}).forEach(([did, routeData]) => {
    mergedRoutes[did] = JSON.parse(JSON.stringify(routeData));
  });

  // Then process routes from the optimizer, merging with frozen portions
  Object.entries(optimizedRoutes || {}).forEach(([did, optimizedData]) => {
    const frozenCount = frozenCounts[did] || 0;
    const originalRoute = state.routes[did];

    if (frozenCount === 0 || !originalRoute) {
      // No frozen portion - use optimized route directly
      mergedRoutes[did] = JSON.parse(JSON.stringify(optimizedData));
    } else {
      // Prepend frozen trajectory
      const frozenTraj = originalRoute.trajectory.slice(0, frozenCount);
      const optimizedTraj = optimizedData.trajectory || [];

      // Check for duplicate at junction
      const lastFrozen = frozenTraj[frozenTraj.length - 1];
      const firstOptimized = optimizedTraj[0];
      const isDuplicate = lastFrozen && firstOptimized &&
        Math.abs(lastFrozen[0] - firstOptimized[0]) < 0.001 &&
        Math.abs(lastFrozen[1] - firstOptimized[1]) < 0.001;

      const mergedTraj = isDuplicate
        ? frozenTraj.concat(optimizedTraj.slice(1))
        : frozenTraj.concat(optimizedTraj);

      mergedRoutes[did] = {
        ...optimizedData,
        trajectory: mergedTraj,
        distance: (originalRoute.distance || 0), // Keep original distance calc
      };

    }
  });

  return mergedRoutes;
}

// ----------------------------------------------------
// Optimization buttons
// ----------------------------------------------------
/**
 * Run Insert Missed optimization to add any unvisited targets to routes.
 * Called automatically after solve and manually via button.
 * @param {boolean} silent - If true, don't show "no insertions" message
 * @returns {Promise<number>} Number of targets inserted
 */
async function runInsertMissedOptimization(silent = false) {
  const generation = state.lifecycleGeneration || 0;
  if (!state.routes || Object.keys(state.routes).length === 0) {
    if (!silent) appendDebugLine("No routes to optimize. Run planner first.");
    return 0;
  }

  const droneConfigs = state.droneConfigs;
  // FROZEN TRAJECTORY RESPECT: Use only unfrozen routes and targets
  const unfrozenEnv = getUnfrozenEnv();
  // Check for frozen targets from EITHER segmented import OR checkpoint replan
  const segmentedFrozen = segmentedImport.isActive() && segmentedImport.getCurrentSegmentIndex() > 0;
  const checkpointFrozen = (state.visitedTargets && state.visitedTargets.length > 0);
  const hasFrozen = segmentedFrozen || checkpointFrozen;
  // For segmented import, use trajectory-based frozen targets; for checkpoint, use state.visitedTargets
  const actualFrozenTargets = segmentedFrozen ? getActualFrozenTargets() : (state.visitedTargets || []);

  try {
    const resp = await fetch("/api/insert_missed_optimize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        solution: {
          routes: state.routes,
          distance_matrix: state.distanceMatrix
        },
        env: unfrozenEnv, // Only unfrozen targets
        drone_configs: droneConfigs,
        visited_targets: actualFrozenTargets // Tell backend which targets are frozen
      })
    });
    const data = await resp.json();
    if ((state.lifecycleGeneration || 0) !== generation) return 0;

    if (data.success) {
      const insertions = data.insertions || [];
      if (insertions.length > 0) {
        // FROZEN TRAJECTORY RESPECT: Merge optimized routes with frozen prefix
        if (hasFrozen) {
          state.routes = mergeWithFrozenRoutes(data.routes);
        } else {
          // Server returns routes with correct refuel metadata (per-leg distances)
          state.routes = JSON.parse(JSON.stringify(data.routes));
        }
        // Deep clone sequences to avoid read-only property errors
        state.sequences = JSON.parse(JSON.stringify(data.sequences || {}));

        // Sync the on-screen sequence input to the currently-selected drone
        const curDrone = state.currentDroneForSeq || "1";
        const seqInput = $("sequence-input");
        if (seqInput && state.sequences[curDrone]) {
          seqInput.value = state.sequences[curDrone];
        }

        appendDebugLine(`Insert optimization: ${insertions.length} targets inserted.`);
        insertions.forEach(ins => {
          appendDebugLine(`  ${ins.target} -> D${ins.drone}`);
        });

        // Recalculate trajectories for modified routes
        await regenerateTrajectories();
        if ((state.lifecycleGeneration || 0) !== generation) return 0;
        updateStatsFromRoutes();
        drawEnvironment();

        // Update the committed segment so Reset preserves optimizer changes
        missionReplay.updateCurrentSegmentSolution(state.routes, state.sequences);

        return insertions.length;
      } else {
        if (!silent) appendDebugLine("Insert optimization: No insertions possible (all visited or fuel exhausted).");
        return 0;
      }
    } else {
      appendDebugLine("Insert optimization error: " + (data.error || "Unknown error"));
      return 0;
    }
  } catch (err) {
    appendDebugLine("Insert optimization error: " + err.message);
    return 0;
  }
}

function attachOptimizationHandlers() {
  const btnInsert = $("btn-optimize-insert");
  const btnCrossRemove = $("btn-optimize-cross");

  if (btnInsert) {
    btnInsert.addEventListener("click", async () => {
      saveOptimizerSnapshot();
      appendDebugLine("Running Insert Missed optimization...");
      btnInsert.disabled = true;
      btnInsert.textContent = "Working...";

      try {
        await runInsertMissedOptimization(false);
      } finally {
        btnInsert.disabled = false;
        btnInsert.textContent = "Insert";
      }
    });
  }

  // "Swap" button - runs cascade swap optimization until no more beneficial swaps
  const btnSwapUntil = $("btn-optimize-swap-until");
  if (btnSwapUntil) {
    btnSwapUntil.addEventListener("click", async () => {
      if (!state.routes || Object.keys(state.routes).length === 0) {
        appendDebugLine("No routes to optimize. Run planner first.");
        return;
      }

      btnSwapUntil.disabled = true;
      const originalText = btnSwapUntil.textContent;
      btnSwapUntil.textContent = "Running...";
      saveOptimizerSnapshot();

      try {
        const droneConfigs = state.droneConfigs;
        const unfrozenEnv = getUnfrozenEnv();
        const segmentedFrozen = segmentedImport.isActive() && segmentedImport.getCurrentSegmentIndex() > 0;
        const checkpointFrozen = (state.visitedTargets && state.visitedTargets.length > 0);
        const hasFrozen = segmentedFrozen || checkpointFrozen;
        const actualFrozenTargets = segmentedFrozen ? getActualFrozenTargets() : (state.visitedTargets || []);

        // Run client-side loop - one iteration at a time so user can see each swap
        let totalSwaps = 0;
          const maxLoops = 200; // safety cap to avoid accidental infinite loops
          let loop = 0;

          while (loop < maxLoops) {
            loop += 1;
            appendDebugLine(`Swap loop iteration ${loop}...`);

            const resp = await fetch("/api/trajectory_swap_optimize", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                solution: { routes: state.routes, distance_matrix: state.distanceMatrix },
                env: unfrozenEnv,
                drone_configs: droneConfigs,
                visited_targets: actualFrozenTargets
              })
            });
            const data = await resp.json();

            if (!data || !data.success) {
              appendDebugLine("Swap optimization error during loop: " + (data && data.error ? data.error : "Unknown error"));
              break;
            }

            const swaps = data.swaps_made || [];
            if (swaps.length === 0) {
              appendDebugLine(`No more swaps found after ${loop - 1} iterations. Stopping.`);
              break;
            }

            // Apply the returned routes and sequences (preserves frozen handling similar to single-click)
            const refuelMeta = snapshotRefuelMeta();
            let savedFrozenTrajectories = null;
            if (hasFrozen) {
              const frozenCounts = getFrozenTrajectoryCounts();
              savedFrozenTrajectories = {};
              Object.entries(state.routes || {}).forEach(([did, routeData]) => {
                const frozenCount = frozenCounts[did] || 0;
                if (frozenCount > 0 && routeData.trajectory && routeData.trajectory.length >= frozenCount) {
                  savedFrozenTrajectories[did] = routeData.trajectory.slice(0, frozenCount);
                }
              });
            }

            // Use returned routes (deep clone)
            state.routes = JSON.parse(JSON.stringify(data.routes));
            restoreRefuelMeta(state.routes, refuelMeta);
            if (savedFrozenTrajectories) {
              Object.entries(savedFrozenTrajectories).forEach(([did, frozenTraj]) => {
                if (state.routes[did]) state.routes[did].trajectory = frozenTraj;
              });
            }
            state.sequences = JSON.parse(JSON.stringify(data.sequences || {}));

            // Show the swaps from this iteration
            appendDebugLine(`  Iter ${loop}: ${swaps.length} swap(s)`);
            swaps.slice(0, 20).forEach(swap => {
              appendDebugLine(`    ${swap.target}: D${swap.from_drone} -> D${swap.to_drone} (savings=${(swap.savings||swap.savings||0).toFixed? (swap.savings||0).toFixed(1) : swap.savings})`);
            });
            if (swaps.length > 20) appendDebugLine(`    ... and ${swaps.length - 20} more`);

            totalSwaps += swaps.length;

            // The SWAP endpoint already returns SAM-aware trajectories (server-side
            // regenerate_solution_trajectories) that match the swapped route, so we
            // do NOT regenerate here — the old frontend regen FAILED on post-cut
            // checkpoint waypoints (C1-1) and corrupted the correct server trajectory.
            updateStatsFromRoutes();
            drawEnvironment();

            // Also update mission replay committed solution so Reset preserves progress
            missionReplay.updateCurrentSegmentSolution(state.routes, state.sequences);

            // small yield to keep UI responsive
            await new Promise(r => setTimeout(r, 50));
          }

          appendDebugLine(`Swap finished: total swaps applied = ${totalSwaps}, iterations = ${loop}`);
      } catch (err) {
        appendDebugLine("Swap error: " + err.message);
      } finally {
        btnSwapUntil.disabled = false;
        btnSwapUntil.textContent = originalText;
      }
    });
  }

  if (btnCrossRemove) {
    btnCrossRemove.addEventListener("click", async () => {
      if (!state.routes || Object.keys(state.routes).length === 0) {
        appendDebugLine("No routes to optimize. Run planner first.");
        return;
      }
      saveOptimizerSnapshot();
      appendDebugLine("Running Crossing Removal optimization...");
      btnCrossRemove.disabled = true;
      btnCrossRemove.textContent = "Working...";

      try {
        const droneConfigs = state.droneConfigs;
        // FROZEN TRAJECTORY RESPECT: Use only unfrozen targets
        const unfrozenEnv = getUnfrozenEnv();
        // Check for frozen targets from EITHER segmented import OR checkpoint replan
        const segmentedFrozen = segmentedImport.isActive() && segmentedImport.getCurrentSegmentIndex() > 0;
        const checkpointFrozen = (state.visitedTargets && state.visitedTargets.length > 0);
        const hasFrozen = segmentedFrozen || checkpointFrozen;
        // For segmented import, use trajectory-based frozen targets; for checkpoint, use state.visitedTargets
        const actualFrozenTargets = segmentedFrozen ? getActualFrozenTargets() : (state.visitedTargets || []);
        const resp = await fetch("/api/crossing_removal_optimize", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            solution: { routes: state.routes },
            env: unfrozenEnv, // Only unfrozen targets
            drone_configs: droneConfigs,
            visited_targets: actualFrozenTargets // Tell backend which targets are frozen
          })
        });
        const data = await resp.json();

        if (data.success) {
          const fixes = data.fixes_made || [];
          if (fixes.length > 0) {
            // Save original routes before applying changes
            const originalRoutes = JSON.parse(JSON.stringify(state.routes));
            const originalSequences = JSON.parse(JSON.stringify(state.sequences));

            // Apply optimized routes
            if (hasFrozen) {
              state.routes = mergeWithFrozenRoutes(data.routes);
            } else {
              state.routes = JSON.parse(JSON.stringify(data.routes));
            }
            state.sequences = JSON.parse(JSON.stringify(data.sequences || {}));

            // For refueling drones: recalculate leg distances and reject if over budget
            const waypoints = {};
            if (state.env) {
              for (const a of (state.env.airports || [])) waypoints[a.id || a.label] = [a.lat, a.lng];
              for (const t of (state.env.targets || [])) waypoints[t.id || t.label] = [t.lat, t.lng];
            }

            let anyRejected = false;
            for (const [did, cfg] of Object.entries(state.droneConfigs || {})) {
              const refuelAp = cfg.refuel_airport || "";
              if (!refuelAp || refuelAp === "" || !state.routes[did]) continue;

              const route = state.routes[did].route || [];
              const budget = Number(cfg.fuel_budget || 0);

              // Find refuel airport in the route (not first or last)
              let refuelIdx = -1;
              for (let i = 1; i < route.length - 1; i++) {
                if (route[i] === refuelAp || (refuelAp === "-" && (state.env.airports || []).some(a => a.id === route[i]))) {
                  // For "-" (any), find the airport in the middle of the route
                  if (refuelAp === "-") {
                    const isAirport = (state.env.airports || []).some(a => a.id === route[i]);
                    if (isAirport) { refuelIdx = i; break; }
                  } else {
                    refuelIdx = i; break;
                  }
                }
              }

              if (refuelIdx === -1) continue;

              // Calculate leg distances
              let leg1Dist = 0;
              for (let i = 0; i < refuelIdx; i++) {
                const from = waypoints[route[i]], to = waypoints[route[i + 1]];
                if (from && to) leg1Dist += Math.sqrt((to[0]-from[0])**2 + (to[1]-from[1])**2);
              }
              let leg2Dist = 0;
              for (let i = refuelIdx; i < route.length - 1; i++) {
                const from = waypoints[route[i]], to = waypoints[route[i + 1]];
                if (from && to) leg2Dist += Math.sqrt((to[0]-from[0])**2 + (to[1]-from[1])**2);
              }

              if (leg1Dist > budget || leg2Dist > budget) {
                // Reject: revert this drone to original
                appendDebugLine(`⚠️ D${did} crossing removal rejected: leg fuel exceeds budget (L1=${leg1Dist.toFixed(1)}, L2=${leg2Dist.toFixed(1)}, budget=${budget})`);
                state.routes[did] = originalRoutes[did];
                state.sequences[did] = originalSequences[did];
                anyRejected = true;
              } else {
                // Accept: set refuel metadata
                state.routes[did].refuel_airport = route[refuelIdx];
                state.routes[did].leg1_distance = leg1Dist;
                state.routes[did].leg2_distance = leg2Dist;
                appendDebugLine(`✅ D${did} crossing removal accepted: L1=${leg1Dist.toFixed(1)}, L2=${leg2Dist.toFixed(1)}, budget=${budget}`);
              }
            }

            // Update sequence display
            for (const [did, seq] of Object.entries(state.sequences)) {
              state.sequences[did] = seq;
            }

            appendDebugLine(`Crossing removal: ${fixes.length} crossings found, ${anyRejected ? "some rejected (fuel violation)" : "all accepted"}.`);
            fixes.forEach(fix => {
              appendDebugLine(`  Drone ${fix.drone}: reversed segment ${fix.segment_i}-${fix.segment_j}`);
            });

            // The CROSSING endpoint now returns SAM-aware trajectories server-side
            // (regenerate_solution_trajectories after the 2-opt reversal), so no
            // frontend regen — it failed on post-cut checkpoint waypoints (C1-1).
            updateStatsFromRoutes();
            drawEnvironment();

            // Update the committed segment so Reset preserves optimizer changes
            missionReplay.updateCurrentSegmentSolution(state.routes, state.sequences);
          } else {
            appendDebugLine("Crossing removal: No crossings found.");
          }
        } else {
          appendDebugLine("Crossing removal error: " + (data.error || "Unknown error"));
        }
      } catch (err) {
        appendDebugLine("Crossing removal error: " + err.message);
      } finally {
        btnCrossRemove.disabled = false;
        btnCrossRemove.textContent = "No Cross";
      }
    });
  }

  const btnOrOpt = $("btn-optimize-oropt");
  if (btnOrOpt) {
    btnOrOpt.addEventListener("click", async () => {
      if (!state.routes || Object.keys(state.routes).length === 0) {
        appendDebugLine("No routes to optimize. Run planner first.");
        return;
      }
      saveOptimizerSnapshot();
      appendDebugLine("Running Or-opt optimization (relocate contiguous runs)...");
      btnOrOpt.disabled = true;
      btnOrOpt.textContent = "Working...";

      try {
        const droneConfigs = state.droneConfigs;
        // FROZEN TRAJECTORY RESPECT: only optimize over the unfrozen part.
        const unfrozenEnv = getUnfrozenEnv();
        const segmentedFrozen = segmentedImport.isActive() && segmentedImport.getCurrentSegmentIndex() > 0;
        const checkpointFrozen = (state.visitedTargets && state.visitedTargets.length > 0);
        const hasFrozen = segmentedFrozen || checkpointFrozen;
        const actualFrozenTargets = segmentedFrozen ? getActualFrozenTargets() : (state.visitedTargets || []);

        const resp = await fetch("/api/or_opt_optimize", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            solution: { routes: state.routes, distance_matrix: state.distanceMatrix },
            env: unfrozenEnv,
            drone_configs: droneConfigs,
            visited_targets: actualFrozenTargets
          })
        });
        const data = await resp.json();

        if (data.success) {
          const moves = data.or_opt_moves || [];
          if (moves.length > 0) {
            const refuelMeta = snapshotRefuelMeta();
            if (hasFrozen) {
              state.routes = mergeWithFrozenRoutes(data.routes);
            } else {
              state.routes = JSON.parse(JSON.stringify(data.routes));
            }
            restoreRefuelMeta(state.routes, refuelMeta);
            state.sequences = JSON.parse(JSON.stringify(data.sequences || {}));

            appendDebugLine(`Or-opt: ${moves.length} run move(s) applied.`);
            moves.slice(0, 20).forEach(m => {
              appendDebugLine(`  ${Array.isArray(m.run) ? m.run.join(",") : m.run}: D${m.from_drone} -> D${m.to_drone} (rev=${m.reversed}, gain=${(m.gain || 0).toFixed ? m.gain.toFixed(1) : m.gain})`);
            });
            if (moves.length > 20) appendDebugLine(`  ... and ${moves.length - 20} more`);

            // Server already returns SAM-aware trajectories (regenerate_solution_trajectories);
            // do NOT regenerate on the frontend (it fails on post-cut checkpoint waypoints).
            updateStatsFromRoutes();
            drawEnvironment();
            missionReplay.updateCurrentSegmentSolution(state.routes, state.sequences);
          } else {
            appendDebugLine("Or-opt: no beneficial run moves found.");
          }
        } else {
          appendDebugLine("Or-opt error: " + (data.error || "Unknown error"));
        }
      } catch (err) {
        appendDebugLine("Or-opt error: " + err.message);
      } finally {
        btnOrOpt.disabled = false;
        btnOrOpt.textContent = "Or-Opt";
      }
    });
  }
}

async function regenerateTrajectories() {
  const generation = state.lifecycleGeneration || 0;
  // Regenerate SAM-avoiding trajectories for all routes after swap optimization
  // FROZEN TRAJECTORY RESPECT: Only regenerate unfrozen portions
  const droneConfigs = state.droneConfigs;
  const frozenCounts = getFrozenTrajectoryCounts();
  // Check for frozen from EITHER segmented import OR checkpoint workflow
  const segmentedFrozen = segmentedImport.isActive() && segmentedImport.getCurrentSegmentIndex() > 0;
  const checkpointFrozen = state.checkpoint?.active || (state.visitedTargets && state.visitedTargets.length > 0);
  const hasFrozen = segmentedFrozen || checkpointFrozen;

  // Build env with merged drone configs from UI
  const envWithConfigs = JSON.parse(JSON.stringify(state.env || {}));
  const mergedDroneConfigs = JSON.parse(JSON.stringify(envWithConfigs.drone_configs || {}));
  Object.entries(state.droneConfigs || {}).forEach(([did, cfg]) => {
    if (!mergedDroneConfigs[did]) {
      mergedDroneConfigs[did] = JSON.parse(JSON.stringify(cfg));
    } else {
      if (cfg.end_airport) mergedDroneConfigs[did].end_airport = cfg.end_airport;
      if (cfg.start_airport) mergedDroneConfigs[did].start_airport = cfg.start_airport;
      if (cfg.fuel_budget !== undefined) mergedDroneConfigs[did].fuel_budget = cfg.fuel_budget;
      if (cfg.enabled !== undefined) mergedDroneConfigs[did].enabled = cfg.enabled;
    }
  });
  envWithConfigs.drone_configs = mergedDroneConfigs;

  // For checkpoint workflow: add synthetic starts from frozen trajectory endpoints
  // or from draftSolution.checkpointSegments
  // IMPORTANT: Add to AIRPORTS array (not just synthetic_starts) so /api/apply_sequence can find them
  if (hasFrozen) {
    envWithConfigs.synthetic_starts = envWithConfigs.synthetic_starts || {};
    envWithConfigs.airports = envWithConfigs.airports || [];

    // Try to get synthetic starts from draftSolution first
    if (missionState.draftSolution?.checkpointSegments) {
      Object.entries(missionState.draftSolution.checkpointSegments).forEach(([did, seg]) => {
        if (seg.splitPoint) {
          const nodeId = `D${did}_START`;
          envWithConfigs.synthetic_starts[nodeId] = syntheticStartNode(nodeId, seg.splitPoint);
          // Also add as airport so /api/apply_sequence can find it
          // IMPORTANT: Mark as is_synthetic: true so backend filters it from valid_end_airports
          if (!envWithConfigs.airports.some(a => a.id === nodeId)) {
            envWithConfigs.airports.push({ ...syntheticStartNode(nodeId, seg.splitPoint), is_synthetic: true });
          }
        }
      });
    }

    // Fallback: derive from frozen trajectory endpoints
    Object.entries(state.routes || {}).forEach(([did, routeData]) => {
      const frozenCount = frozenCounts[did] || 0;
      const nodeId = `D${did}_START`;
      if (frozenCount > 0 && routeData.trajectory && routeData.trajectory.length >= frozenCount && !envWithConfigs.synthetic_starts[nodeId]) {
        const lastFrozenPt = routeData.trajectory[frozenCount - 1];
        envWithConfigs.synthetic_starts[nodeId] = syntheticStartNode(nodeId, lastFrozenPt);
        // Also add as airport so /api/apply_sequence can find it
        // IMPORTANT: Mark as is_synthetic: true so backend filters it from valid_end_airports
        if (!envWithConfigs.airports.some(a => a.id === nodeId)) {
          envWithConfigs.airports.push({ ...syntheticStartNode(nodeId, lastFrozenPt), is_synthetic: true });
        }
      }
    });
  }

  for (const [did, routeData] of Object.entries(state.routes)) {
    if (!routeData.route || routeData.route.length < 2) {
      continue;
    }

    try {
      // If we have frozen trajectory, we need to preserve it
      const frozenCount = frozenCounts[did] || 0;
      const frozenTraj = hasFrozen && frozenCount > 0
        ? routeData.trajectory?.slice(0, frozenCount)
        : null;

      const resp = await fetch("/api/apply_sequence", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          drone_id: did,
          sequence: routeData.route.join(","),
          env: envWithConfigs,
          fuel_budget: droneConfigs[did]?.fuel_budget || 300
        })
      });
      const data = await resp.json();
      if ((state.lifecycleGeneration || 0) !== generation) return;
      if (data.success) {
        // Ensure state.routes[did] is a fresh writable object to avoid read-only errors
        if (!state.routes[did] || Object.isFrozen(state.routes[did])) {
          state.routes[did] = JSON.parse(JSON.stringify(state.routes[did] || {}));
        }

        if (frozenTraj && frozenTraj.length > 0) {
          // FROZEN TRAJECTORY RESPECT: Merge frozen trajectory with regenerated
          // The new trajectory starts from the synthetic start (cut position),
          // so we simply append it to the frozen trajectory
          const newTraj = data.trajectory || [];

          // Check for duplicate at junction - new trajectory starts from synthetic start
          // which should be at or near the last frozen point
          const lastFrozen = frozenTraj[frozenTraj.length - 1];
          const firstNew = newTraj[0];
          const isDuplicate = lastFrozen && firstNew &&
            Math.abs(lastFrozen[0] - firstNew[0]) < 1.0 &&
            Math.abs(lastFrozen[1] - firstNew[1]) < 1.0;

          state.routes[did].trajectory = isDuplicate
            ? frozenTraj.concat(newTraj.slice(1))
            : frozenTraj.concat(newTraj);
          // Frozen-prefix concat shifts every index by the frozen length; the
          // server map describes only the regenerated suffix. Rather than offset
          // (rarely hit: only checkpoint/segment replays), drop the now-stale map
          // so the animation falls back to exact haversine over the actual points.
          state.routes[did].waypoint_indices = {};

        } else {
          // No frozen trajectory - use regenerated directly, AND its matching
          // waypoint_indices. A route edit/insert changes both the trajectory and
          // the map; carrying the OLD map forward desynced the animation distance
          // frame (drone raced through the SAM and stopped short of the end). The
          // map MUST travel with the trajectory it describes.
          state.routes[did].trajectory = data.trajectory;
          state.routes[did].waypoint_indices = data.waypoint_indices || {};
        }
        state.routes[did].distance = data.distance;
        state.routes[did].points = data.points;
      }
    } catch (err) {
    }
  }
}

// ----------------------------------------------------
// Planner integration
// ----------------------------------------------------
async function fetchWrappedPolygons(recalculate = false) {
  // Abort any pending wrapping request
  if (state.wrappingAbortController) {
    state.wrappingAbortController.abort();
  }

  // Create new abort controller for this request
  state.wrappingAbortController = new AbortController();
  const signal = state.wrappingAbortController.signal;

  try {
    let resp, data;

    if (recalculate && state.env && state.env.sams && state.env.sams.length > 0) {
      // Recalculate wrapping with current SAM positions
      resp = await fetch("/api/calculate_wrapping", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sams: state.env.sams }),
        signal: signal
      });
      data = await resp.json();
    } else {
      // Get cached wrapping from last solve
      resp = await fetch("/api/wrapped_polygons", { signal: signal });
      data = await resp.json();
    }

    if (data && data.success && data.polygons) {
      state.wrappedPolygons = data.polygons;
    } else {
      state.wrappedPolygons = [];
    }
  } catch (err) {
    if (err.name === 'AbortError') {
      return; // Don't clear polygons on abort
    }
    state.wrappedPolygons = [];
  }
}

// ----------------------------------------------------
// Mission History & Segmented Replanning Helpers
// ----------------------------------------------------

/**
 * Save the initial environment snapshot (called on first load or reset)
 */
function saveInitialEnvSnapshot() {
  if (state.env) {
    state.initialEnvSnapshot = JSON.parse(JSON.stringify(state.env));
    state.missionHistory = [];
    clearVisited();
    state.currentCutSegment = 1;  // Reset segment counter
    state.previouslyAssignedTargets = [];  // Clear previously assigned targets
    state.targetSegmentMap = {};  // Clear segment assignments
    appendDebugLine("Initial environment snapshot saved");
  }
}

/**
 * Push current segment to mission history
 */
function pushMissionSegment(note = "") {
  const segment = {
    envSnapshot: JSON.parse(JSON.stringify(state.env)),
    droneConfigsSnapshot: JSON.parse(JSON.stringify(state.droneConfigs)),
    startStates: {},  // { did: { x, y } } - where each drone started this segment
    trajectoriesByDrone: {},  // { did: [[x,y], ...] }
    routesByDrone: {},  // { did: ["A1", "T1", ...] }
    visitedTargetsThisSegment: [],
    meta: {
      createdAt: new Date().toISOString(),
      note: note,
      segmentIndex: state.missionHistory.length,
    }
  };

  // Capture start states and trajectories from current routes
  Object.entries(state.routes || {}).forEach(([did, routeData]) => {
    const traj = routeData.trajectory || [];
    const route = routeData.route || [];

    // Start state is first point of trajectory (or start airport position)
    if (traj.length > 0) {
      segment.startStates[did] = { x: traj[0][0], y: traj[0][1] };
      segment.trajectoriesByDrone[did] = JSON.parse(JSON.stringify(traj));
    }
    segment.routesByDrone[did] = [...route];

    // Extract visited targets (anything starting with T)
    route.forEach(wp => {
      if (String(wp).startsWith("T") && !state.visitedTargets.includes(wp)) {
        segment.visitedTargetsThisSegment.push(wp);
        markTargetVisitedByDrone(wp, did);
      }
    });
  });

  state.missionHistory.push(segment);
  appendDebugLine(`Segment ${segment.meta.segmentIndex} saved (${segment.visitedTargetsThisSegment.length} targets visited)`);
  return segment;
}

/**
 * Get drone configs from the last committed segment.
 * The segment is the source of truth for drone configuration.
 * Returns null only for the very first solve (segment 0) when no segments exist yet.
 *
 * @returns {object|null} The segment's drone_configs, or null for first solve
 */
function getPreviousSegmentDroneConfigs() {
  // Check missionReplay for previous segment drone configs
  const segIdx = missionReplay.getCurrentSegmentIndex();
  if (segIdx >= 0) {
    const seg = missionReplay.getSegment(segIdx);
    if (seg && seg.drone_configs) {
      appendDebugLine(`[SOLVER] Using drone_configs from legacy segment ${segIdx}`);
      return JSON.parse(JSON.stringify(seg.drone_configs));
    } else {
      appendDebugLine(`[SOLVER] Legacy segment ${segIdx} exists but no drone_configs (seg=${!!seg}, drone_configs=${!!seg?.drone_configs})`);
    }
  }

  // First solve - no segments yet
  appendDebugLine(`[SOLVER] No previous segment drone_configs found - using UI state`);
  return null;
}

/**
 * Point a drone's config at its resume node for a checkpoint replan, and
 * carry forward the fuel-neutral-cut fields (§11.10: start_fuel is the
 * TRANSIENT reduced tank for leg-1 only, never fuel_budget itself) and the
 * whole-mission refuel cap. Shared by every live-mission branch of
 * buildCheckpointEnv so they can't drift out of sync on which fields get
 * set — a prior drift here (LIVE CREATION path never set start_fuel) meant
 * a drone resuming from an already-committed segment silently got a full
 * tank instead of its real remaining fuel on that replan.
 */
function applyResumeNodeToDroneConfig(droneConfig, nodeId, did, { setHomeAirport = true } = {}) {
  if (!droneConfig) return;
  droneConfig.start_airport = nodeId;
  droneConfig.start_id = nodeId;
  // home_airport is deliberately NOT set for a checkpoint-id resume node
  // (C{n}-{did}) — it was found to be an unread field there and removed;
  // the synthetic_starts (D{did}_START) branches still rely on it being set.
  if (setHomeAirport) droneConfig.home_airport = nodeId;

  const fullTank = Number(droneConfig.fuel_budget) || 150;
  const burned = (state.animation.drones?.[did]?.legBurnedDistance) || 0;
  droneConfig.start_fuel = Math.max(0, fullTank - burned);

  droneConfig.refuels_used = Array.isArray(state.legFuelHistory?.[did])
    ? state.legFuelHistory[did].length : 0;
}

/**
 * Build env2 for replanning after checkpoint freeze
 * - Creates synthetic start nodes at frozen positions
 * - Marks visited targets
 * - Returns modified env and droneConfigs
 */
/**
 * A drone that LANDED (finished its route) before the cut. Landing ends its
 * mission, unless it may refuel at that airport (refuel_options / legacy
 * refuel_airport, with refuels left): then it takes off again from there with a
 * full tank. Otherwise it stays parked — start = end = where it landed, 0 fuel —
 * so the planner gives it no targets (its settings stay enabled).
 */
function resumeLandedDrone(cfg, did, seg, env2) {
  if (!cfg) return;
  const route = (state.routes?.[did]?.route || []).map(String);
  const last = route[route.length - 1];
  const isAirport = (env2.airports || []).some(a => String(a.id) === last);
  let node = last;
  if (!isAirport) {
    // Ended somewhere that isn't an airport: it stays where it stopped.
    node = `D${did}_START`;
    env2.synthetic_starts = env2.synthetic_starts || {};
    env2.synthetic_starts[node] = syntheticStartNode(node, seg.splitPoint);
  }
  const refuelPoints = new Set(
    [...(cfg.refuel_options || []), cfg.refuel_airport]
      .map(x => String(x || "")).filter(x => x && x !== "-")
  );
  const refuelsUsed = Array.isArray(state.legFuelHistory?.[did]) ? state.legFuelHistory[did].length : 0;
  const maxRefuels = cfg.max_refuels == null ? Infinity : Number(cfg.max_refuels);
  const canRefuel = isAirport && refuelPoints.has(node) && refuelsUsed < maxRefuels;

  cfg.start_airport = node;
  cfg.start_id = node;
  cfg.home_airport = node;
  if (canRefuel) {
    cfg.start_fuel = Number(cfg.fuel_budget) || 150;
    cfg.refuels_used = refuelsUsed + 1;
    appendDebugLine(`   Drone ${did}: landed at ${node} — refuels there and can fly again (full tank)`);
  } else {
    cfg.end_airport = node;
    cfg.start_fuel = 0;
    cfg.refuels_used = refuelsUsed;
    appendDebugLine(`   Drone ${did}: landed at ${node} — mission over for this drone (no refuel allowed there)`);
  }
}

/**
 * A D{did}_START resume node at a cut point. Points in the browser are [lat, lng];
 * the server projects a synthetic start to its local km frame ONLY when it has
 * lat/lng (server/solver/projection.py) — sending the lat/lng as x/y made the
 * server read them as km, placing the node tens of km off the map.
 */
function syntheticStartNode(nodeId, point) {
  return { id: nodeId, lat: point[0], lng: point[1] };
}

function buildCheckpointEnv() {
  appendDebugLine(`buildCheckpointEnv: checkpoint.active=${state.checkpoint?.active}, segments=${Object.keys(state.checkpoint?.segments || {}).join(",") || "NONE"}`);
  appendDebugLine(`buildCheckpointEnv: visitedTargets=[${state.visitedTargets.join(", ")}]`);

  // ========================================
  // NEW CLEAN PATH: Use SegmentedImportManager
  // ========================================
  if (segmentedImport.isActive()) {
    const segIdx = segmentedImport.getCurrentSegmentIndex();
    appendDebugLine(`buildCheckpointEnv: NEW MANAGER, segment ${segIdx}`);

    // Check if we have a checkpoint cut during animation (state.checkpoint.active)
    // In this case, we need to use the checkpoint split positions, not the original segment cut positions
    const hasCheckpointCut = state.checkpoint?.active && Object.keys(state.checkpoint.segments || {}).length > 0;
    appendDebugLine(`   hasCheckpointCut=${hasCheckpointCut}`);

    // Get env ready for solver (filtered targets, synthetic starts)
    const solverEnv = segmentedImport.getEnvForSolver();
    // Read drone configs directly from UI - what user sees is what solver uses
    const droneConfigs = readDroneConfigsFromUI();
    appendDebugLine(`   UI drone_configs: ${Object.entries(droneConfigs).map(([d,c]) => `D${d}:${c.enabled?'✓':'✗'}`).join(' ')}`);

    const unfrozen = segmentedImport.getUnfrozenTargets();
    const visited = segmentedImport.getVisitedTargetsForSegment(segIdx);

    // IMPORTANT: Also include any targets added during edit mode that aren't in the manager
    // The manager only knows about targets from the original import, not edits
    const managerTargetIds = new Set(unfrozen.map(t => t.id));
    const stateTargets = state.env?.targets || [];
    const newlyAddedTargets = stateTargets.filter(t =>
      !managerTargetIds.has(t.id) && !visited.includes(t.id)
    );

    if (newlyAddedTargets.length > 0) {
      appendDebugLine(`   Newly added targets from edits: ${newlyAddedTargets.map(t => t.id).join(",")}`);
      // Add newly added targets to solver env
      solverEnv.targets = [...solverEnv.targets, ...newlyAddedTargets];
    }

    // Also sync SAMs from state.env (user may have added/removed SAMs during editing)
    solverEnv.sams = JSON.parse(JSON.stringify(state.env?.sams || []));

    appendDebugLine(`   Targets for solver: ${solverEnv.targets.map(t => t.id).join(",")}`);
    appendDebugLine(`   Frozen (excluded): ${visited.join(",")}`);

    // If there's a checkpoint cut during animation, use those positions instead of original segment cuts
    if (hasCheckpointCut) {
      appendDebugLine(`   Using CHECKPOINT cut positions (mid-animation cut)`);
      solverEnv.synthetic_starts = solverEnv.synthetic_starts || {};

      Object.entries(state.checkpoint.segments).forEach(([did, seg]) => {
        if (!seg?.splitPoint) return;
        const nodeId = `D${did}_START`;
        solverEnv.synthetic_starts[nodeId] = syntheticStartNode(nodeId, seg.splitPoint);
        // Update ALL start fields to use synthetic start
        if (droneConfigs[did]) {
          droneConfigs[did].start_airport = nodeId;
          droneConfigs[did].start_id = nodeId;
          droneConfigs[did].home_airport = nodeId;
        }
        // Also update in solverEnv.drone_configs
        if (solverEnv.drone_configs && solverEnv.drone_configs[did]) {
          solverEnv.drone_configs[did].start_airport = nodeId;
          solverEnv.drone_configs[did].start_id = nodeId;
          solverEnv.drone_configs[did].home_airport = nodeId;
        }
        appendDebugLine(`   Drone ${did}: start from checkpoint [${seg.splitPoint[0].toFixed(2)}, ${seg.splitPoint[1].toFixed(2)}]`);
      });
    } else if (segIdx > 0) {
      // CRITICAL: For segment > 0, we must use synthetic starts from getEnvForSolver()
      // The solverEnv.drone_configs already has start_airport set to D{did}_START
      // We need to copy that to droneConfigs (which was based on state.droneConfigs)
      const cutPos = segmentedImport.getCutPositionForSegment(segIdx);
      if (cutPos) {
        Object.entries(cutPos).forEach(([did, pos]) => {
          const nodeId = `D${did}_START`;
          // Update ALL start fields to use synthetic start
          if (droneConfigs[did]) {
            droneConfigs[did].start_airport = nodeId;
            droneConfigs[did].start_id = nodeId;
            droneConfigs[did].home_airport = nodeId;
          }
          appendDebugLine(`   Drone ${did}: start from segment cut [${pos[0].toFixed(2)}, ${pos[1].toFixed(2)}] → start=${nodeId}`);
        });
      }
    }

    // Log end_airport for each drone to help debug
    Object.entries(droneConfigs).forEach(([did, cfg]) => {
      if (cfg.enabled) {
        appendDebugLine(`   Drone ${did}: start=${cfg.start_airport}, end=${cfg.end_airport}`);
      }
    });

    return {
      env: solverEnv,
      droneConfigs: droneConfigs,
      isCheckpointReplan: segIdx > 0 || hasCheckpointCut,
    };
  }

  // ========================================
  // LEGACY PATH: Old segmentInfo workflow
  // ========================================
  const isSegmentInfoWorkflow = state.importedSegmentCuts && state.importedSegmentCuts.length > 0;
  const currentSegIdx = missionReplay.getCurrentSegmentIndex();
  const currentSeg = missionReplay.getSegment(currentSegIdx);

  // Support both V2 export (cutPosition) and legacy (cutPositions)
  const currentSegCutPos = currentSeg?.cutPosition || currentSeg?.cutPositions;
  if (isSegmentInfoWorkflow && currentSegIdx > 0 && currentSegCutPos) {
    appendDebugLine(`buildCheckpointEnv: OLD segmentInfo workflow, segment ${currentSegIdx}, using cutPositions as start`);

    const baseEnv = state.env;
    const env2 = JSON.parse(JSON.stringify(baseEnv));
    env2.targets = (env2.targets || []).filter(t => !state.visitedTargets.includes(t.id));

    env2.synthetic_starts = env2.synthetic_starts || {};
    // Read drone configs directly from UI - what user sees is what solver uses
    const newDroneConfigs = readDroneConfigsFromUI();
    appendDebugLine(`   UI drone_configs: ${Object.entries(newDroneConfigs).map(([d,c]) => `D${d}:${c.enabled?'✓':'✗'}`).join(' ')}`);

    Object.entries(currentSegCutPos).forEach(([did, pos]) => {
      if (!pos || pos.length < 2) return;
      const nodeId = `D${did}_START`;
      env2.synthetic_starts[nodeId] = syntheticStartNode(nodeId, pos);
      if (newDroneConfigs[did]) {
        newDroneConfigs[did].start_airport = nodeId;
        newDroneConfigs[did].start_id = nodeId;
        newDroneConfigs[did].home_airport = nodeId;
      }
    });

    return { env: env2, droneConfigs: newDroneConfigs, isCheckpointReplan: true };
  }

  // ========================================
  // LIVE CREATION PATH: Check MissionReplay for committed segments with cut positions
  // This handles the case where we've done cut → accept → animate → cut again
  // ========================================
  const liveSegmentIdx = missionReplay.getCurrentSegmentIndex();
  const liveCurrentSeg = missionReplay.getSegment(liveSegmentIdx);
  // Support both V2 export (cutPosition) and legacy (cutPositions)
  const liveSegCutPos = liveCurrentSeg?.cutPosition || liveCurrentSeg?.cutPositions;
  const hasLiveCutPositions = liveSegCutPos && Object.keys(liveSegCutPos).length > 0;

  if (!state.checkpoint?.active && hasLiveCutPositions && liveSegmentIdx > 0) {
    appendDebugLine(`buildCheckpointEnv: LIVE CREATION path, segment ${liveSegmentIdx}, using saved cutPositions`);

    const baseEnv = state.env;
    const env2 = JSON.parse(JSON.stringify(baseEnv));

    // Filter out visited targets from the solver's target list. Same
    // env2.visited_targets metadata + filter as the CHECKPOINT path below —
    // both must stay consistent or the Insert-Missed optimizer can route a
    // drone back through a target it already visited (see the CHECKPOINT
    // path's comment on this exact class of bug).
    env2.visited_targets = [...state.visitedTargets];
    env2.targets = (env2.targets || []).filter(t => !state.visitedTargets.includes(t.id));
    appendDebugLine(`   Filtered targets: ${env2.targets.map(t => t.id).join(",")}`);
    appendDebugLine(`   Visited (excluded): ${state.visitedTargets.join(",")}`);

    // Create synthetic starts from the saved cut positions
    env2.synthetic_starts = env2.synthetic_starts || {};
    // Read drone configs directly from UI - what user sees is what solver uses
    const newDroneConfigs = readDroneConfigsFromUI();
    appendDebugLine(`   UI drone_configs: ${Object.entries(newDroneConfigs).map(([d,c]) => `D${d}:${c.enabled?'✓':'✗'}`).join(' ')}`);

    Object.entries(liveSegCutPos).forEach(([did, pos]) => {
      if (!pos || pos.length < 2) return;
      const nodeId = `D${did}_START`;
      env2.synthetic_starts[nodeId] = syntheticStartNode(nodeId, pos);
      applyResumeNodeToDroneConfig(newDroneConfigs[did], nodeId, did);
      if (newDroneConfigs[did]) {
        appendDebugLine(`   Drone ${did}: start from live cut [${pos[0].toFixed(2)}, ${pos[1].toFixed(2)}], start_fuel=${newDroneConfigs[did].start_fuel.toFixed(1)}, refuels_used=${newDroneConfigs[did].refuels_used}`);
      }
    });

    return { env: env2, droneConfigs: newDroneConfigs, isCheckpointReplan: true };
  }

  if (!state.checkpoint?.active || !state.checkpoint.segments) {
    appendDebugLine("buildCheckpointEnv: NOT a checkpoint replan (checkpoint.active is false or no segments)");
    // Read drone configs directly from UI - what user sees is what solver uses
    const uiConfigs = readDroneConfigsFromUI();
    appendDebugLine(`   UI drone_configs: ${Object.entries(uiConfigs).map(([d,c]) => `D${d}:${c.enabled?'✓':'✗'}`).join(' ')}`);
    return { env: state.env, droneConfigs: uiConfigs, isCheckpointReplan: false };
  }

  // Deep copy current env
  const env2 = JSON.parse(JSON.stringify(state.env));

  // Read drone configs directly from UI - what user sees is what solver uses
  const newDroneConfigs = readDroneConfigsFromUI();
  appendDebugLine(`buildCheckpointEnv: CHECKPOINT path - using UI drone_configs`);
  appendDebugLine(`   UI drone_configs: ${Object.entries(newDroneConfigs).map(([d,c]) => `D${d}:${c.enabled?'✓':'✗'}`).join(' ')}`);

  // NEW: Add per-drone checkpoints to environment (C1-1, C1-2, etc.)
  // Each drone gets its own checkpoint at its cut position
  env2.checkpoints = env2.checkpoints || [];
  if (state.pendingCheckpoints && Object.keys(state.pendingCheckpoints).length > 0) {
    // Add each drone's checkpoint to the environment (lat/lng)
    Object.entries(state.pendingCheckpoints).forEach(([did, cp]) => {
      // Only add if not already in the list
      if (!env2.checkpoints.some(c => c.id === cp.id)) {
        env2.checkpoints.push({ id: cp.id, lat: cp.lat, lng: cp.lng });
      }
      appendDebugLine(`   Added checkpoint ${cp.id} at [${cp.lat.toFixed(6)}, ${cp.lng.toFixed(6)}]`);

      // Update drone config to start from its checkpoint. start = the cut node;
      // end_airport is already sanitized to a REAL airport (or "-") upstream in
      // readDroneConfigsFromUI, so it is never the cut node here.
      applyResumeNodeToDroneConfig(newDroneConfigs[did], cp.id, did, { setHomeAirport: false });
      if (newDroneConfigs[did]) {
        appendDebugLine(`   Drone ${did}: start from checkpoint ${cp.id}, start_fuel=${newDroneConfigs[did].start_fuel.toFixed(1)}, refuels_used=${newDroneConfigs[did].refuels_used}`);
      }
    });
    // Drones that had already FINISHED their route when the cut was made get no
    // checkpoint; without this they kept their ORIGINAL start airport and would be
    // replanned from where the mission began. Resume them where they stopped, as
    // the all-finished branch below does.
    env2.synthetic_starts = env2.synthetic_starts || {};
    Object.entries(state.checkpoint.segments || {}).forEach(([did, seg]) => {
      if (!seg?.finishedAtCut || !seg.splitPoint || state.pendingCheckpoints[did]) return;
      resumeLandedDrone(newDroneConfigs[did], did, seg, env2);
    });
  } else {
    // Fallback to per-drone synthetic starts (legacy)
    env2.synthetic_starts = env2.synthetic_starts || {};
    Object.entries(state.checkpoint.segments).forEach(([did, seg]) => {
      if (!seg?.splitPoint) return;
      if (seg.finishedAtCut) {
        resumeLandedDrone(newDroneConfigs[did], did, seg, env2);
        return;
      }

      const nodeId = `D${did}_START`;
      env2.synthetic_starts[nodeId] = syntheticStartNode(nodeId, seg.splitPoint);
      applyResumeNodeToDroneConfig(newDroneConfigs[did], nodeId, did);
    });
    appendDebugLine(`   Using legacy synthetic_starts: ${Object.keys(env2.synthetic_starts).join(",")}`);
  }

  // Exclude already-visited targets from the solver's target list — SAME as the two
  // sibling checkpoint branches above (10101, 10139). This branch only recorded them as
  // env2.visited_targets metadata and left them IN env2.targets. The MANUAL solve survived
  // because those sibling branches (or the server) removed them, so the Insert-Missed
  // optimizer never saw a visited target as "missing". This branch (used by the agent
  // continue) skipped the removal, so Insert-Missed re-inserted the visited targets and
  // routed the drone BACK through the cut. Filtering here makes all three branches
  // consistent and feeds the optimizer a clean pending-target set.
  env2.visited_targets = [...state.visitedTargets];
  const _visitedSet = new Set(state.visitedTargets || []);
  if (_visitedSet.size) {
    env2.targets = (env2.targets || []).filter((t) => !_visitedSet.has(t.id));
  }

  appendDebugLine(`buildCheckpointEnv: IS checkpoint replan. checkpoints=${(env2.checkpoints || []).map(c => c.id).join(",")}, visited=${env2.visited_targets.length}, targets=[${(env2.targets||[]).map(t=>t.id).join(",")}]`);

  return {
    env: env2,
    droneConfigs: newDroneConfigs,
    isCheckpointReplan: true,
  };
}

/**
 * Get joined trajectories for full mission replay
 * Concatenates all segment trajectories per drone
 */
function getJoinedTrajectories() {
  const joined = {};

  state.missionHistory.forEach((segment) => {
    Object.entries(segment.trajectoriesByDrone || {}).forEach(([did, traj]) => {
      if (!joined[did]) {
        joined[did] = [];
      }

      if (joined[did].length > 0 && traj.length > 0) {
        // Check if we need to bridge (avoid duplicate points)
        const lastPoint = joined[did][joined[did].length - 1];
        const firstPoint = traj[0];
        if (Math.abs(lastPoint[0] - firstPoint[0]) < 0.001 &&
            Math.abs(lastPoint[1] - firstPoint[1]) < 0.001) {
          // Skip duplicate point
          joined[did] = joined[did].concat(traj.slice(1));
        } else {
          joined[did] = joined[did].concat(traj);
        }
      } else {
        joined[did] = joined[did].concat(traj);
      }
    });
  });

  return joined;
}

/**
 * Build combined routes from MissionReplay segments
 * For multi-segment missions, concatenates trajectories from frozen solutions
 * @param {number} [maxSegmentIndex] - If provided, only include segments 0 through maxSegmentIndex (inclusive)
 * Returns combined routes object ready for display/animation
 */
function buildChunkRefuelEvents(routeData, trajectory, env, distanceOffset = 0) {
  if (!Array.isArray(trajectory) || trajectory.length < 2) return [];
  const refuelIndices = Array.isArray(routeData?.refuel_indices)
    ? routeData.refuel_indices.map(Number).filter(Number.isInteger)
    : [];
  if (!refuelIndices.length) return [];

  const airportPositions = {};
  (env?.airports || []).forEach((airport) => {
    const id = airport?.id || airport?.label;
    if (id && Number.isFinite(airport.lat) && Number.isFinite(airport.lng)) {
      airportPositions[String(id)] = [airport.lat, airport.lng];
    }
  });
  const cumulative = [0];
  for (let i = 1; i < trajectory.length; i++) {
    cumulative.push(cumulative[i - 1] + haversineKm(
      trajectory[i - 1][0], trajectory[i - 1][1],
      trajectory[i][0], trajectory[i][1]
    ));
  }

  const events = [];
  let searchStart = 0;
  refuelIndices.forEach((routeIndex) => {
    const waypoint = routeData?.route?.[routeIndex];
    const position = airportPositions[String(waypoint)];
    if (!position) return;
    let closestIndex = -1;
    let closestDistance = Infinity;
    for (let i = searchStart; i < trajectory.length; i++) {
      const distance = haversineKm(
        trajectory[i][0], trajectory[i][1], position[0], position[1]
      );
      if (distance < closestDistance) {
        closestDistance = distance;
        closestIndex = i;
      }
    }
    // A planned refuel beyond this segment's cut is absent from the truncated
    // trajectory and must not become a replay event.
    if (closestIndex < 0 || closestDistance >= 0.5) return;
    events.push({
      wp: String(waypoint),
      dist: distanceOffset + cumulative[closestIndex],
      isRefuel: true,
    });
    searchStart = closestIndex + 1;
  });
  return events;
}

// A loiter is a stationary interval in the playback distance timeline. Keep
// this construction shared by initial playback and cut-time route rebuilding.
function routeLoiterSpecs(route, env, cfg) {
  if (!cfg?.loiter_enabled) return [];
  return (route || []).flatMap((wp) => {
    const target = (env?.targets || []).find(t => (t.id || t.label) === wp);
    if (!target) return [];
    const type = (target.type || "a").toLowerCase();
    const distance = Number(cfg.loiter_times?.[type] || 0) * Number(cfg.speed || 1);
    return distance > 0 ? [{ wp, position: [target.lat, target.lng], distance,
      radius: Math.max(0.5, Number(cfg.detection_radius?.[type] || 0) * 1.05) }] : [];
  });
}

function addLoiterIntervals(trajectory, cumulativeDistances, specs) {
  const holds = new Map();
  for (const spec of specs || []) {
    let best = -1, nearest = Infinity;
    trajectory.forEach((point, i) => {
      const distance = haversineKm(point[0], point[1], spec.position[0], spec.position[1]);
      if (distance < nearest) { nearest = distance; best = i; }
    });
    if (best >= 0 && nearest <= spec.radius) {
      holds.set(best, (holds.get(best) || 0) + spec.distance);
    }
  }
  const points = [], cumDist = [], events = [];
  let extra = 0;
  trajectory.forEach((point, i) => {
    points.push([...point]);
    cumDist.push(cumulativeDistances[i] + extra);
    const hold = holds.get(i) || 0;
    if (hold > 0) {
      events.push({ distance: cumulativeDistances[i], hold });
      extra += hold;
      points.push([...point]);
      cumDist.push(cumulativeDistances[i] + extra);
    }
  });
  return { trajectory: points, cumDist, total: cumDist.at(-1) || 0,
    shiftDistance: distance => distance + events.reduce(
      (sum, event) => sum + (event.distance <= distance + 1e-6 ? event.hold : 0), 0) };
}

// Locate a spatial checkpoint on the SAME timeline used to animate this drone.
// Its cumulative values include stationary loiter intervals; geometric length
// alone cannot be used as a playback threshold. A minimum distance disambiguates
// repeated passes through the same location during a multi-segment replay.
function locatePlaybackPosition(trajectory, cumulativeDistances, position, minimumDistance = 0) {
  if (!position || trajectory.length !== cumulativeDistances.length) return null;
  let nearest = Infinity, location = null;
  for (let i = 1; i < trajectory.length; i++) {
    const a = trajectory[i - 1], b = trajectory[i];
    const {fraction, distanceKm: separation} = closestFlightPosition(a, b, position);
    const candidate = cumulativeDistances[i - 1] + fraction *
      (cumulativeDistances[i] - cumulativeDistances[i - 1]);
    if (candidate < minimumDistance - 1e-6) continue;
    if (separation < nearest) {
      nearest = separation;
      location = { distance: candidate, index: i, fraction };
    }
  }
  return location;
}

function playbackDistanceAtPosition(trajectory, cumulativeDistances, position, minimumDistance = 0) {
  return locatePlaybackPosition(trajectory, cumulativeDistances, position, minimumDistance)?.distance ?? null;
}

// Saved cut coordinates are authoritative route junctions. Older trajectories
// can omit the junction vertex; include it before flying, preserving all existing
// timeline values and inserting the interpolated value at that junction.
function anchorPlaybackCheckpoint(routeInfo, drone, position) {
  const trajectory = routeInfo.trajectory || [];
  const cumulative = drone.cumulativeDistances || [];
  const location = locatePlaybackPosition(trajectory, cumulative, position, drone.distanceTraveled);
  if (!location) return;
  const { index, fraction, distance } = location;
  const samePosition = point => Math.abs(point[0] - position[0]) < 1e-10 &&
    Math.abs(point[1] - position[1]) < 1e-10;
  if (samePosition(trajectory[index - 1]) || samePosition(trajectory[index])) return;
  const points = trajectory.slice(0, index).concat([[...position]], trajectory.slice(index));
  const distances = cumulative.slice(0, index).concat([distance], cumulative.slice(index));
  routeInfo.trajectory = points;
  drone.cumulativeDistances = distances;
  if (routeInfo.anim_frame) {
    const frame = routeInfo.anim_frame;
    const indices = Object.fromEntries(Object.entries(frame.waypoint_indices || {})
      .map(([key, value]) => [key, value >= index ? value + 1 : value]));
    routeInfo.anim_frame = { ...frame, trajectory: points,
      cumulative_distances: distances, waypoint_indices: indices };
    routeInfo.waypoint_indices = indices;
    if (Array.isArray(frame.altitudes) && frame.altitudes.length === trajectory.length) {
      const altitude = frame.altitudes[index - 1] + fraction *
        (frame.altitudes[index] - frame.altitudes[index - 1]);
      routeInfo.anim_frame.altitudes = frame.altitudes.slice(0, index)
        .concat([altitude], frame.altitudes.slice(index));
    }
  }
}

function buildCombinedRoutesFromSegments(maxSegmentIndex) {
  const totalSegments = missionReplay.getSegmentCount();
  // If maxSegmentIndex provided, limit to that; otherwise use all segments
  const segmentCount = (maxSegmentIndex !== undefined && maxSegmentIndex >= 0)
    ? Math.min(maxSegmentIndex + 1, totalSegments)
    : totalSegments;

  if (segmentCount <= 1 && maxSegmentIndex === undefined) {
    // Single segment or no segments - return state.routes as-is
    return state.routes;
  }

  appendDebugLine(`Building combined routes from segments 0-${segmentCount - 1} (of ${totalSegments} total)...`);

  const combinedRoutes = {};

  // Build a map of which drones are lost at which segment
  // lostDrones in segment N = drones that died at the END of segment N-1 (at cut CN)
  const droneLostAtSegment = {};
  for (let i = 1; i < totalSegments; i++) {
    const seg = missionReplay.getSegment(i);
    if (seg?.lostDrones) {
      seg.lostDrones.forEach(did => {
        droneLostAtSegment[String(did)] = i;
      });
    }
  }
  if (Object.keys(droneLostAtSegment).length > 0) {
    appendDebugLine(`  Lost drones map: ${Object.entries(droneLostAtSegment).map(([d, s]) => `D${d}@seg${s}`).join(', ')}`);
  }

  for (let i = 0; i < segmentCount; i++) {
    const seg = missionReplay.getSegment(i);
    if (!seg) {
      appendDebugLine(`  Seg ${i}: No segment`);
      continue;
    }

    // Get the FULL trajectory for this segment from the ORIGINAL JSON import.
    // segmentedImport stores the unmodified trajectories from the imported JSON file.
    // These are the complete trajectories (start → airport) that need truncation.
    // DO NOT use missionState.fullSolutionsPerSegment - it may contain re-solved
    // trajectories that are different from the original import.
    let solutionToUse = null;
    let solutionSource = 'none';
    if (segmentedImport.isActive()) {
      solutionToUse = segmentedImport.getSolutionForSegment(i);
      if (solutionToUse?.routes && Object.keys(solutionToUse.routes).length > 0) {
        solutionSource = 'segmentedImport';
      }
    }
    if (!solutionToUse || !solutionToUse.routes || Object.keys(solutionToUse.routes).length === 0) {
      solutionToUse = seg.solution;
      solutionSource = 'missionReplay';
    }

    if (!solutionToUse || !solutionToUse.routes) {
      appendDebugLine(`  Seg ${i}: No solution/routes`);
      continue;
    }

    // Check if NEXT segment has a cutDistance - if so, truncate THIS segment's trajectory there
    // IMPORTANT: cutDistance is CUMULATIVE from mission start, but each segment's trajectory
    // starts at distance 0. So we need segment-relative truncation distance.
    const nextSeg = missionReplay.getSegment(i + 1);
    const nextSolution = (i + 1 < segmentCount)
      ? ((segmentedImport.isActive() && segmentedImport.getSolutionForSegment(i + 1)) || nextSeg?.solution || null)
      : null;
    const nextCutDistance = nextSeg?.cutDistance || null;
    // Get THIS segment's start distance (which is the cutDistance stored in THIS segment, or 0 for seg0)
    const thisCutDistance = seg.cutDistance || 0;
    // Segment-relative truncation = next cut - this segment's start
    const segmentRelativeTruncation = (nextCutDistance !== null && nextCutDistance > 0)
      ? nextCutDistance - thisCutDistance
      : null;

    // Debug: Log segment info with trajectory distances
    const routeCount = Object.keys(solutionToUse.routes).length;
    const firstDrone = Object.keys(solutionToUse.routes)[0];
    const firstTraj = solutionToUse.routes[firstDrone]?.trajectory || [];
    const firstTrajLen = firstTraj.length;
    let firstTrajDist = 0;
    for (let j = 1; j < firstTraj.length; j++) {
      const dx = firstTraj[j][0] - firstTraj[j-1][0];
      const dy = firstTraj[j][1] - firstTraj[j-1][1];
      firstTrajDist += Math.sqrt(dx*dx + dy*dy);
    }
    const usingFull = (i === 0 && solutionToUse === missionState.seg0FullSolution);
    appendDebugLine(`  Seg ${i} [${solutionSource}]: cutDist=${thisCutDistance?.toFixed(1)}, nextCut=${nextCutDistance?.toFixed(1) || 'null'}, truncAt=${segmentRelativeTruncation?.toFixed(1) || 'null'}, drones=${routeCount}, D${firstDrone}:${firstTrajLen}pts/${firstTrajDist.toFixed(1)}dist${usingFull ? ' (FULL)' : ''}`);

    Object.entries(solutionToUse.routes).forEach(([droneId, routeData]) => {
      // Check if this drone was lost at the START of the NEXT segment (i+1)
      // If so, this is the last segment for this drone - truncate at the kill position
      const lostAtSeg = droneLostAtSegment[droneId];
      if (lostAtSeg !== undefined && lostAtSeg <= i) {
        // Drone was already lost before or at this segment - skip adding more trajectory
        return;
      }

      let trajectory = routeData.trajectory || [];
      if (trajectory.length === 0) return;

      // Check if this drone is KILLED at the end of this segment (lost at segment i+1)
      // If so, truncate at the kill position (red diamond) - trajectory after that should vanish
      const droneKilledHere = (lostAtSeg === i + 1);
      const originalLen = trajectory.length;

      if (droneKilledHere) {
        // Drone is killed at the end of this segment - truncate at kill position
        // Try missionReplay segment first, then segmentedImport
        // Note: segment export uses 'cutPosition' (singular), not 'cutPositions'
        let killCutPositions = nextSeg?.cutPosition || nextSeg?.cutPositions;
        if ((!killCutPositions || !killCutPositions[droneId]) && segmentedImport.isActive()) {
          const importedCutPos = segmentedImport.getCutPositionForSegment(i + 1);
          if (importedCutPos && importedCutPos[droneId]) {
            killCutPositions = importedCutPos;
          }
        }

        if (killCutPositions && killCutPositions[droneId]) {
          const killPos = killCutPositions[droneId];
          // Truncate trajectory at the kill position
          const truncated = truncateTrajectoryAtPosition(trajectory, killPos);
          trajectory = truncated;
          appendDebugLine(`    D${droneId}: KILLED at seg ${i+1}, truncated at [${killPos[0].toFixed(1)},${killPos[1].toFixed(1)}]: ${originalLen}→${trajectory.length} pts`);
        } else if (segmentRelativeTruncation !== null && segmentRelativeTruncation > 0) {
          // Fallback: truncate at distance
          const splitResult = split_polyline_at_distance(trajectory, segmentRelativeTruncation);
          trajectory = splitResult?.prefixPoints || trajectory;
          appendDebugLine(`    D${droneId}: KILLED at seg ${i+1}, truncated at dist ${segmentRelativeTruncation.toFixed(1)}: ${originalLen}→${trajectory.length} pts`);
        }
      } else {
        // Truncate trajectory at the NEXT segment's cut position (where the drone was when cut happened)
        // CRITICAL: Only truncate segments BEFORE the last one being built.
        // The last segment (the one we're currently animating) should keep its FULL trajectory.
        const isLastSegmentBeingBuilt = (i === segmentCount - 1);

        // Get cut positions from missionReplay segment first, then fallback to segmentedImport
        // For imported JSON files, cutPositions may be in segmentedImport but not in missionReplay
        // Note: segment export uses 'cutPosition' (singular), not 'cutPositions'
        let nextSegCutPositions = nextSeg?.cutPosition || nextSeg?.cutPositions;
        if ((!nextSegCutPositions || !nextSegCutPositions[droneId]) && segmentedImport.isActive()) {
          // nextSeg is segment i+1, so its cut position is C{i+1}
          const importedCutPos = segmentedImport.getCutPositionForSegment(i + 1);
          if (importedCutPos && importedCutPos[droneId]) {
            nextSegCutPositions = importedCutPos;
          }
        }

        if (isLastSegmentBeingBuilt && i > 0) {
          // LAST segment (and not the only/first): each imported segment stores the
          // FULL solver route for that leg, from mission start. The pre-C{i} portion
          // is ALREADY contributed by the earlier segments' prefixes, so keep only the
          // SUFFIX after this segment's OWN start cut C{i}. (Mirror of the prefix
          // truncation the non-last segments do.) Without this the last segment's full
          // trajectory is appended whole and the drone re-flies its route twice.
          let ownCutPositions = seg?.cutPosition || seg?.cutPositions;
          if ((!ownCutPositions || !ownCutPositions[droneId]) && segmentedImport.isActive()) {
            const importedOwnCut = segmentedImport.getCutPositionForSegment(i);
            if (importedOwnCut && importedOwnCut[droneId]) {
              ownCutPositions = importedOwnCut;
            }
          }
          if (ownCutPositions && ownCutPositions[droneId]) {
            const ownCut = ownCutPositions[droneId];
            const suffix = truncateTrajectoryFromPosition(trajectory, ownCut);
            appendDebugLine(`    D${droneId}: LAST-seg suffix from [${ownCut[0].toFixed(1)},${ownCut[1].toFixed(1)}]: ${originalLen}→${suffix.length} pts`);
            trajectory = suffix;
          } else {
            // No own cut for this drone in the last segment → the drone did no new work
            // here (a stale duplicate of its previous segment). Contribute nothing, so
            // it doesn't re-fly its already-completed route.
            appendDebugLine(`    D${droneId}: LAST-seg has no own cut → skip (stale duplicate)`);
            return;
          }
        } else if (!isLastSegmentBeingBuilt && nextSegCutPositions && nextSegCutPositions[droneId]) {
          // USE POSITION-BASED TRUNCATION: truncate at the exact cut position
          // This is more accurate than distance-based truncation because each segment's
          // trajectory in the JSON goes from start to AIRPORT (full solver output),
          // not from start to next checkpoint.
          const cutPos = nextSegCutPositions[droneId];
          const truncated = truncateTrajectoryAtPosition(trajectory, cutPos);

          appendDebugLine(`    D${droneId}: Position-truncate at [${cutPos[0].toFixed(1)},${cutPos[1].toFixed(1)}]: ${originalLen}→${truncated.length} pts`);
          trajectory = truncated;
        } else if (!isLastSegmentBeingBuilt && segmentRelativeTruncation !== null && segmentRelativeTruncation > 0) {
          // Fallback: distance-based truncation if no cut positions available
          const splitResult = split_polyline_at_distance(trajectory, segmentRelativeTruncation);
          trajectory = splitResult?.prefixPoints || trajectory;

          if (droneId === Object.keys(solutionToUse.routes)[0]) {
            appendDebugLine(`    D${droneId}: Distance-truncate at ${segmentRelativeTruncation.toFixed(1)}: ${originalLen}→${trajectory.length} pts`);
          }
        }
      }

      // Determine the checkpoint ID for the END of this segment (if there's a next segment)
      // Format: C{nextSegIdx}-{droneId}
      const isLastSegmentBeingBuilt = (i === segmentCount - 1);
      const checkpointAtEnd = (!isLastSegmentBeingBuilt && nextSeg) ? `C${i + 1}-${droneId}` : null;

      // For killed drones, the checkpoint is their final waypoint
      const killCheckpoint = droneKilledHere ? `C${i + 1}-${droneId}` : null;

      // The waypoints this drone actually flew in this segment. Each segment
      // stores its whole plan (to the end airport), but a cut segment was only
      // flown up to the cut. The next segment re-plans every target not yet
      // visited, so the flown part ends at the last target that does NOT
      // reappear in the next plan; everything after it was never flown here.
      const flownRoute = (() => {
        const planned = routeData.route || [];
        const nextPlanned = nextSolution?.routes?.[droneId]?.route;
        if (!checkpointAtEnd || !Array.isArray(nextPlanned)) return planned;
        const replanned = new Set(nextPlanned.map(String));
        let last = -1;
        planned.forEach((wp, k) => {
          if (/^T/i.test(String(wp)) && !replanned.has(String(wp))) last = k;
        });
        // No target flown yet: keep only the start (synthetic start / airport).
        return last >= 0 ? planned.slice(0, last + 1) : planned.slice(0, 1);
      })();

      const loiterConfigs = segmentedImport.isActive()
        ? segmentedImport.getDroneConfigsForSegment(i)
        : (seg.drone_configs || seg.env?.drone_configs || state.droneConfigs);
      // Completed visits belong to the flown prefix, not the abandoned plan
      // after its cut. Saved visit history is authoritative for imported missions.
      const visitedAtCut = checkpointAtEnd && segmentedImport.isActive()
        ? new Set(segmentedImport.getVisitedTargetsForSegment(i + 1)) : null;
      const loiterRoute = visitedAtCut
        ? (routeData.route || []).filter(wp => visitedAtCut.has(wp)) : flownRoute;
      const loiterSpecs = routeLoiterSpecs(loiterRoute,
        segmentedImport.isActive()
          ? { targets: segmentedImport._segmentTargets[i] || [] } : seg.env,
        loiterConfigs?.[droneId] || {});

      // Get the route from solver - this has the targets in order for THIS drone
      const originalRoute = routeData.route || [];

      // Extract targets only (filter out synthetic starts and airports)
      const segTargetsInOrder = originalRoute.filter(wp => {
        const wpStr = String(wp);
        if (wpStr.match(/^D\d+_START$/)) return false;
        if (wpStr.match(/^A\d+$/)) return false;
        if (wpStr.match(/^C\d+-\d+$/)) return false; // Also filter existing checkpoints
        return true;
      });

      // Find start airport (first airport in route that's not a synthetic start)
      const startAirport = originalRoute.find(wp => {
        const wpStr = String(wp);
        return wpStr.match(/^A\d+$/) && originalRoute.indexOf(wp) === 0;
      });

      // Find end airport (last item if it's an airport)
      const lastItem = originalRoute[originalRoute.length - 1];
      const endAirport = (lastItem && String(lastItem).match(/^A\d+$/)) ? lastItem : null;

      if (!combinedRoutes[droneId]) {
        // First segment for this drone - copy route data
        // Build fullRoute: start airport + targets + checkpoint
        let fullRouteForSeg = [];

        // Add start airport (for first segment only)
        if (startAirport) {
          fullRouteForSeg.push(startAirport);
        }

        // Add targets in solver order
        fullRouteForSeg = fullRouteForSeg.concat(segTargetsInOrder);

        // Add checkpoint or kill checkpoint at end (NOT the airport yet)
        if (killCheckpoint) {
          fullRouteForSeg.push(killCheckpoint);
        } else if (checkpointAtEnd) {
          fullRouteForSeg.push(checkpointAtEnd);
        } else if (isLastSegmentBeingBuilt && endAirport) {
          // Only single segment mission - add end airport
          fullRouteForSeg.push(endAirport);
        }

        // A stitched route must NOT keep one segment's anim_frame (its playback
        // frame covers that segment only, and the player prefers it to the
        // trajectory): without it the player builds the frame from the stitched
        // trajectory, as intended for replays.
        const { anim_frame: _segmentFrame, ...segmentRoute } = routeData;
        combinedRoutes[droneId] = {
          ...segmentRoute,
          route: [...flownRoute],
          trajectory: [...trajectory],
          replay_loiter_specs: loiterSpecs,
          replay_refuel_events: buildChunkRefuelEvents(
            routeData, trajectory, seg.env || state.initialEnvSnapshot || state.env, 0
          ),
          distance: routeData.distance || 0,
          fullRoute: fullRouteForSeg,
        };
      } else {
        // Subsequent segment - concatenate trajectory AND route
        const existing = combinedRoutes[droneId].trajectory;
        let trajectoryOffset = 0;
        for (let j = 1; j < existing.length; j++) {
          trajectoryOffset += haversineKm(
            existing[j - 1][0], existing[j - 1][1],
            existing[j][0], existing[j][1]
          );
        }
        const newRefuelEvents = buildChunkRefuelEvents(
          routeData,
          trajectory,
          seg.env || state.initialEnvSnapshot || state.env,
          trajectoryOffset
        );
        if (existing.length > 0 && trajectory.length > 0) {
          const lastPoint = existing[existing.length - 1];
          const firstPoint = trajectory[0];
          // Check for duplicate point at junction
          const isDuplicate = Math.abs(lastPoint[0] - firstPoint[0]) < 0.001 &&
                             Math.abs(lastPoint[1] - firstPoint[1]) < 0.001;
          if (isDuplicate) {
            // Skip duplicate
            combinedRoutes[droneId].trajectory = existing.concat(trajectory.slice(1));
          } else {
            combinedRoutes[droneId].trajectory = existing.concat(trajectory);
          }
        } else {
          combinedRoutes[droneId].trajectory = existing.concat(trajectory);
        }
        combinedRoutes[droneId].replay_loiter_specs.push(...loiterSpecs);
        // Accumulate distance
        combinedRoutes[droneId].distance += (routeData.distance || 0);
        combinedRoutes[droneId].replay_refuel_events = (
          combinedRoutes[droneId].replay_refuel_events || []
        ).concat(newRefuelEvents);

        // CRITICAL: Also concatenate the route array so targets from later segments get checked
        // Filter out synthetic starts (D1_START, D2_START, etc.), checkpoints (C1-1, C1-2, etc.), and duplicates
        const existingRoute = combinedRoutes[droneId].route || [];
        const newRoute = flownRoute;
        const filteredNewRoute = newRoute.filter(wp => {
          const wpStr = String(wp);
          // Skip synthetic starts
          if (wpStr.match(/^D\d+_START$/)) return false;
          // Skip checkpoints (C{seg}-{drone} format) - they're segment boundaries, not mission waypoints
          if (wpStr.match(/^C\d+-\d+$/)) return false;
          // Skip a target already flown (airports may be visited again: refuels)
          if (/^T/i.test(wpStr) && existingRoute.includes(wp)) return false;
          return true;
        });
        combinedRoutes[droneId].route = existingRoute.concat(filteredNewRoute);

        // fullRoute: add this segment's NEW targets in order + checkpoint at end
        const existingFullRoute = combinedRoutes[droneId].fullRoute || [];

        // Add targets that were newly visited in this segment (in solver order)
        // Filter out any targets already in fullRoute (avoid duplicates)
        let filteredFullRoute = segTargetsInOrder.filter(t => !existingFullRoute.includes(t));

        // Add checkpoint at end if applicable
        if (killCheckpoint) {
          filteredFullRoute.push(killCheckpoint);
        } else if (checkpointAtEnd) {
          filteredFullRoute.push(checkpointAtEnd);
        } else if (isLastSegmentBeingBuilt && endAirport) {
          // Final segment - add the end airport
          filteredFullRoute.push(endAirport);
        }

        combinedRoutes[droneId].fullRoute = existingFullRoute.concat(filteredFullRoute);
      }
    });
  }

  const droneCount = Object.keys(combinedRoutes).length;
  // Log combined trajectory lengths AND distances
  const trajInfo = Object.entries(combinedRoutes).map(([did, rd]) => {
    const traj = rd.trajectory || [];
    let dist = 0;
    for (let j = 1; j < traj.length; j++) {
      const dx = traj[j][0] - traj[j-1][0];
      const dy = traj[j][1] - traj[j-1][1];
      dist += Math.sqrt(dx*dx + dy*dy);
    }
    return `D${did}:${traj.length}pts/${dist.toFixed(1)}dist`;
  }).join(', ');
  appendDebugLine(`Combined routes: ${droneCount} drones from ${segmentCount} segs → [${trajInfo}]`);
  // §9 (replay/reset aliasing fix): return a fully INDEPENDENT deep copy. The
  // builder spreads `{...routeData}` from each segment's stored solution (so
  // nested fields like `route` stay aliased to missionReplay._segments[i]).
  // If callers assign this straight into state.routes, the animation's in-place
  // trajectory mutations (densify ~10740, loiter splice ~10803, _fullTrajectory
  // re-densify) would write BACK into the stored segments, corrupting them so a
  // later Reset replays inflated/garbage data (breaks with ≥2 cuts). Deep-copying
  // here makes the stored segments immutable across replays, for ALL callers.
  return JSON.parse(JSON.stringify(combinedRoutes));
}

/**
 * Regenerate trajectories for combined routes using /api/apply_sequence.
 * This ensures we get fresh SAM-aware trajectories from the combined route sequences
 * instead of concatenating pre-existing (and potentially overlapping) trajectories.
 *
 * @param {Object} combinedRoutes - Routes object with route arrays for each drone
 * @param {Object} env - Environment with airports, targets, sams, checkpoints
 * @returns {Promise<Object>} - Updated routes with regenerated trajectories
 */
async function regenerateTrajectoriesFromSequences(combinedRoutes, env) {
  if (!combinedRoutes || Object.keys(combinedRoutes).length === 0) {
    appendDebugLine(`[REGEN] No combined routes to regenerate`);
    return combinedRoutes;
  }

  appendDebugLine(`[REGEN] Regenerating trajectories for ${Object.keys(combinedRoutes).length} drones...`);

  // Build a map of ALL waypoints (airports, targets, checkpoints) with their coordinates
  const waypointMap = {}; // id -> {lat, lng}

  // Add airports (lat/lng)
  (env.airports || []).forEach(a => {
    waypointMap[String(a.id)] = { lat: a.lat, lng: a.lng };
  });

  // Add targets (lat/lng)
  (env.targets || []).forEach(t => {
    waypointMap[String(t.id)] = { lat: t.lat, lng: t.lng };
  });

  // Add checkpoints from all segment cut positions (lat/lng)
  const totalSegs = missionReplay.getSegmentCount();
  for (let segIdx = 1; segIdx < totalSegs; segIdx++) {
    const seg = missionReplay.getSegment(segIdx);
    const segCutPos = seg?.cutPosition || seg?.cutPositions;
    if (segCutPos) {
      Object.entries(segCutPos).forEach(([droneId, pos]) => {
        if (pos && pos.length >= 2) {
          const cpId = `C${segIdx}-${droneId}`;
          waypointMap[cpId] = { lat: pos[0], lng: pos[1] };
        }
      });
    }
  }

  appendDebugLine(`[REGEN] Waypoint map has ${Object.keys(waypointMap).length} entries`);

  // Create env with checkpoints added for /api/apply_sequence (lat/lng)
  const checkpoints = [];
  for (let segIdx = 1; segIdx < totalSegs; segIdx++) {
    const seg = missionReplay.getSegment(segIdx);
    const segCutPos2 = seg?.cutPosition || seg?.cutPositions;
    if (segCutPos2) {
      Object.entries(segCutPos2).forEach(([droneId, pos]) => {
        if (pos && pos.length >= 2) {
          checkpoints.push({ id: `C${segIdx}-${droneId}`, lat: pos[0], lng: pos[1] });
        }
      });
    }
  }
  const envWithCheckpoints = { ...env, checkpoints };

  const updatedRoutes = JSON.parse(JSON.stringify(combinedRoutes));
  const promises = [];

  for (const [droneId, routeData] of Object.entries(combinedRoutes)) {
    const trajectory = routeData.trajectory || [];
    if (trajectory.length < 2) {
      appendDebugLine(`  D${droneId}: Skipping (trajectory too short: ${trajectory.length})`);
      continue;
    }

    // Build sequence by walking trajectory and detecting waypoints
    // A waypoint is "visited" when trajectory passes within a small threshold of its coordinates
    const sequence = [];
    const visited = new Set();
    const THRESHOLD = 0.5; // coordinate match threshold

    // Get checkpoints for THIS drone
    const droneCheckpoints = [];
    for (let segIdx = 1; segIdx < totalSegs; segIdx++) {
      const cpId = `C${segIdx}-${droneId}`;
      if (waypointMap[cpId]) {
        droneCheckpoints.push(cpId);
      }
    }

    // Walk through trajectory points
    for (const pt of trajectory) {
      const px = pt[0];
      const py = pt[1];

      // Check all waypoints
      for (const [wpId, wpPos] of Object.entries(waypointMap)) {
        if (visited.has(wpId)) continue;

        // Only check checkpoints that belong to THIS drone
        if (wpId.match(/^C\d+-\d+$/)) {
          const cpDroneId = wpId.split('-')[1];
          if (cpDroneId !== droneId) continue;
        }

        const dx = Math.abs(px - wpPos.x);
        const dy = Math.abs(py - wpPos.y);
        if (dx < THRESHOLD && dy < THRESHOLD) {
          sequence.push(wpId);
          visited.add(wpId);
        }
      }
    }

    if (sequence.length < 2) {
      appendDebugLine(`  D${droneId}: Skipping (detected sequence too short: ${sequence.length})`);
      continue;
    }

    const sequenceStr = sequence.join(', ');
    appendDebugLine(`  D${droneId}: Sequence = ${sequenceStr}`);

    const droneConfig = state.droneConfigs[droneId] || {};
    const fuelBudget = droneConfig.fuel_budget || 300;

    // Call /api/apply_sequence to regenerate trajectory
    const promise = fetch("/api/apply_sequence", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        drone_id: droneId,
        sequence: sequenceStr,
        env: envWithCheckpoints,
        fuel_budget: fuelBudget
      })
    })
      .then(res => res.json())
      .then(data => {
        if (data.success && data.trajectory && data.trajectory.length > 0) {
          updatedRoutes[droneId].trajectory = data.trajectory;
          updatedRoutes[droneId].distance = data.distance;
          updatedRoutes[droneId].points = data.points;
          appendDebugLine(`  D${droneId}: ✓ Regenerated ${data.trajectory.length} pts, dist=${data.distance?.toFixed(1)}`);
        } else {
          appendDebugLine(`  D${droneId}: ✗ Failed to regenerate: ${data.error || 'empty trajectory'}`);
        }
      })
      .catch(err => {
        appendDebugLine(`  D${droneId}: ✗ Error: ${err.message}`);
      });

    promises.push(promise);
  }

  // Wait for all trajectory regenerations to complete
  await Promise.all(promises);

  const trajInfo = Object.entries(updatedRoutes).map(([did, rd]) => `D${did}:${rd.trajectory?.length || 0}`).join(', ');
  appendDebugLine(`[REGEN] Complete: [${trajInfo}]`);

  return updatedRoutes;
}

// resetMission() is now defined in the Mission Mode Action Handlers section

function buildCheckpointRepairRoutes(configsToSolve) {
  if (!configsToSolve || !state.routes || Object.keys(state.routes).length === 0) {
    return null;
  }

  const targetIds = new Set((state.env?.targets || []).map(t => String(t.id)));
  const airportIds = new Set((state.env?.airports || []).map(a => String(a.id)));
  const visited = new Set((state.visitedTargets || []).map(String));
  const repairRoutes = {};

  for (const [did, cfg] of Object.entries(configsToSolve || {})) {
    if (!cfg || cfg.enabled === false) continue;
    const oldRouteData = state.routes?.[did];
    const oldRoute = Array.isArray(oldRouteData?.route) ? oldRouteData.route.map(String) : [];
    if (oldRoute.length < 2) continue;

    const start = String(cfg.start_airport || oldRoute[0]);
    const end = String(cfg.end_airport || oldRoute[oldRoute.length - 1]);
    const suffix = [];

    for (let i = 1; i < oldRoute.length; i++) {
      const wp = String(oldRoute[i]);
      if (targetIds.has(wp) && visited.has(wp)) continue;
      if (wp === start) continue;
      suffix.push(wp);
    }

    if (suffix.length === 0 || suffix[suffix.length - 1] !== end) {
      suffix.push(end);
    }

    const route = [start, ...suffix];
    const refuelIndices = [];
    for (let i = 1; i < route.length - 1; i++) {
      const wp = String(route[i]);
      if (airportIds.has(wp) && wp !== end) {
        refuelIndices.push(i);
      }
    }

    repairRoutes[did] = {
      route,
      sequence: route.join(","),
      fuel_budget: Number(cfg.fuel_budget || oldRouteData.fuel_budget || 0),
      refuel_indices: refuelIndices,
      refuel_stops: refuelIndices.map(i => route[i]),
    };
  }

  return Object.keys(repairRoutes).length > 0 ? repairRoutes : null;
}

/**
 * Put a checkpoint draft's cut back into live state (frozen segments, cut
 * positions, cut distance). A checkpoint draft switches the cut off and keeps
 * it itself; Discard and a re-solve both need it back.
 */
function restoreCheckpointFromDraft(draft) {
  // Restore checkpoint segments (frozen trajectory data)
  if (draft.checkpointSegments) {
    state.checkpoint = {
      active: true,
      pct: 0.5,
      segments: JSON.parse(JSON.stringify(draft.checkpointSegments)),
    };
    appendDebugLine(`   Restored ${Object.keys(draft.checkpointSegments).length} checkpoint segments`);
  }

  // Restore pending cut positions (cut marker locations)
  if (draft.pendingCutPositions) {
    state.pendingCutPositions = JSON.parse(JSON.stringify(draft.pendingCutPositions));
    appendDebugLine(`   Restored pendingCutPositions for drones: [${Object.keys(draft.pendingCutPositions).join(", ")}]`);
  }

  // Restore cut distance
  if (draft.cutDistance !== null && draft.cutDistance !== undefined) {
    state.pendingCutDistance = draft.cutDistance;
    appendDebugLine(`   Restored pendingCutDistance: ${draft.cutDistance.toFixed(1)}`);
  }
}

/**
 * Re-solving a checkpoint draft before Accept: restore the draft's cut so the
 * new solve starts from the same diamonds. Returns true if it restored.
 */
function restoreCutFromCheckpointDraft() {
  const draft = missionState.draftSolution;
  if (missionState.mode !== MissionMode.DRAFT_READY || !draft?.isCheckpointReplan) return false;
  if (state.checkpoint?.active) return false;
  // Only a draft solved from a fresh cut holds that cut. A draft solved from an
  // already-accepted cut has none; buildCheckpointEnv finds that cut in the
  // committed segment on its own, and switching on an empty cut would hide it.
  if (!draft.checkpointSegments || Object.keys(draft.checkpointSegments).length === 0) return false;
  appendDebugLine("🔄 Re-solving a checkpoint draft — restoring its cut...");
  restoreCheckpointFromDraft(draft);
  return true;
}

// Lost drones: drones that have a cut position but are now disabled — they were
// "lost" at this checkpoint (drawn in their red diamond).
function detectLostDrones(configsToSolve, isCheckpointReplan) {
  state.pendingLostDrones = [];
  if (isCheckpointReplan && state.pendingCutPositions) {
    Object.keys(state.pendingCutPositions).forEach(did => {
      const cfg = configsToSolve[did] || state.droneConfigs[did];
      if (cfg && !cfg.enabled) {
        state.pendingLostDrones.push(did);
        appendDebugLine(`🔴 Drone ${did} LOST at checkpoint (had cut position but now disabled)`);
      }
    });
  }
}

// Each enabled drone's route as the operator sees it now, from its current
// position: {did: [start, ..., end]}. Sent to the agent for its route edits.
function currentRoutesForAgent(req) {
  const out = {};
  for (const [did, cfg] of Object.entries(req.configsToSolve || {})) {
    if (!cfg || cfg.enabled === false) continue;
    const seq = buildMissionSequence(did);
    const remaining = seq.nodes.slice(seq.historyLength);
    if (seq.historyLength === 0) {
      if (remaining.length) out[did] = remaining;
      continue;
    }
    const start = String(cfg.start_airport || "");
    out[did] = [start, ...remaining.filter(wp => wp !== start)];
  }
  return out;
}

// Drone settings the agent changed (set_drone) become part of the request, as if
// the operator had changed them before solving.
function adoptAgentDroneConfigs(req, agentConfigs) {
  for (const [did, cfg] of Object.entries(agentConfigs || {})) {
    req.configsToSolve[did] = { ...(req.configsToSolve[did] || {}), ...cfg };
  }
  detectLostDrones(req.configsToSolve, req.isCheckpointReplan);
}

/**
 * Build the solve request for the CURRENT mission state — fresh solve, C-key cut,
 * live-edit cut, or a checkpoint draft being re-solved. Shared by every solver
 * (Run Planner and the reasoning agent) so they always solve the same problem:
 * same starts (cut nodes), carried fuel, visited targets, lost drones and repair
 * candidate. The solver is the only thing that differs between them.
 */
function buildSolveRequest() {
  // Get the selected allocation strategy
  const strategySelect = $("allocation-strategy");
  const strategy = strategySelect ? strategySelect.value : "geographic";

  // Check if this is a checkpoint replan (solve from frozen positions)
  const isCheckpointMode = missionState.mode === MissionMode.CHECKPOINT;
  // Re-solving a checkpoint draft before Accept: the draft switched the cut off
  // (state.checkpoint.active = false) and holds it itself. Put it back so
  // buildCheckpointEnv solves from the same cut instead of an older one.
  const restoredCutFromDraft = restoreCutFromCheckpointDraft();
  const { env: envToSolve, droneConfigs: configsToSolve, isCheckpointReplan } = buildCheckpointEnv();

  // Mission-wide cruise altitude (km): shrinks every SAM's 2D avoidance
  // radius to its horizontal slice at this height. Read straight from the
  // input each solve (not stored earlier in state.env) so it applies
  // uniformly regardless of which buildCheckpointEnv() branch ran above.
  // 0/absent = ground level, byte-identical to pre-altitude behavior.
  // Altitude is per drone (drone_configs[*].cruise_altitude_km). Drop any
  // mission-wide value an older imported env still carries.
  delete envToSolve.cruise_altitude_km;

  // Solve-boundary trace: exactly what goes to the solver, per drone. Kept as a
  // debug line (always in the Debug panel) — it pins start/end/fuel and the target
  // set for any future replan investigation.
  appendDebugLine(`🩺 SOLVE BOUNDARY: isCheckpointReplan=${isCheckpointReplan}, mode=${missionState.mode}`);
  Object.entries(configsToSolve || {}).forEach(([did, c]) => {
    if (!c || c.enabled === false) return;
    appendDebugLine(`🩺 D${did}: start=${c.start_airport} end=${c.end_airport} start_fuel=${c.start_fuel ?? '—'} fuel_budget=${c.fuel_budget}`);
  });
  appendDebugLine(`🩺 env checkpoints=[${(envToSolve.checkpoints||[]).map(c=>c.id).join(',')}] targets=[${(envToSolve.targets||[]).map(t=>t.id).join(',')}]`);

  detectLostDrones(configsToSolve, isCheckpointReplan);

  // Check if auto-optimize is enabled BEFORE building payload
  const autoOptimizeCheckbox = $("chk-auto-optimize");
  const shouldAutoOptimize = autoOptimizeCheckbox && autoOptimizeCheckbox.checked;
  const timeSyncOptions = readTimeSyncOptionsFromUI();
  const solverVisitedTargets = Array.isArray(state.visitedTargets) ? state.visitedTargets : [];
  const repairRoutes = isCheckpointReplan ? buildCheckpointRepairRoutes(configsToSolve) : null;

  const payload = {
    env: envToSolve,
    drone_configs: configsToSolve,
    allocation_strategy: strategy,
    is_checkpoint_replan: isCheckpointReplan,
    visited_targets: solverVisitedTargets,
    post_optimize: shouldAutoOptimize,  // Server-side iterative pipeline when checked
    repair_routes: repairRoutes,
    time_sync: timeSyncOptions,
    // §1a-0/§0.6: continue the active mission so it persists across edits/re-solves.
    mission_id: state.missionId,
    mission_name: state.missionName,
  };
  const modeLabel = isCheckpointReplan ? "CHECKPOINT REPLAN" : "FRESH SOLVE";
  appendDebugLine(`➡ [${modeLabel}] Sending /api/solve_with_allocation (strategy: ${strategy})...`);
  if (timeSyncOptions.enabled) {
    const syncStrategyLabel = timeSyncOptions.wait_strategy === "hover_wait" ? "hover wait" : "launch delay";
    const syncScope = timeSyncOptions.mode === "selected_targets"
      ? timeSyncOptions.members.map((member) => `D${member.drone_id}→${member.target_id}`).join(", ")
      : "first target arrival";
    appendDebugLine(`   Time sync: ${syncScope}, ${syncStrategyLabel}, tolerance=${timeSyncOptions.tolerance}`);
  }
  appendDebugLine(`   Payload targets: ${(envToSolve.targets||[]).map(t=>t.id).join(",")}`);
  appendDebugLine(`   Visited targets sent: ${solverVisitedTargets.join(",") || "none"}`);
  appendDebugLine(`   Auto optimize: ${shouldAutoOptimize ? "on" : "off"}`);
  if (repairRoutes) {
    appendDebugLine(`   Checkpoint repair candidate: ${Object.entries(repairRoutes).map(([did, rd]) => `D${did}:${rd.route.join("→")}`).join(" | ")}`);
  }
  appendDebugLine(`   state.env targets: ${(state.env.targets||[]).map(t=>t.id).join(",")}`);
  // Show fuel budgets for each drone
  const fuelInfo = Object.entries(configsToSolve).map(([did, cfg]) =>
    `D${did}:${cfg.fuel_budget?.toFixed(0) || '?'}`
  ).join(", ");
  appendDebugLine(`   Fuel budgets: ${fuelInfo}`);
  // Show which drones are enabled/disabled (critical for debugging lost drone persistence)
  const enabledInfo = Object.entries(configsToSolve).map(([did, cfg]) =>
    `D${did}:${cfg.enabled ? '✓' : '✗'}`
  ).join(", ");
  appendDebugLine(`   Enabled drones: ${enabledInfo}`);
  // Show start/end airports for each drone (for debugging end_airport issue)
  const airportInfo = Object.entries(configsToSolve).map(([did, cfg]) =>
    `D${did}:${cfg.start_airport || '?'}→${cfg.end_airport || '?'}`
  ).join(", ");
  appendDebugLine(`   Start→End airports: ${airportInfo}`);
  // Show refuel config for each drone
  const refuelInfo = Object.entries(configsToSolve).map(([did, cfg]) =>
    `D${did}:${cfg.refuel_airport || 'None'}`
  ).join(", ");
  appendDebugLine(`   Refuel airports: ${refuelInfo}`);

  return {
    payload,
    envToSolve,
    configsToSolve,
    isCheckpointReplan,
    isCheckpointMode,
    shouldAutoOptimize,
    restoredCutFromDraft,
  };
}

/**
 * Apply a solver result (from Run Planner's solver OR the reasoning agent) to the
 * mission: draft with frozen history + lost drones, render, trajectory repair,
 * optimize summary, cut cleared, DRAFT_READY. `req` is buildSolveRequest()'s
 * result for the request that produced `data`.
 *
 * opts.allowClientInsert (default true): when the server did not run the
 * optimizer pipeline, Run Planner silently inserts missed targets client-side.
 * The agent passes false — its plan is deliberate (e.g. "abort, no targets").
 */
async function applySolveResult(data, req, opts = {}) {
  const isCurrent = opts.isCurrent || (() => true);
  if (!isCurrent()) return;
  const { isCheckpointReplan, isCheckpointMode, configsToSolve } = req;
  missionState.revision += 1;  // a new draft replaces whatever was on screen
  const shouldAutoOptimize = req.shouldAutoOptimize || opts.allowClientInsert === false;

  // If this is the first solve (no history), save the initial snapshot
  if (missionState.committedSegments.length === 0 && !isCheckpointMode) {
    saveInitialEnvSnapshot();
    // Also set the accepted env if not already set
    if (!missionState.acceptedEnv) {
      missionState.acceptedEnv = JSON.parse(JSON.stringify(state.env));
    }
  }

  // §0.6: adopt the mission id from the solve so it persists across edits.
  if (data.mission_id) {
    if (data.mission_id !== state.missionId) {
      appendDebugLine("Mission: " + data.mission_id);
    }
    state.missionId = data.mission_id;
    state.missionStatus = "active";
    syncMissionUi();
  }

  // For checkpoint replans: DON'T splice frozen prefix into the new trajectory!
  // Each segment stores its own trajectory independently:
  // - Segment 0: A → ... → c1 (truncated at cut)
  // - Segment 1: c1 → targets → end (solver's result, starting from synthetic start)
  // The combination happens during animation via buildCombinedRoutesFromSegments
  if (isCheckpointReplan) {
    appendDebugLine(`📏 Cut distance for this segment: ${state.pendingCutDistance?.toFixed(1) || "null"}`);
    // Log the solver's trajectory (should start from synthetic start, NOT airport)
    Object.entries(data.routes || {}).forEach(([did, routeData]) => {
      const traj = routeData.trajectory || [];
      if (traj.length > 0) {
        const firstPt = `(${traj[0][0].toFixed(1)},${traj[0][1].toFixed(1)})`;
        const lastPt = `(${traj[traj.length-1][0].toFixed(1)},${traj[traj.length-1][1].toFixed(1)})`;
        appendDebugLine(`📎 D${did}: solver trajectory ${traj.length}pts [${firstPt}→${lastPt}]`);
      }
    });
  }

  // Store as draft solution (two-phase commit)
  // IMPORTANT: Deep copy routes to prevent modifications to state.routes from affecting the draft
  // Also save checkpoint state so it can be restored if the draft is discarded
  // CRITICAL: Save drone_configs at Solve time so Accept uses the actual config used for solving
  // DEBUG: Log cutDistance capture at solve time
  const solverCutDistance = isCheckpointReplan ? state.pendingCutDistance : null;
  appendDebugLine(`[SOLVE] Creating draftSolution: isCheckpointReplan=${isCheckpointReplan}, cutDistance=${solverCutDistance?.toFixed(1) || 'null'}, pendingCutDistance=${state.pendingCutDistance?.toFixed(1) || 'null'}`);

  missionState.draftSolution = {
    sequences: JSON.parse(JSON.stringify(data.sequences || {})),
    routes: JSON.parse(JSON.stringify(data.routes || {})),
    weatherSnapshot: req.payload?.env?.weather_snapshot ? JSON.parse(JSON.stringify(req.payload.env.weather_snapshot)) : null,
    wrappedPolygons: JSON.parse(JSON.stringify(data.wrapped_polygons || [])),
    allocations: JSON.parse(JSON.stringify(data.allocations || {})),
    // The solver doesn't echo the strategy; Run Planner's is the one it sent.
    // (The agent passes allowClientInsert=false and picks its own.)
    allocationStrategy: data.allocation_strategy ||
      (opts.allowClientInsert === false ? null : req.payload?.allocation_strategy) || null,
    distanceMatrix: data.distance_matrix ? JSON.parse(JSON.stringify(data.distance_matrix)) : null,
    timeSync: data.time_sync ? JSON.parse(JSON.stringify(data.time_sync)) : null,
    isCheckpointReplan: isCheckpointReplan,
    checkpointSegments: isCheckpointReplan ? JSON.parse(JSON.stringify(state.checkpoint.segments)) : null,
    cutDistance: solverCutDistance,
    // Save cut positions so they can be restored on discard
    pendingCutPositions: state.pendingCutPositions ? JSON.parse(JSON.stringify(state.pendingCutPositions)) : null,
    // Save drone_configs from solver (includes previous segment's disabled drones)
    // Use configsToSolve (from buildCheckpointEnv) which carries forward disabled drones from previous segments
    droneConfigs: JSON.parse(JSON.stringify(configsToSolve || {})),
    // Save lost drones so applyDraftSolutionToUI can purge them
    lostDrones: state.pendingLostDrones?.length > 0 ? [...state.pendingLostDrones] : [],
  };

  // Apply the draft solution visually (but not committed yet)
  applyDraftSolutionToUI(missionState.draftSolution);
  (data.warnings || []).forEach((warning) => appendDebugLine(`⚠️ ${warning}`));
  if (data.time_sync?.enabled) {
    const syncStatus = data.time_sync.status || "unknown";
    const syncTime = data.time_sync.sync_time;
    const syncStrategy = data.time_sync.wait_strategy === "hover_wait" ? "hover wait" : "launch delay";
    appendDebugLine(`⏱ Time sync ${syncStatus}${syncTime != null ? ` at t=${syncTime}` : ""} (${syncStrategy})`);
    (data.time_sync.warnings || []).forEach((warning) => appendDebugLine(`  ⚠️ ${warning}`));
  }

  // For checkpoint replans: check if any routes are missing proper trajectories
  // and regenerate them if needed
  if (isCheckpointReplan) {
    const routesNeedingRegeneration = [];
    Object.entries(state.routes || {}).forEach(([did, routeData]) => {
      // A disabled/lost drone's "incomplete" trajectory is CORRECT (it has
      // nowhere left to fly), not something to regenerate. Without this
      // guard, routeData.route/.trajectory here can still be the drone's
      // STALE pre-disable route (state.routes hasn't necessarily been
      // overwritten with this solve's empty response yet), so this block
      // called /api/apply_sequence with that old sequence and silently
      // resurrected a full trajectory for a drone that was just killed.
      if (configsToSolve?.[did]?.enabled === false) return;
      if (routeData.route && routeData.route.length > 2) {
        // Check if trajectory seems incomplete (only has frozen portion)
        const frozenCount = missionState.draftSolution.checkpointSegments?.[did]?.prefix?.length || 0;
        const trajLen = routeData.trajectory?.length || 0;
        if (trajLen <= frozenCount + 1) {
          routesNeedingRegeneration.push(did);
        }
      }
    });

    if (routesNeedingRegeneration.length > 0) {
      // Regenerate trajectories for incomplete routes
      await regenerateTrajectories();
      if (!isCurrent()) return;
    }
  }

  // AUTO-OPTIMIZE: Server-side pipeline already ran if shouldAutoOptimize was true
  // (we sent post_optimize: true in the payload)
  if (shouldAutoOptimize) {
    // Server already ran the iterative pipeline (INSERT → SWAP → NO-CROSS until convergence)
    // Just show the summary from the response
    const summary = data.pipeline_summary;
    if (summary) {
      appendDebugLine(`🔧 Server pipeline: ${summary.pipeline_passes} pass(es), ` +
        `${summary.targets_before}→${summary.targets_after} targets, ` +
        `${summary.fuel_before}→${summary.fuel_after} fuel`);
      if (summary.insertions > 0) {
        appendDebugLine(`  ✅ Inserted ${summary.insertions} target(s)`);
      }
      if (summary.swaps > 0) {
        appendDebugLine(`  ✅ Swapped ${summary.swaps} target(s)`);
      }
      if (summary.crossings_fixed > 0) {
        appendDebugLine(`  ✅ Removed ${summary.crossings_fixed} crossing(s)`);
      }
    } else {
      appendDebugLine("🔧 Auto-optimize complete (no summary)");
    }
    // Update draft with optimized routes (already in state.routes from server response)
    missionState.draftSolution.routes = JSON.parse(JSON.stringify(state.routes));
    missionState.draftSolution.sequences = JSON.parse(JSON.stringify(state.sequences));
  } else {
    // Just run INSERT silently (original behavior when auto-optimize is unchecked)
    const insertedCount = await runInsertMissedOptimization(true);
    if (!isCurrent()) return;
    if (insertedCount > 0) {
      appendDebugLine(`✅ Auto-inserted ${insertedCount} missed target(s) into routes.`);
      missionState.draftSolution.routes = JSON.parse(JSON.stringify(state.routes));
      missionState.draftSolution.sequences = JSON.parse(JSON.stringify(state.sequences));
    }
  }

  // Clear checkpoint state after successful replan (now in draft)
  state.checkpoint = {
    active: false,
    pct: 0.5,
    segments: {},
  };

  // Transition to DRAFT_READY
  setMissionMode(MissionMode.DRAFT_READY, isCheckpointReplan ? "checkpoint replan ready" : "solution ready");

  const resultLabel = isCheckpointReplan ? "✅ Checkpoint replan ready for review." : "✅ Solution ready for review.";
  appendDebugLine(resultLabel);
}

async function runPlanner() {
  if (!state.env) {
    alert("No environment loaded yet.");
    return;
  }
  state.timeSync = null;
  updateTimeSyncUi();

  // Check if we can solve in current state
  const perms = getUiPermissions();
  if (!perms.canSolve) {
    appendDebugLine("Cannot run planner in current state");
    return;
  }

  // After a finished mission every target is marked visited, and the solver
  // skips visited targets — Run Planner would return an empty plan. A finished
  // mission means "plan it again": clear the marks and plan every target.
  if (state.animation.missionComplete && (state.visitedTargets || []).length) {
    clearVisited();
    appendDebugLine("🆕 Mission finished — planning every target again (visited marks cleared)");
  }

  // Cancel any existing solve request
  if (state.solveAbortController) {
    state.solveAbortController.abort();
  }

  // Create new abort controller
  const solveController = new AbortController();
  state.solveAbortController = solveController;
  const solveGeneration = state.lifecycleGeneration || 0;
  const solveIsCurrent = () => state.solveAbortController === solveController &&
    !solveController.signal.aborted && (state.lifecycleGeneration || 0) === solveGeneration;

  // Grey out the Run Planner button while solving
  const btnRun = $("btn-run-planner");
  if (btnRun) {
    btnRun.disabled = true;
    btnRun.textContent = "Solving...";
    btnRun.style.opacity = "0.5";
    btnRun.style.cursor = "not-allowed";
  }

  const solveReq = buildSolveRequest();
  const { payload } = solveReq;

  let bodyStr;
  try {
    bodyStr = JSON.stringify(payload);
  } catch (jsonErr) {
    appendDebugLine(`❌ Failed to serialize payload: ${jsonErr}`);
    appendDebugLine("❌ Error serializing payload: " + jsonErr);
    state.solveAbortController = null;
    if (btnRun) {
      btnRun.disabled = false;
      btnRun.textContent = "Run Planner";
      btnRun.style.opacity = "1";
      btnRun.style.cursor = "pointer";
    }
    updateButtonStates();
    return;
  }

  try {
    const resp = await fetch("/api/solve_with_allocation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: bodyStr,
      signal: solveController.signal,
    });
    if (!solveIsCurrent()) return null;

    // Check if response is OK before parsing
    if (!resp.ok) {
      const errorText = await resp.text();
      appendDebugLine(`❌ Server error (${resp.status}): ${errorText.substring(0, 200)}`);
      return;
    }

    const data = await resp.json();
    if (!solveIsCurrent()) return null;
    // One line per drone, not the whole response (it carries every trajectory
    // point and the distance matrices; dumping it buried the log).
    appendDebugLine(`📋 Planner: ${Object.entries(data?.routes || {})
      .filter(([, r]) => (r?.route || []).length)
      .map(([d, r]) => `D${d} ${r.route.join("-")} · ${Number(r.distance || 0).toFixed(1)} km · ${r.points || 0} pts`)
      .join(" | ") || "no routes"}`);

    if (!data || data.success === false) {
      appendDebugLine(`❌ Planner reported an error: ${JSON.stringify(data?.errors || [])}`);
      return data;
    }

    await applySolveResult(data, solveReq, { isCurrent: solveIsCurrent });
    return solveIsCurrent() ? data : null;
  } catch (err) {
    if (!solveIsCurrent()) return null;
    if (err.name === 'AbortError') {
      appendDebugLine("⚠️ Solve request canceled.");
    } else {
      appendDebugLine("❌ Error calling /api/solve_with_allocation: " + err);
    }
    return null;
  } finally {
    if (state.solveAbortController === solveController) state.solveAbortController = null;
    // Re-enable only our own request; a replacement solve may still be running.
    if (!state.solveAbortController) {
      // Re-enable the Run Planner button
      const btnRun = $("btn-run-planner");
      if (btnRun) {
        btnRun.disabled = false;
        btnRun.textContent = "Run Planner";
        btnRun.style.opacity = "1";
        btnRun.style.cursor = "pointer";
      }
      updateButtonStates();
    }
  }
}

/**
 * Apply a draft solution to the UI for visualization
 * (without committing it to missionState.committedSegments)
 */
function applyDraftSolutionToUI(draft) {
  if (!draft) return;

  // Debug: Log what's in the draft routes before applying
  // Deep copy to allow UI modifications without affecting the stored draft
  state.sequences = JSON.parse(JSON.stringify(draft.sequences || {}));

  // For checkpoint replans: MERGE frozen trajectory with new segment trajectory
  // - state.routes[did].trajectory = frozen prefix (A → c1) from the cut
  // - draft.routes[did].trajectory = new segment (c1 → end) from solver
  // - combined = frozen + new for display
  if (draft.isCheckpointReplan) {
    const mergedRoutes = { ...state.routes };
    Object.entries(draft.routes || {}).forEach(([did, routeData]) => {
      // Use the frozen prefix captured at cut time, NOT whatever is in state.routes
      // This ensures checkpoint visual splice is based on authoritative frozen data
      let frozenTraj = draft.checkpointSegments?.[did]?.prefix
        || mergedRoutes[did]?.trajectory
        || [];
      const newTraj = routeData.trajectory || [];

      // If frozenTraj came from state.routes (not checkpointSegments), it may be FULL trajectory
      // We need to truncate it at the cut position before merging
      const usedStateRoutes = !draft.checkpointSegments?.[did]?.prefix && mergedRoutes[did]?.trajectory;
      if (usedStateRoutes && frozenTraj.length > 0) {
        // Try to get cut position from pendingCutPositions or newTraj start point
        const cutPos = draft.pendingCutPositions?.[did] || (newTraj.length > 0 ? newTraj[0] : null);
        if (cutPos) {
          // Truncate frozenTraj at the cut position
          const truncated = truncateTrajectoryAtPosition(frozenTraj, cutPos);
          if (truncated.length < frozenTraj.length) {
            appendDebugLine(`[applyDraft] D${did}: Truncated frozen traj at cut pos [${cutPos[0].toFixed(1)},${cutPos[1].toFixed(1)}]: ${frozenTraj.length} → ${truncated.length} points`);
            frozenTraj = truncated;
          }
        }
      }

      // §8.8 / §5 definitions: the ANIMATED trajectory must be the SEGMENT-ONLY
      // path (newTraj), which starts AT the diamond — because "on resume the drone
      // continues FROM the diamond" and "the drone is AT the cut point". The frozen
      // prefix (A→diamond) is the PAST: display-only history, never re-walked. The
      // old code concatenated frozen+new into the animated trajectory, so
      // trajectory[0] became the ORIGIN AIRPORT (frozenTraj[0]) and the drone was
      // drawn/animated from the airport instead of the diamond. Keep segment-only
      // for animation; stash the frozen prefix in the static-history store.
      // Accumulate per cut index so Start→C1, C1→C2, … all persist (idempotent on
      // re-display of the same cut). The cut counter identifies this cut's piece.
      if (frozenTraj.length > 0 && typeof missionReplay !== "undefined" && missionReplay.setFrozenTrajectory) {
        const cutIdx = state.checkpointSegmentCounter || 0;
        missionReplay.setFrozenTrajectory(did, frozenTraj, cutIdx);
      }

      mergedRoutes[did] = {
        ...routeData,
        trajectory: newTraj,  // segment-only: starts at the diamond
        // Current segment distance only - don't accumulate across segments.
        distance: routeData.distance || 0,
      };
    });
    // Preserve routes for drones NOT in the draft (completed drones)
    state.routes = mergedRoutes;

    // Ensure trajectoryVisible is set for ALL drones in merged routes
    Object.keys(mergedRoutes).forEach(did => {
      state.trajectoryVisible[did] = true;
    });
  } else {
    // Not a checkpoint replan - just replace as before
    state.routes = JSON.parse(JSON.stringify(draft.routes || {}));
    // Import clears Show selections. Display newly solved routes by default,
    // while preserving any explicit Show/Hide choice made by the operator.
    Object.entries(state.routes).forEach(([did, routeData]) => {
      if (state.trajectoryVisible[did] === undefined &&
          (routeData.trajectory?.length >= 2 || routeData.route?.length >= 2)) {
        state.trajectoryVisible[did] = true;
      }
    });
  }

  state.wrappedPolygons = JSON.parse(JSON.stringify(draft.wrappedPolygons || []));
  state.allocations = JSON.parse(JSON.stringify(draft.allocations || {}));
  state.allocationStrategy = draft.allocationStrategy || null;
  state.distanceMatrix = draft.distanceMatrix ? JSON.parse(JSON.stringify(draft.distanceMatrix)) : null;
  state.timeSync = draft.timeSync ? JSON.parse(JSON.stringify(draft.timeSync)) : null;
  updateTimeSyncUi();

  // After checkpoint replan, update animation drones to show at their new starting positions
  // Use draft.cutDistance (captured at solve time) instead of state.pendingCutDistance (may be stale/cleared)
  const cutDist = (draft.cutDistance != null) ? draft.cutDistance : state.pendingCutDistance;
  if (draft.isCheckpointReplan && state.animation.drones && cutDist != null) {
    Object.keys(state.animation.drones).forEach((did) => {
      if (state.routes[did]) {
        const routeInfo = state.routes[did];
        const trajectory = routeInfo.trajectory || [];

        // §10: cumulative distance from the single SAM-aware builder (matrix frame).
        const _acd = computeAnimationCumDist(trajectory, routeInfo.route, state.distanceMatrix, {
          waypointIndices: routeInfo.waypoint_indices,
          refuelAirport: routeInfo.refuel_airport,
        });
        let cumulativeDistances = _acd.cumDist;
        let totalDistance = _acd.total;

        // §8.8: the segment trajectory + cumulativeDistances are SEGMENT-RELATIVE
        // (0 = the diamond). The drone is AT the diamond when the replan displays,
        // so distanceTraveled must be 0 here — NOT the cumulative cutDist (which
        // would index the segment trajectory far off and pull the drone back near
        // the start airport). Mirrors the startAnimation init fix.
        // §8.8 fuel: preserve fuel-burned-this-leg across the display re-init so the
        // subsequent animate can keep accumulating it (the leg continues). Prefer the
        // DURABLE store state.legFuel[did]; fall back to the live drone value.
        const _priorBurnedDisp =
          typeof state.legFuel[did] === "number"
            ? state.legFuel[did]
            : state.animation.drones?.[did]?.legBurnedDistance;
        state.animation.drones[did] = {
          progress: 0,
          distanceTraveled: 0,
          legBurnedDistance: typeof _priorBurnedDisp === "number" ? _priorBurnedDisp : 0,
          animating: false,  // Not animating yet, just showing position
          cumulativeDistances,
          totalDistance,
        };
      }
    });
  }

  // Killed drones (committed kills and the one pending in this draft) stay in
  // their red diamond while the draft is shown and after Accept — the reset
  // above gave them a fresh state with nothing to draw.
  if (draft.isCheckpointReplan) {
    placeLostDrones({ upToSegment: missionReplay.getSegmentCount() - 1, includePending: true });
  }

  // Debug: Log what allocations we received
  appendDebugLine("🎯 Allocations received: " + JSON.stringify(draft.allocations));

  // Display allocations in Env tab
  updateAllocationDisplay(state.allocations, state.allocationStrategy);

  // Sync the sequence input to whatever the current routes contain
  // (the previously-selected drone may have a brand-new sequence).
  syncSequencesFromRoutes();

  if (state.animation) state.animation._dbgPos = false;  // re-arm draw-pos debug for display
  // WHOLE-MISSION fuel: use updateStatsFromAnimation, which sums state.legFuelHistory
  // (finalized legs across ALL cuts) + the active leg from the durable state.legFuel
  // store — the true mission-wide fuel. updateStatsFromRoutes (the old call here)
  // showed only the CURRENT segment's leg_distances plus frozenFlownKm, and that
  // frozen term reads ~0 on these replans, so a 287 km / 6-tank mission collapsed to
  // "68/100". updateStatsFromAnimation degrades safely with no live animation (it
  // falls back to the durable store + planned distance), so it is correct here too.
  updateStatsFromAnimation();
  drawEnvironment();
}

// Cancel any running solve request
function cancelSolve() {
  if (state.solveAbortController) {
    state.solveAbortController.abort();
    appendDebugLine("⚠️ Canceling solve request...");
  } else {
    appendDebugLine("No solve request in progress.");
  }
}

// ----------------------------------------------------
// Initial environment load from backend (optional)
// ----------------------------------------------------
async function initialLoadEnv() {
  // Helper to create default empty environment
  function createDefaultEnv() {
    state.env = { airports: [], targets: [], sams: [] };
    state.timeSync = null;
    updateTimeSyncUi();
    appendDebugLine("Created default empty environment. Add airports and targets to begin.");
    initDroneConfigsFromEnv();
  }

  try {
    const resp = await fetch("/api/environment");
    if (!resp.ok) {
      appendDebugLine("No /api/environment available, using default empty environment.");
      createDefaultEnv();
      drawEnvironment();
      return;
    }
    const data = await resp.json();
    if (!data || data.success === false) {
      appendDebugLine("Backend /api/environment responded but not success, using default.");
      createDefaultEnv();
      drawEnvironment();
      return;
    }
    state.env = migrateLegacyMapCoordinates(data.environment || null);
    state.envFilename = data.filename || null;

    // If backend returned null environment, create default
    if (!state.env) {
      createDefaultEnv();
      drawEnvironment();
      return;
    }

    if (state.envFilename) {
      $("env-filename").textContent = state.envFilename;
    }

    // Reset mission when loading a new environment from backend
    state.missionId = null;
    state.routes = {};
    state.sequences = {};
    state.trajectoryVisible = {};
    state.timeSync = null;
    updateTimeSyncUi();

    appendDebugLine("Loaded environment from backend.");
    initDroneConfigsFromEnv();
    fillMissingAltitudes(state.env);

    // Fetch SAM wrapping if there are SAMs in the environment
    if (state.env && state.env.sams && state.env.sams.length > 0) {
      fetchWrappedPolygons(true).then(() => drawEnvironment());
    } else {
      drawEnvironment();
    }
  } catch (err) {
    appendDebugLine("Failed to fetch /api/environment: " + err);
    createDefaultEnv();
    drawEnvironment();
  }
}

// ----------------------------------------------------
// Tab switching
// ----------------------------------------------------
let tabControls = null;
function attachTabSwitching() {
  if (!tabControls) tabControls = ISRTabs.create(document);
  tabControls.mount();
}

// ----------------------------------------------------
// Trajectory visibility controls
// ----------------------------------------------------
function updateTrajectoryButtonStates() {
  for (let did = 1; did <= 5; did++) {
    const box = $(`traj-d${did}`);
    if (box) box.checked = Boolean(state.trajectoryVisible[String(did)]);
  }

  // "Show all" / "Hide all": toggles every drone's trajectory.
  const allBtn = $("traj-all");
  if (allBtn) {
    const visible = Object.values(state.trajectoryVisible);
    const allVisible = visible.length > 0 && visible.every((v) => v);
    allBtn.textContent = allVisible ? "Hide all" : "Show all";
  }
}

function toggleTrajectory(did) {
  state.trajectoryVisible[did] = !state.trajectoryVisible[did];
  updateTrajectoryButtonStates();
  drawEnvironment();
  appendDebugLine(`Trajectory D${did}: ${state.trajectoryVisible[did] ? "shown" : "hidden"}`);
}

function toggleAllTrajectories() {
  const visibleValues = Object.values(state.trajectoryVisible);
  // If no trajectories set yet, or none visible, show all; otherwise toggle off
  const allVisible = visibleValues.length > 0 && visibleValues.every((v) => v);
  const newState = !allVisible;

  for (let did = 1; did <= 5; did++) {
    state.trajectoryVisible[String(did)] = newState;
  }

  updateTrajectoryButtonStates();
  drawEnvironment();
  appendDebugLine(`All trajectories: ${newState ? "shown" : "hidden"}`);
}

function attachTrajectoryControls() {
  for (let did = 1; did <= 5; did++) {
    const box = $(`traj-d${did}`);
    if (box) {
      box.addEventListener("change", () => toggleTrajectory(String(did)));
    }
  }

  const allBtn = $("traj-all");
  if (allBtn) {
    allBtn.addEventListener("click", toggleAllTrajectories);
  }

  // Initialize button states
  updateTrajectoryButtonStates();
}

// Manual "C"-key cut during animation: no staged edits, so this is a thin
// call-site adapter over the SAME cut engine the live-edit-disable flow uses
// (prepareCutFromCurrentState) — the staged-env/staged-configs/disabled-
// drone/fuel-carry logic in that function is a no-op here (nothing staged),
// so this only adds the bits specific to a plain manual cut: the average-
// progress log line and a direct redraw (prepareCutFromCurrentState's own
// redraw is the coalesced requestDraw(), which is what its other caller,
// the live-edit path, wants instead).
async function freezeAtCheckpoint() {
  if (!state.animation || !state.animation.drones || Object.keys(state.animation.drones).length === 0) {
    appendDebugLine("No animation data to freeze (drones: " + JSON.stringify(state.animation?.drones) + ")");
    return;
  }

  appendDebugLine(`freezeAtCheckpoint: drones=${Object.keys(state.animation?.drones || {}).join(",")}`);

  await prepareCutFromCurrentState();

  const avgPct = Object.values(state.animation.drones)
    .filter(d => d.totalDistance > 0)
    .map(d => (d.distanceTraveled || 0) / d.totalDistance)
    .reduce((a, b, _, arr) => a + b / arr.length, 0) * 100;

  appendDebugLine(`✂️ pendingCutPositions=${JSON.stringify(state.pendingCutPositions || {})}`);
  const checkpointIds = Object.values(state.pendingCheckpoints || {}).map(cp => cp.id);
  appendDebugLine(`✂️ pendingCheckpoints: ${checkpointIds.join(", ") || "none"}`);

  updateAnimationButtonStates();
  drawEnvironment();
  appendDebugLine(`Frozen at ${avgPct.toFixed(0)}% progress`);
}
// ----------------------------------------------------
// Animation controls
// ----------------------------------------------------
/**
 * Airport-departure pause schedule for a route: the cumulative distances at which the
 * drone TOUCHES each airport (so the animation pauses ~0.5s on arrival). Walks the
 * route in order, matching each waypoint to the CLOSEST trajectory point inside its
 * 0.5 km bubble. Only AIRPORTS produce a pause (targets don't). Returns
 * [{wp, dist}, …]. Used at startAnimation AND rebuilt on each segment switch so the
 * post-cut segment's pauses land at THAT segment's airports — not the stale seg-0 ones.
 */
function buildWaypointDistances(route, trajectory, cumulativeDistances) {
  const out = [];
  if (!route || route.length === 0 || !trajectory || trajectory.length === 0) return out;
  const airportLookup = {};
  (state.env.airports || []).forEach(a => { airportLookup[a.id || a.label] = [a.lat, a.lng]; });
  const allWpLookup = { ...airportLookup };
  (state.env.targets || []).forEach(t => { allWpLookup[t.id || t.label] = [t.lat, t.lng]; });
  const BUBBLE_KM = 0.5;
  let searchStartIdx = 0;
  for (let r = 0; r < route.length - 1; r++) {  // skip last waypoint (final landing)
    const wp = route[r];
    const pos = allWpLookup[wp];
    if (!pos) continue;
    let inBubble = false;
    let touchIdx = -1;
    let touchD = Infinity;
    for (let i = searchStartIdx; i < trajectory.length; i++) {
      const d = haversineKm(trajectory[i][0], trajectory[i][1], pos[0], pos[1]);
      if (d < BUBBLE_KM) {
        inBubble = true;
        if (d < touchD) { touchD = d; touchIdx = i; }
      } else if (inBubble) {
        break;  // exited the bubble — closest-so-far IS the touch
      }
    }
    if (touchIdx >= 0) {
      searchStartIdx = touchIdx + 1;
      if (airportLookup[wp] && touchIdx < cumulativeDistances.length) {
        out.push({ wp, dist: cumulativeDistances[touchIdx] });
      }
    }
  }
  return out;
}

const ANIMATION_BASE_MISSION_SECONDS_PER_WALL_SECOND = 1;
// Mission Control's speed slider steps (index = slider value). Checked: the
// same visits, refuel legs and fuel at 4x, 32x and 128x.
const ANIMATION_SPEEDS = [0.25, 0.5, 1, 2, 4, 8, 16, 32, 64, 128];
const AIRPORT_DEPARTURE_PAUSE_MISSION_SECONDS = 1;

function airportDeparturePauseSeconds(did) {
  // ToT frames already define the full arrival schedule. An explicit zero
  // prevents presentation pauses from adding time outside that schedule.
  const seconds = state.routes?.[did]?.anim_frame?.airport_pause_seconds;
  return typeof seconds === "number" && Number.isFinite(seconds) && seconds >= 0
    ? seconds : AIRPORT_DEPARTURE_PAUSE_MISSION_SECONDS;
}

function advanceMissionClock(dtSeconds) {
  return advanceMissionClockSeconds(animationMissionDeltaSeconds(dtSeconds));
}

function advanceMissionClockSeconds(seconds, distanceLimits = {}) {
  // Aircraft and weather consume this same step, including all airport holds.
  seconds = Math.max(0, seconds);
  const flying = Object.entries(state.animation.drones || {}).filter(([, d]) =>
    d.animating && d.distanceTraveled < d.totalDistance);
  if (flying.length) {
    const remainingSeconds = Math.max(...flying.map(([did, d]) => {
      const limit = Math.min(d.totalDistance, distanceLimits[did] ?? Infinity);
      if (d.distanceTraveled >= limit) return 0;
      const airportHolds = (d._waypointDistances || []).slice(d._wpPauseIdx || 0)
        .filter(w => w.dist >= d.distanceTraveled && w.dist < limit).length;
      return 60 * ((d._launchDelayRemaining || 0) + (d._pauseRemaining || 0) +
        (limit - d.distanceTraveled) / (Number(state.droneConfigs[did]?.speed) || 1)) +
        airportHolds * airportDeparturePauseSeconds(did);
    }));
    seconds = Math.min(seconds, remainingSeconds);
  }
  if (typeof window !== "undefined" && window.weatherCheckpoints) {
    seconds = window.weatherCheckpoints.limitStep(seconds);
  }
  state.animation.elapsedMissionSeconds = (state.animation.elapsedMissionSeconds || 0) + seconds;
  return seconds / 60;
}

function animationMissionDeltaSeconds(dtSeconds) {
  const speedMultiplier = state.animation.speedMultiplier || 1;
  return ANIMATION_BASE_MISSION_SECONDS_PER_WALL_SECOND * speedMultiplier * dtSeconds;
}

function createAnimationFrameClock() {
  let lastTime = null;
  let changes = [];
  return {
    changeSpeed(speed, currentTime) {
      if (lastTime !== null) {
        changes.push({ time: currentTime, speed, previous: state.animation.speedMultiplier || 1 });
      }
    },
    takeMissionSeconds(currentTime) {
      if (lastTime === null) { lastTime = currentTime; changes = []; return 0; }
      let cursor = lastTime;
      let speed = changes.length ? changes[0].previous : state.animation.speedMultiplier || 1;
      let seconds = 0;
      for (const change of changes) {
        const time = Math.max(cursor, Math.min(currentTime, change.time));
        seconds += (time - cursor) / 1000 * speed;
        cursor = time;
        speed = change.speed;
      }
      seconds += Math.max(0, currentTime - cursor) / 1000 * speed;
      lastTime = currentTime;
      changes = [];
      return seconds * ANIMATION_BASE_MISSION_SECONDS_PER_WALL_SECOND;
    },
  };
}

function recordAnimationRefuel(did, drone, waypoint) {
  const route = state.routes[did] || {};
  const stops = route.refuel_stops?.length ? route.refuel_stops
    : route.refuel_indices?.length && Array.isArray(route.route)
      ? route.refuel_indices.map(i => route.route[i]).filter(w => w != null)
      : route.refuel_airport && route.refuel_airport !== "-" ? [route.refuel_airport] : [];
  if (!waypoint.isRefuel && !stops.some(s => String(s) === String(waypoint.wp))) return;
  const burned = state.legFuel[did] || drone.legBurnedDistance || 0;
  // An airport at departure has no completed fuel leg.
  if (burned > 0.05) {
    if (!Array.isArray(state.legFuelHistory[did])) state.legFuelHistory[did] = [];
    state.legFuelHistory[did].push(burned);
    appendDebugLine(`⛽ D${did}: refueled at ${waypoint.wp} — leg ${state.legFuelHistory[did].length} finalized (${burned.toFixed(1)}), new leg fuel reset`);
  }
  drone.legBurnedDistance = 0;
  state.legFuel[did] = 0;
}

function advanceDroneMissionTime(did, drone, minutes, distanceLimit = Infinity) {
  const speed = Number(state.droneConfigs[did]?.speed) || 1;
  const limit = Math.min(drone.totalDistance, distanceLimit);
  const waypoints = drone._waypointDistances || [];
  drone._wpPauseIdx = drone._wpPauseIdx || 0;
  while (minutes > 0 && drone.distanceTraveled < limit) {
    // Consume every event crossed in this frame; no travel time is discarded
    // when a slow callback crosses an airport, a launch delay, or several stops.
    for (const key of ['_launchDelayRemaining', '_pauseRemaining']) {
      const consumed = Math.min(drone[key] || 0, minutes);
      drone[key] = Math.max(0, (drone[key] || 0) - consumed);
      drone._elapsedHoldMinutes = (drone._elapsedHoldMinutes || 0) + consumed;
      minutes = Math.max(0, minutes - consumed);
    }
    if (minutes <= 0) break;
    while (drone._wpPauseIdx < waypoints.length &&
        waypoints[drone._wpPauseIdx].dist < drone.distanceTraveled) drone._wpPauseIdx++;
    const waypoint = waypoints[drone._wpPauseIdx];
    const nextDistance = Math.min(limit, waypoint?.dist ?? Infinity);
    const distance = Math.min(minutes * speed, Math.max(0, nextDistance - drone.distanceTraveled));
    drone.distanceTraveled += distance;
    drone.legBurnedDistance = (drone.legBurnedDistance || 0) + distance;
    state.legFuel[did] = drone.legBurnedDistance;
    minutes = Math.max(0, minutes - distance / speed);
    if (waypoint && waypoint.dist <= limit && drone.distanceTraveled >= waypoint.dist) {
      drone._wpPauseIdx++;
      drone._pauseRemaining = airportDeparturePauseSeconds(did) / 60;
      recordAnimationRefuel(did, drone, waypoint);
      appendDebugLine(`⏸️ D${did}: airport departure at ${waypoint.wp}@${waypoint.dist.toFixed(1)}`);
    } else {
      break;
    }
  }
}

// Killed drones have no route to fly, so nothing gives them an animation state —
// place each one, stopped, in its red diamond. Covers kills in committed segments
// up to `upToSegment` (default: the current one, so a replay doesn't show a kill
// that hasn't happened yet) and, with includePending, a kill still in the draft.
// ONE place for this: the draft display and startAnimation both call it.
function placeLostDrones({ upToSegment, includePending = false } = {}) {
  const lost = [];  // [did, [lat, lng], cutDistance]
  if (typeof missionReplay !== "undefined" && missionReplay.getSegmentCount) {
    const last = upToSegment ?? missionReplay.getCurrentSegmentIndex();
    for (let segIdx = 1; segIdx <= last; segIdx++) {
      const seg = missionReplay.getSegment(segIdx);
      const segCutPos = seg?.cutPosition || seg?.cutPositions;
      (seg?.lostDrones || []).forEach((did) => {
        if (segCutPos?.[did]) lost.push([String(did), segCutPos[did], seg.cutDistance || 0]);
      });
    }
  }
  if (includePending) {
    (state.pendingLostDrones || []).forEach((did) => {
      const pos = state.pendingCutPositions?.[did];
      if (pos) lost.push([String(did), pos, state.pendingCutDistance || 0]);
    });
  }
  lost.forEach(([did, pos, cutDistance]) => {
    const ds = state.animation.drones[did];
    const traj = state.routes?.[did]?.trajectory || [];
    if (ds?._lostAtCutDistance !== undefined || traj.length >= 2) return;  // placed / still flying
    state.animation.drones[did] = {
      progress: 0,
      distanceTraveled: 0,
      animating: false,
      totalDistance: 0,
      _lostAtPosition: [...pos],
      _lostAtCutDistance: cutDistance,
    };
    state.trajectoryVisible[did] = true;
    appendDebugLine(`D${did}: LOST - drawn in its red diamond [${pos[0].toFixed(4)}, ${pos[1].toFixed(4)}]`);
  });
}

// Drones that have a path to fly on the routes currently loaded.
function dronesWithTrajectories() {
  return Object.keys(state.routes || {})
    .map(String)
    .filter((did) => Array.isArray(state.routes[did]?.trajectory) && state.routes[did].trajectory.length >= 2);
}

// Live-created segments store their own unvisited targets at Accept. Rebuild
// the display cumulatively, retaining earlier targets without revealing later
// ones. Use the current segment's environment for airports, SAMs and settings.
function getCreatedReplayEnv(segmentIndex) {
  const env = JSON.parse(JSON.stringify(missionReplay.getSegment(segmentIndex).env));
  const targets = new Map();
  for (let i = 0; i <= segmentIndex; i++) {
    for (const target of missionReplay.getSegment(i).env.targets || []) {
      targets.set(target.id, target);
    }
  }
  env.targets = JSON.parse(JSON.stringify([...targets.values()]));
  return env;
}

// droneIds: the drones to animate, or nothing for "all drones" — resolved below,
// AFTER the replay branches may have swapped in the original segment-0 routes.
// (A caller's list is built from the routes on screen — the LAST segment — where
// a drone killed later has no route, so the first replay used to leave it out.)
function startAnimation(droneIds) {
  if (typeof window !== "undefined" && window.weatherPreview?.allowAnimation?.() === false) return;
  const perms = getUiPermissions();
  const animateAll = !droneIds;

  // Debug: log which drones are being passed to animation
  appendDebugLine(`startAnimation called with droneIds=[${animateAll ? "all" : droneIds.join(",")}]`);
  state.animation._dbgPos = false;  // re-arm the one-shot draw-position debug

  // Check if we can animate or resume
  if (!perms.canAnimate && !perms.canResume) {
    appendDebugLine("⚠️ Cannot animate in current state. Accept a solution first.");
    return;
  }

  // If we're resuming from pause, restore context
  if (perms.canResume && missionState.pauseContext) {
    resumeAnimation();
    return;
  }

  // Stop any existing animation
  stopAnimation();

  // FULL-MISSION REPLAY: the mission ran to its natural end last time. Replay the
  // WHOLE original mission start→end (not just the last segment). Load seg-0's full
  // airport→end solution and rewind missionReplay to segment 0 — but DON'T destroy
  // the cut segments (that is RESET's job). Only triggers when complete; a
  // mid-segment pause was already handled by the resume path above.
  // Replay-mode toggle: "Replay segments" (checkbox) re-flies the cut history —
  // start on the ORIGINAL pre-cut solve, stop at each cut diamond, load that
  // segment's env + replan, continue. Otherwise replay is CONTINUOUS (one flight).
  const _replaySegmentsCb = document.getElementById("mc-replay-segments");
  const replaySegments = !!(_replaySegmentsCb && _replaySegmentsCb.checked);

  // Imported pre-solved missions must honor the replay-mode choice on their FIRST
  // Animate, not only after one complete run. Checked keeps the normal progressive
  // segment switches. Unchecked builds the already-flown stitched mission up front.
  if (!state.animation.missionComplete && segmentedImport.isActive() &&
      missionReplay.getSegmentCount() > 1) {
    if (replaySegments) {
      state.animation.continuousReplay = false;
      missionReplay.setCurrentSegmentIndex(0);
    } else {
      const finalSegmentIndex = missionReplay.getSegmentCount() - 1;
      const stitchedRoutes = buildCombinedRoutesFromSegments(finalSegmentIndex);
      if (Object.keys(stitchedRoutes).length > 0) {
        state.routes = stitchedRoutes;
        Object.keys(state.routes).forEach((did) => {
          state.trajectoryVisible[did] = true;
        });
        state.animation.continuousReplay = true;
        state.animation.forceStartDistanceZero = true;
        appendDebugLine("🎬 Imported continuous replay: stitched all flown segments");
      }
    }
  }

  if (state.animation.missionComplete && replaySegments) {
    // SEGMENT-BY-SEGMENT REPLAY. Load seg-0's FULL original solve (start→end, the
    // pre-cut trajectory) and rewind to segment 0, keeping all segments intact and
    // continuousReplay OFF. The animate loop's checkSegmentBoundary then stops at
    // each cut diamond and swaps in the next segment's env + routes — the exact
    // machinery an imported segmented mission uses. The drone flies the original
    // path up to cut1, then segment 1's replan to cut2, and so on.
    state.animation.missionComplete = false;  // consume
    const seg0 = missionState.seg0FullSolution
      || missionReplay.getSegment?.(0)?.solution;
    if (seg0?.routes && Object.keys(seg0.routes).length > 0) {
      state.routes = JSON.parse(JSON.stringify(seg0.routes));
      Object.keys(state.routes).forEach((did) => { state.trajectoryVisible[did] = true; });
      missionReplay.setCurrentSegmentIndex(0);
      missionState.currentSegmentIndex = 0;
      if (segmentedImport.isActive()) {
        // Routes and the displayed environment must rewind together. Leaving
        // the import manager at the final segment reveals future targets until
        // the first cut reloads the environment.
        segmentedImport._currentSegmentIndex = 0;
        state.env = segmentedImport.getEnvForDisplay();
        state.droneConfigs = segmentedImport.getDroneConfigsForSegment(0)
          || state.env.drone_configs || state.droneConfigs;
        state.env.drone_configs = state.droneConfigs;
        refreshDroneConfigUI();
      } else {
        state.env = getCreatedReplayEnv(0);
        state.droneConfigs = JSON.parse(JSON.stringify(
          missionReplay.getSegment(0).drone_configs
          || state.env.drone_configs || state.droneConfigs));
        state.env.drone_configs = state.droneConfigs;
        refreshDroneConfigUI();
      }
      if (missionReplay.clearFrozenTrajectories) missionReplay.clearFrozenTrajectories();
      state.legFuel = {};
      state.legFuelHistory = {};
      clearVisited();
      state.pendingCutDistance = null;
      state.pendingCutPositions = null;
      state.animation.forceStartDistanceZero = true;  // start at distance 0
      state.animation.continuousReplay = false;        // DO switch at cut diamonds
      appendDebugLine(`🎬 Segment-by-segment replay: loaded seg-0 original solve, ${missionReplay.getSegmentCount()} segments kept`);
    }
  } else if (state.animation.missionComplete) {
    state.animation.missionComplete = false;  // consume — one full replay
    // Replay the ACTUAL FLOWN mission (start→cut1→…→end): every segment's flown
    // part, stitched by the SAME builder an imported mission's replay uses. (A
    // separate live stitch copied the last segment's route record, anim_frame
    // included, so the player flew only the last segment and the earlier flown
    // path vanished.) A mission with no cuts replays its own route as is.
    const stitchedRoutes = missionReplay.getSegmentCount() > 1
      ? buildCombinedRoutesFromSegments(missionReplay.getSegmentCount() - 1)
      : {};
    if (Object.keys(stitchedRoutes).length > 0) {
      state.routes = JSON.parse(JSON.stringify(stitchedRoutes));
      Object.keys(state.routes).forEach((did) => { state.trajectoryVisible[did] = true; });
      // Rewind playback to the start; segments stay intact so the user can re-cut.
      missionReplay.setCurrentSegmentIndex(0);
      if (missionReplay.clearFrozenTrajectories) missionReplay.clearFrozenTrajectories();
      state.animation.continuousReplay = true;  // suppress segment-boundary switching
      appendDebugLine(`🎬 Full-mission replay: stitched all ${missionReplay.getSegmentCount()} segments (${Object.keys(stitchedRoutes).length} drones), segments kept`);
    } else {
      state.animation.continuousReplay = false;
      appendDebugLine("🎬 Full-mission replay: no cuts, flying the route again");
    }
    // Every replay of a finished mission is a fresh flight, cuts or not: clear
    // the per-leg fuel and visited targets so they re-accumulate from zero.
    // (This used to sit inside the stitched branch, so a mission with no cuts
    // replayed on top of the last run's fuel and kept its targets ticked.)
    state.legFuel = {};
    state.legFuelHistory = {};
    clearVisited();
    state.pendingCutDistance = null;
    state.pendingCutPositions = null;
    state.animation.forceStartDistanceZero = true;  // start at distance 0
  } else if (!(segmentedImport.isActive() && !replaySegments &&
               missionReplay.getSegmentCount() > 1)) {
    // Normal animate (fresh segment / cut resume): segment switching must work, so
    // make sure the continuous-replay suppression flag is OFF.
    state.animation.continuousReplay = false;
  }

  // Check if we're in a frozen checkpoint state WITHOUT a replan
  // If checkpoint is active (frozen) but no cut distance exists, block animation
  const hasFrozenCheckpoint = state.checkpoint?.active ||
    Object.keys(state.checkpoint?.segments || {}).length > 0;
  const hasReplanData = state.pendingCutDistance !== null && state.pendingCutDistance > 0;

  if (hasFrozenCheckpoint && !hasReplanData) {
    appendDebugLine("⚠️ Cannot animate: checkpoint is frozen. Run Planner to replan from checkpoint, or press R to reset.");
    return;
  }

  // Clear checkpoint state so frozen drones don't persist
  if (state.checkpoint) {
    state.checkpoint.active = false;
    state.checkpoint.segments = {};
  }

  // Restore full trajectories if they were truncated by a previous freeze —
  // BUT NOT for a checkpoint replan resume (§8.8). The comment said "not for a
  // checkpoint replan" but the guard was MISSING: on resume it overwrote the
  // spliced segment trajectory (diamond→end) with the full ORIGINAL start→end
  // trajectory, sending the drone back to the start airport. A checkpoint replan
  // resume is indicated by a current segment index > 0 (resuming mid-mission) or
  // a pending cut distance. In that case, keep the spliced trajectory as-is.
  const isCheckpointResume =
    (typeof missionReplay !== "undefined" &&
      missionReplay.getCurrentSegmentIndex &&
      missionReplay.getCurrentSegmentIndex() > 0) ||
    (state.pendingCutDistance !== null && state.pendingCutDistance > 0);
  if (!isCheckpointResume) {
    state.animation.elapsedMissionSeconds = 0;
    Object.keys(state.routes || {}).forEach((did) => {
      if (state.routes[did]._fullTrajectory) {
        state.routes[did].trajectory = state.routes[did]._fullTrajectory;
        delete state.routes[did]._fullTrajectory;
      }
    });
  }

  // Initialize animation state for selected drones
  state.animation.active = true;
  state.animation.drones = {};

  // NEW: Single mission distance tracker (same for all drones since they fly at same speed)
  // Get starting distance from MissionReplay (0 for segment 0, cutDistance for checkpoint replans)
  // Exception: After Reset, we force start at 0 even though missionReplay index is 1 (for marker drawing)
  if (state.animation.forceStartDistanceZero) {
    state.animation.missionDistance = 0;
    state.animation.forceStartDistanceZero = false; // Clear the flag
    appendDebugLine(`🎬 Mission starting at distance: 0 (forced after Reset)`);
  } else {
    state.animation.missionDistance = missionReplay.getStartingDistance();
    appendDebugLine(`🎬 Mission starting at distance: ${state.animation.missionDistance.toFixed(1)}`);
  }

  // Enable debug stop at splice point if this is a checkpoint replan
  const isCheckpointReplan = state.animation.missionDistance > 0;
  state.debugStopAtSplice = isCheckpointReplan;  // Set true to stop at splice point

  // Debug: Log visited targets and MissionReplay state at animation start
  appendDebugLine(`🎬 Animation starting. Visited targets: [${state.visitedTargets.join(", ")}]`);
  const replayInfo = missionReplay.getDebugInfo();
  appendDebugLine(`🎬 MissionReplay: ${replayInfo.segmentCount} segments, current=${replayInfo.currentIndex}`);
  if (isCheckpointReplan) {
    appendDebugLine(`🛑 DEBUG: Will stop at splice point (prefix distance)`);
  }

  if (animateAll) droneIds = dronesWithTrajectories();

  droneIds.forEach((did) => {
    const routeInfo = state.routes[did];

    // Debug: Log why a drone might be skipped
    if (!routeInfo) {
      appendDebugLine(`⚠️ D${did} skipped: no routeInfo`);
      return;
    }

    // Debug: Log trajectory info at animation start
    const trajLen = routeInfo.trajectory ? routeInfo.trajectory.length : 0;
    const routeStr = (routeInfo.route || []).join("-");
    const firstPt = trajLen > 0 ? `(${routeInfo.trajectory[0][0].toFixed(1)},${routeInfo.trajectory[0][1].toFixed(1)})` : "N/A";
    const lastPt = trajLen > 0 ? `(${routeInfo.trajectory[trajLen-1][0].toFixed(1)},${routeInfo.trajectory[trajLen-1][1].toFixed(1)})` : "N/A";
    appendDebugLine(`🎬 D${did}: ${trajLen} pts, route: ${routeStr}, from ${firstPt} to ${lastPt}`);
    if (!routeInfo.route) {
      appendDebugLine(`⚠️ D${did} skipped: no route array`);
      return;
    }
    if (routeInfo.route.length < 2) {
      appendDebugLine(`⚠️ D${did} skipped: route too short (${routeInfo.route.length} waypoints)`);
      return;
    }

    // §frame-rebuild: the backend now emits ONE authoritative animation frame
    // (anim_frame: densified trajectory + lockstep cumulative_distances +
    // remapped waypoint_indices + leg_breaks). When present, the player consumes
    // it VERBATIM — no client densify, no loiter haversine-rebuild, no
    // computeAnimationCumDist. This is the single source of truth that fixes the
    // speed-jumps, the wrong per-leg fuel on revisit-airport routes, and the
    // stale-waypoint_indices drift. The legacy build path below runs only when no
    // anim_frame is available (e.g. a stitched full-mission replay trajectory).
    let trajectory = routeInfo.trajectory || [];
    let cumulativeDistances;
    let totalDistance;
    let replayRefuelEvents = routeInfo.replay_refuel_events;
    const _af = routeInfo.anim_frame;
    const _useFrame =
      _af && Array.isArray(_af.trajectory) && _af.trajectory.length >= 2 &&
      Array.isArray(_af.cumulative_distances) &&
      _af.cumulative_distances.length === _af.trajectory.length;

    if (_useFrame) {
      trajectory = _af.trajectory;
      routeInfo.trajectory = _af.trajectory;
      routeInfo.waypoint_indices = _af.waypoint_indices || routeInfo.waypoint_indices;
      cumulativeDistances = _af.cumulative_distances;
      totalDistance = _af.total_distance || cumulativeDistances[cumulativeDistances.length - 1] || 0;
      appendDebugLine(`🎞️ D${did} using backend anim_frame: ${trajectory.length} pts, total ${totalDistance.toFixed(1)}, legBreaks [${(_af.leg_breaks||[]).map(x=>x.toFixed(1)).join(",")}]`);
    } else {
    // LEGACY frame build (no anim_frame — e.g. stitched replay). §10 SAM-aware
    // builder + densify, the path the rebuild replaces.
    const _acdInit = computeAnimationCumDist(trajectory, routeInfo.route, state.distanceMatrix, {
      waypointIndices: routeInfo.waypoint_indices,
      refuelAirport: routeInfo.refuel_airport,
    });
    cumulativeDistances = _acdInit.cumDist;
    totalDistance = _acdInit.total;

    if (trajectory.length >= 2) {

      // Densify trajectory for uniform visual speed WITHOUT dropping vertices.
      // §9/§8.1: every original vertex (which includes the route waypoints —
      // i.e. the TARGETS) MUST survive, so isTargetVisited() can match the
      // target's exact vertex. We therefore walk each original segment and only
      // INSERT interpolated points between its endpoints; we never replace the
      // endpoints. (The old version resampled onto a fixed grid and discarded
      // the target vertices, which is why some targets were never "crossed" —
      // and why a 500 m radius was bolted on to compensate. No more patch.)
      if (totalDistance > 0) {
        const RESAMPLE_STEP_KM = totalDistance / Math.max(200, trajectory.length);
        const densified = [trajectory[0]];
        const densifiedCumDist = [0];

        for (let i = 1; i < trajectory.length; i++) {
          const p0 = trajectory[i - 1];
          const p1 = trajectory[i];
          const segStart = cumulativeDistances[i - 1];
          const segDist = cumulativeDistances[i] - segStart;

          // Insert intermediate points strictly BETWEEN the endpoints.
          if (segDist > RESAMPLE_STEP_KM) {
            const steps = Math.floor(segDist / RESAMPLE_STEP_KM);
            for (let s = 1; s < steps; s++) {
              const t = (s * RESAMPLE_STEP_KM) / segDist;
              densified.push(interpolateFlightPosition(p0, p1, t));
              densifiedCumDist.push(segStart + s * RESAMPLE_STEP_KM);
            }
          }

          // ALWAYS keep the original vertex p1 (target vertices preserved exactly).
          densified.push(p1);
          densifiedCumDist.push(cumulativeDistances[i]);
        }

        // Replace trajectory and cumulative distances with the densified version.
        trajectory = densified;
        routeInfo.trajectory = densified;
        cumulativeDistances = densifiedCumDist;
        // totalDistance stays the same
      }
    }
    } // end legacy frame build (!_useFrame)

    if (!_useFrame) {
      const loiterFrame = addLoiterIntervals(trajectory, cumulativeDistances,
        routeInfo.replay_loiter_specs || routeLoiterSpecs(
          routeInfo.route, state.env, state.droneConfigs[did] || {}));
      trajectory = loiterFrame.trajectory;
      routeInfo.trajectory = trajectory;
      cumulativeDistances = loiterFrame.cumDist;
      totalDistance = loiterFrame.total;
      if (Array.isArray(replayRefuelEvents)) {
        replayRefuelEvents = replayRefuelEvents.map(event =>
          ({ ...event, dist: loiterFrame.shiftDistance(event.dist) }));
      }
    }

    // Compute cumulative-distance value at which each AIRPORT departure happens.
    // A 0.5s pause is added when the drone leaves an airport — mission start
    // (departing the launch airport) and any refuel stop. The final landing
    // airport gets no pause (drone is done). This models the takeoff delay
    // without mutating trajectory geometry.
    let _waypointDistances = buildWaypointDistances(
      routeInfo.route || [], trajectory, cumulativeDistances
    );
    if (Array.isArray(replayRefuelEvents)) {
      _waypointDistances = replayRefuelEvents.map((event) => ({ ...event }));
    }
    if (_waypointDistances.length) {
      const wpList = _waypointDistances.map(w => `${w.wp}@${w.dist.toFixed(1)}`).join(", ");
      appendDebugLine(`⏸️ D${did}: ${_waypointDistances.length} airport-departure pauses: [${wpList}]`);
    }

    if (totalDistance <= 0) {
      appendDebugLine(`⚠️ D${did} skipped: totalDistance is ${totalDistance} (trajectory has ${trajectory.length} points)`);
      return;
    }

    // §8.8: the per-drone trajectory + cumulativeDistances + _waypointDistances are
    // SEGMENT-RELATIVE (0 = the diamond/segment start). So distanceTraveled must
    // start at 0 here — NOT at the cumulative missionDistance (cutDistance), which
    // would index the segment trajectory far past the diamond and render the drone
    // back near the start. The cumulative frame is preserved separately via
    // segmentStartOffset (added back into the global missionDistance in the advance
    // loop) so checkSegmentBoundary / next-cut keep working in cumulative terms.
    state.animation.segmentStartOffset = state.animation.missionDistance || 0;
    const initialDistance = 0;

    // Find refuel point distance (if this drone has a refuel airport)
    let refuelDistance = null;
    const refuelAirport = routeInfo.refuel_airport;
    if (refuelAirport && refuelAirport !== "-") {
      const refuelEntry = _waypointDistances.find(w => w.wp === refuelAirport);
      if (refuelEntry) {
        refuelDistance = refuelEntry.dist;
      }
    }

    // §8.8 fuel: carry forward fuel-burned-this-leg across cuts. state.legFuel[did]
    // is the DURABLE, AUTHORITATIVE store of "km burned on the current leg since the
    // last real refuel". It accumulates during flight (12022/12900) and resets to 0
    // ONLY when the drone crosses a refuel airport (12067/12945) or on an EXPLICIT
    // fresh-mission / env-load / reset clear (4126 / 8661 / 11488 / 11546). A CUT is
    // fuel-neutral: the burn must survive it.
    //
    // ROOT FIX: startAnimation must only SEED the volatile per-drone counter FROM the
    // durable store — it must NEVER zero the durable store. The old code gated the
    // seed on `isCheckpointResume`; that gate rests on getCurrentSegmentIndex()>0
    // (pendingCutDistance is nulled at accept, 2933), so on a mid-mission re-animate
    // it went false and line `state.legFuel[did] = 0` DESTROYED the accumulated burn.
    // That gave start_fuel = fullTank − 0 = a FREE FULL TANK at every such cut (a cut
    // wrongly became a new tank), and simultaneously collapsed the whole-mission fuel
    // stat to the current segment. Reading (never writing 0) fixes both: a fresh
    // mission already has legFuel==0 from its explicit clear; a resume carries its
    // accumulated value.
    const _priorBurned = state.legFuel[did];
    const _seedBurned = (typeof _priorBurned === "number" && _priorBurned > 0)
      ? _priorBurned
      : 0;
    // Do NOT overwrite state.legFuel[did] here — the durable store is the source of
    // truth and is only reset by a real refuel or an explicit fresh/reset clear.
    if (state.legFuel[did] == null) state.legFuel[did] = _seedBurned;
    const launchDelay = Math.max(0, Number(routeInfo.launch_delay || 0));
    if (launchDelay > 0) {
      appendDebugLine(`⏱️ D${did}: launch delay ${launchDelay.toFixed(2)} before time-sync departure`);
    }
    state.animation.drones[did] = {
      progress: totalDistance > 0 ? initialDistance / totalDistance : 0,
      distanceTraveled: initialDistance,
      legBurnedDistance: _seedBurned,
      animating: initialDistance < totalDistance,  // Only animate if there's more to travel
      _launchDelayRemaining: launchDelay,
      cumulativeDistances,
      totalDistance,
      // Per-waypoint pause schedule. Pauses are stored in mission minutes so the
      // playback-speed slider scales launch delays, pauses, and route travel together.
      _waypointDistances,
      _wpPauseIdx: 0,
      _pauseRemaining: airportDeparturePauseSeconds(did) / 60,
      // Refuel point distance for real-time leg progress display
      refuelDistance,
    };

    // TEMP TEST: verify split utility
    const traj = (state.routes[did] && state.routes[did].trajectory) ? state.routes[did].trajectory : [];
    // Make sure trajectory is visible for animated drones
    state.trajectoryVisible[did] = true;
  });

  // Killed drones (no route to fly) sit in their red diamond.
  placeLostDrones();

  // Note: missionDistance is already set from missionReplay.getStartingDistance()
  // and will persist across the animation lifecycle

  updateTrajectoryButtonStates();
  updateAnimationButtonStates();

  // Check if any drones were added for animation
  const activeDrones = Object.keys(state.animation.drones)
    .filter((did) => state.animation.drones[did]._lostAtCutDistance === undefined);
  if (activeDrones.length === 0) {
    appendDebugLine("No valid drones to animate (all routes too short). Transitioning to CHECKPOINT for re-planning.");
    state.animation.active = false;

    // Transition to CHECKPOINT mode so user can edit or try again
    // This prevents getting stuck in READY_TO_ANIMATE with nothing to animate
    setMissionMode(MissionMode.CHECKPOINT, "no routes to animate - ready for edit/replan");
    alert("No flight route is available to animate. Check the selected drones and their start/end airports. A route such as A1 → A1 with no targets has no distance to fly. For an airport-to-airport mission, choose different start and end airports, then Run Planner and Accept again.");
    return;
  }

  // Start animation loop
  const frameClock = createAnimationFrameClock();
  state.animation.frameClock = frameClock;
  let replayBoundary = null;

  function animate(currentTime) {
    // Check if animation was stopped/paused externally
    if (!state.animation.active) {
      return;
    }

    let missionSeconds = frameClock.takeMissionSeconds(currentTime);
    let anyAnimating = false;

    // CHECKPOINT PAUSE: Check if we're paused at a checkpoint
    // During pause, don't advance distance but still render (drone sits at diamond)
    if (state.animation.checkpointPause?.active) {
      if (Date.now() >= state.animation.checkpointPause.resumeTime) {
        // Pause duration complete - resume animation
        appendDebugLine(`▶️ Checkpoint pause complete, resuming animation`);
        state.animation.checkpointPause.active = false;
        state.animation.checkpointPause = null;
        // Resume from the displayed diamond. A delayed callback must not charge
        // time spent in the intentional replay presentation hold as flight.
        missionSeconds = 0;
      } else {
        // Still paused - redraw but don't advance
        drawEnvironment(true);
        state.animation.animationId = requestAnimationFrame(animate);
        return;
      }
    }

    // Each saved cut contains a separate position for each drone. Derive its
    // threshold from that drone's playback timeline, including its own loiters.
    // Wait for every participating drone instead of comparing their different
    // distance/fuel counters to one geometric mission-distance threshold.
    const nextReplaySegment = state.animation.continuousReplay
      ? null : missionReplay.getNextSegment();
    if (replayBoundary?.segmentIndex !== nextReplaySegment?.index) {
      replayBoundary = null;
      const cutPositions = nextReplaySegment?.cutPosition || nextReplaySegment?.cutPositions;
      if (cutPositions && Object.keys(cutPositions).length > 0) {
        const distances = {};
        Object.entries(state.animation.drones).forEach(([did, drone]) => {
          if (!cutPositions[did] || drone._lostAtCutDistance !== undefined) return;
          const routeInfo = state.routes[did];
          if (!routeInfo) return;
          anchorPlaybackCheckpoint(routeInfo, drone, cutPositions[did]);
          const trajectory = routeInfo.trajectory || [];
          const distance = playbackDistanceAtPosition(trajectory,
            drone.cumulativeDistances || [], cutPositions[did], drone.distanceTraveled);
          if (distance !== null) distances[did] = distance;
        });
        if (Object.keys(distances).length > 0) {
          replayBoundary = { segmentIndex: nextReplaySegment.index, distances };
        }
      }
    }

    // Advance each drone's distance based on its individual speed.
    // §8.8: distanceTraveled is SEGMENT-RELATIVE (0 at the diamond). Track the max
    // segment-relative distance; the global cumulative missionDistance is rebuilt
    // below as segmentStartOffset + max (so segment-boundary checks stay cumulative).
    const missionStepMinutes = advanceMissionClockSeconds(missionSeconds, replayBoundary?.distances);
    const _segOffset = state.animation.segmentStartOffset || 0;
    let maxDroneDistance = Math.max(0, state.animation.missionDistance - _segOffset);

    Object.entries(state.animation.drones).forEach(([did, droneState]) => {
      if (!droneState.animating) return;
      if (droneState.distanceTraveled >= droneState.totalDistance) return;
      const checkpointDistance = replayBoundary?.distances[did];
      if (checkpointDistance !== undefined &&
          droneState.distanceTraveled >= checkpointDistance - 1e-9) {
        anyAnimating = true; // wait at the cut without consuming time/fuel
        return;
      }

      advanceDroneMissionTime(did, droneState, missionStepMinutes, checkpointDistance);

      // Track max for segment boundary checks
      if (droneState.distanceTraveled > maxDroneDistance) {
        maxDroneDistance = droneState.distanceTraveled;
      }
    });

    // Update global missionDistance (CUMULATIVE) = segment offset + fastest drone's
    // segment-relative distance — keeps checkSegmentBoundary / next-cut in the
    // cumulative frame while drone position stays segment-relative (§8.8).
    state.animation.missionDistance = _segOffset + maxDroneDistance;

    // LOST DRONES CHECK: Find the cut distance where each drone is lost and stop them there
    // This must happen BEFORE we sync distanceTraveled, so drones stop at their cut markers.
    // Everything this block reads (missionReplay segments, state.routes, state.animation)
    // is populated for a manually-created/manually-cut mission too, not just an imported
    // one — gating on segmentedImport.isActive() meant this whole stop-at-cut mechanism
    // silently never ran for a manual mission: a killed drone's full original route (loaded
    // fresh from segment 0 on replay) just played out to the end airport unimpeded, since
    // nothing else in the per-frame loop stops a drone short of its own route's natural end.
    if (missionReplay.getSegmentCount() > 1) {
      const currentMissionDist = state.animation.missionDistance;

      Object.entries(state.animation.drones).forEach(([did, droneState]) => {
        if (!droneState.animating) return;
        if (droneState._lostAtCutDistance !== undefined) return; // Already handled

        // Check each segment to find where this drone is lost
        const segCount = missionReplay.getSegmentCount();
        for (let segIdx = 1; segIdx < segCount; segIdx++) {
          const seg = missionReplay.getSegment(segIdx);
          if (!seg || !seg.lostDrones) continue;

          // Is this drone lost at this segment?
          if (seg.lostDrones.includes(did)) {
            const cutDistance = seg.cutDistance;
            if (!replayBoundary && cutDistance && currentMissionDist >= cutDistance) {
              // Drone should stop at this cut distance
              droneState._lostAtCutDistance = cutDistance;
              droneState.distanceTraveled = cutDistance;
              droneState.animating = false;

              // TRUNCATE TRAJECTORY and get split point for lost drone position
              const routeInfo = state.routes[did];
              if (routeInfo && routeInfo.trajectory && routeInfo.trajectory.length > 0) {
                const fullTraj = routeInfo.trajectory;
                // split_polyline_at_distance returns { prefixPoints, suffixPoints, splitPoint, ... }
                const splitResult = split_polyline_at_distance(fullTraj, cutDistance);
                const truncated = splitResult?.prefixPoints;
                if (truncated && truncated.length > 0) {
                  routeInfo.trajectory = truncated;
                }
                // Save the cut position for drawing (so drone circle matches the marker)
                // Priority: seg.cutPosition/cutPositions if available, else use calculated split point
                const segCutPosForLost = seg.cutPosition || seg.cutPositions;
                if (segCutPosForLost && segCutPosForLost[did]) {
                  droneState._lostAtPosition = [...segCutPosForLost[did]];
                } else if (splitResult?.splitPoint) {
                  droneState._lostAtPosition = [...splitResult.splitPoint];
                } else if (truncated && truncated.length > 0) {
                  // Fallback: use end of truncated trajectory
                  droneState._lostAtPosition = [...truncated[truncated.length - 1]];
                }
              } else {
                // No trajectory - check cutPosition/cutPositions
                const segCutPosForLostNoTraj = seg.cutPosition || seg.cutPositions;
                if (segCutPosForLostNoTraj && segCutPosForLostNoTraj[did]) {
                  droneState._lostAtPosition = [...segCutPosForLostNoTraj[did]];
                }
              }
              break;
            }
          }
        }
      });
    }

    Object.entries(state.animation.drones).forEach(([did, droneState]) => {
      if (!droneState.animating) return;

      // Distance already advanced above based on per-drone speed

      // Clamp and stop if THIS drone's trajectory is finished
      // (other drones may continue if their trajectories are longer)
      if (droneState.distanceTraveled >= droneState.totalDistance) {
        // Do not charge the final animation-frame overshoot as fuel.
        const destinationOvershoot = Math.max(
          0,
          droneState.distanceTraveled - droneState.totalDistance,
        );
        if (destinationOvershoot > 0) {
          droneState.legBurnedDistance = Math.max(
            0,
            droneState.legBurnedDistance - destinationOvershoot,
          );
          state.legFuel[did] = droneState.legBurnedDistance;
        }
        droneState.distanceTraveled = droneState.totalDistance;
        droneState.animating = false;
      } else {
        anyAnimating = true;
      }

      // 3) derive progress (for existing drawing/UI code)
      droneState.progress = (droneState.totalDistance > 0)
        ? (droneState.distanceTraveled / droneState.totalDistance)
        : 1;

      // 4) Check if drone's current position matches any unvisited target
      // Calculate drone position from trajectory using distance traveled
      const trajectory = state.routes[did]?.trajectory || [];
      const cumulativeDistances = droneState.cumulativeDistances || [];

      if (trajectory.length < 2 || cumulativeDistances.length !== trajectory.length) return;

      // Find current position based on distance traveled (same logic as drawDrone)
      const targetDistance = droneState.distanceTraveled;
      let segmentIdx = 0;
      for (let i = 1; i < cumulativeDistances.length; i++) {
        if (cumulativeDistances[i] >= targetDistance) {
          segmentIdx = i - 1;
          break;
        }
        segmentIdx = i - 1;
      }

      const fromPoint = trajectory[segmentIdx];
      const toPoint = trajectory[segmentIdx + 1] || fromPoint;
      const segmentStartDist = cumulativeDistances[segmentIdx];
      const segmentEndDist = cumulativeDistances[segmentIdx + 1] || segmentStartDist;
      const segmentLength = segmentEndDist - segmentStartDist;

      const segmentProgress = segmentLength > 0
        ? (targetDistance - segmentStartDist) / segmentLength
        : 0;

      // droneX is lat, droneY is lng (trajectory is [lat, lng])
      const [droneLat, droneLng] = interpolateFlightPosition(fromPoint, toPoint, segmentProgress);

      // Visit detection: a target is visited if EITHER the drone REACHED its
      // vertex on the canonical distance frame (isTargetVisited, §9) OR the drone
      // passed within the per-type detection_radius (when set > 0). Detection
      // radius EXTENDS the vertex rule, it does not replace it.
      droneState._currentLat = droneLat;
      droneState._currentLng = droneLng;
      const droneConfig = state.droneConfigs[did] || {};
      const detectionRadiusConfig = droneConfig.detection_radius || {};

      // Get targets in this drone's route for the current segment (optimization)
      // This avoids checking all targets in the environment
      const currentSegIdx = missionReplay._currentSegmentIndex || 0;
      const currentSeg = missionReplay.getSegment(currentSegIdx);
      const routeTargetIds = new Set();

      // Collect target IDs from the current segment's route for this drone
      // IMPORTANT: Use state.routes FIRST as it has the most current routes after checkpoint replans
      const segRoute = state.routes[did]?.route || currentSeg?.solution?.routes?.[did]?.route || [];
      // NOTE: Do NOT use getRouteTargetLabels() here - it checks trajectory proximity, not route membership,
      // which can incorrectly mark targets visited by the wrong drone if trajectories pass nearby.
      for (const wp of segRoute) {
        if (wp && String(wp).startsWith('T')) {
          routeTargetIds.add(wp);
        }
      }

      // Check targets in this drone's route
      // Snapshot ∪ live env ∪ segments: the snapshot alone misses targets added
      // after it was taken (e.g. after a C-key cut), which then never got a green
      // cross and were re-offered to the solver on the next cut.
      const allTargets = getAllKnownTargets();
      // Full-mission replay flies the whole stitched path, but state.routes[did].route
      // only carries the LAST segment's waypoints. In replay the drone's targets are
      // those on its route in ANY segment — not every target: checking all of them
      // credited a target to whichever drone first passed within its radius (D3
      // claimed D1's target; the per-drone counts were wrong, the total right).
      if (state.animation.continuousReplay) {
        for (const seg of missionReplay.getSegments() || []) {
          const r = seg?.solution?.routes?.[did]?.route || seg?.routes?.[did]?.route || [];
          for (const wp of r) {
            if (wp && /^T\d+$/i.test(String(wp))) routeTargetIds.add(String(wp));
          }
        }
      }
      for (const target of allTargets) {
        if (!target.id) continue;
        if (!routeTargetIds.has(target.id)) continue;  // Only this drone's targets
        if (state.visitedTargets.includes(target.id)) continue;  // Already visited (stays checked until Reset)

        // Detection radius (per target type): a positive radius lets the drone
        // "detect" the target by passing within it. radius 0 = pure §9 vertex.
        const targetType = (target.type || 'a').toLowerCase();
        const detectionRadius = detectionRadiusConfig[targetType] || 0;
        // VISIT = the drone REACHED the target's (relaxed) vertex on the distance
        // frame. With a detection radius, the taut-string relaxes the waypoint up
        // to r off the target center, so we pass the radius as the hit tolerance:
        // isTargetVisited matches the closest trajectory vertex within r and fires
        // the instant the drone reaches its cumulative distance (deterministic —
        // not a per-frame proximity sample that can skip the tangent, which is why
        // radius targets were missing their green cross).
        const reachedVertex = isTargetVisited(
          target, trajectory, cumulativeDistances, droneState.distanceTraveled,
          targetHitToleranceKm(target, did)
        );
        // Secondary: live per-frame proximity (catches a target the drone happens
        // to pass close to even if not its own routed waypoint).
        const withinRadius =
          detectionRadius > 0 &&
          haversineKm(droneLat, droneLng, target.lat, target.lng) <= detectionRadius * 1.01;

        if (reachedVertex || withinRadius) {
          markTargetVisitedByDrone(target.id, did);
          const note = withinRadius && !reachedVertex
            ? ` (detection radius: ${detectionRadius}km)` : '';
          appendDebugLine(`🎯 D${did} visited ${target.id}${note}`);
        }
      }
    });

    // MISSION REPLAY: Check segment boundary using single mission distance
    // This is simple: when missionDistance >= nextSegment.cutDistance, switch segments
    let switched = false;
    let newSegment = null;

    // Segment boundary check — SKIPPED during a full-mission replay. In that mode
    // we fly the seg-0 full trajectory start→end as one continuous flight, so the
    // old cut boundaries must NOT trigger a segment switch (that left the drone
    // stuck at the first cut diamond while post-cut targets still got marked).
    if (!state.animation.continuousReplay) {
      try {
        const nextSeg = missionReplay.getNextSegment();
        const nextCutDist = nextSeg?.getCutDistance?.() || nextSeg?.cutDistance || null;
        // DEBUG: Log segment check every 10 units of travel
        const lastSegCheckLog = state.animation._lastSegCheckLog || 0;
        if (state.animation.missionDistance - lastSegCheckLog > 10) {
          appendDebugLine(`[SEG CHECK] missionDist=${state.animation.missionDistance.toFixed(1)}, nextCutDist=${nextCutDist?.toFixed(1) || 'null'}, currentSeg=${missionReplay._currentSegmentIndex}, anyAnimating=${anyAnimating}`);
          state.animation._lastSegCheckLog = state.animation.missionDistance;
        }

        const checkpointReached = replayBoundary
          ? Object.entries(replayBoundary.distances).every(([did, distance]) =>
            state.animation.drones[did].distanceTraveled >= distance - 1e-9) : null;
        const result = missionReplay.checkSegmentBoundary(
          state.animation.missionDistance, checkpointReached);
        switched = result.switched;
        newSegment = result.newSegment;
      } catch (err) {
        console.error('[SEG CHECK ERROR]', err);
        appendDebugLine(`[SEG CHECK ERROR] ${err.message}`);
      }
    }

    if (switched && newSegment) {
      appendDebugLine(`🔄 === SEGMENT SWITCH to ${newSegment.index} (via MissionReplay) ===`);
      // DEBUG: Print segment table at switch to diagnose cutDistance issues
      appendDebugLine(missionReplay.printSegmentTable());

      // CHECKPOINT PAUSE: Show white diamond and pause for 1 second at checkpoint
      // This gives the user visual feedback of the segment transition
      // Try newSegment cutPosition (V2) or cutPositions (legacy) first, then fall back to segmentedImport
      let cutPositions = newSegment.cutPosition || newSegment.cutPositions || {};
      if (Object.keys(cutPositions).length === 0 && segmentedImport.isActive()) {
        // For pre-imported missions, get cutPositions from segmentedImport
        cutPositions = segmentedImport.getCutPositionForSegment(newSegment.index) || {};
        // The segment itself has no cut position, so collectCheckpointMarkers
        // can't draw this diamond from it: show it as a pending cut instead.
        // (When the segment has one, its own C<n> diamond is already drawn —
        // a pending copy stacked a second, wrongly numbered diamond on it.)
        if (Object.keys(cutPositions).length > 0) {
          state.pendingCutPositions = JSON.parse(JSON.stringify(cutPositions));
          state.pendingLostDrones = newSegment.lostDrones || [];
        }
      }
      if (Object.keys(cutPositions).length > 0) {

        // Start checkpoint pause (1 second = 1000ms)
        state.animation.checkpointPause = {
          active: true,
          resumeTime: Date.now() + 1000,
          segmentIndex: newSegment.index
        };
        appendDebugLine(`⏸️ Checkpoint pause at C${newSegment.index} - drone sits at diamond for 1 second`);
      }

      // Also sync missionState for backward compatibility
      missionState.currentSegmentIndex = newSegment.index;

      // SEGMENTED IMPORT: Update environment and targets using SegmentedImportManager
      if (segmentedImport.isActive()) {
        // Sync SegmentedImportManager's current segment index
        segmentedImport._currentSegmentIndex = newSegment.index;

        // Get environment for display (cumulative targets from segments 0 to current)
        state.env = segmentedImport.getEnvForDisplay();

        // Load drone_configs from the SEGMENT (the source of truth)
        if (newSegment.drone_configs) {
          state.droneConfigs = JSON.parse(JSON.stringify(newSegment.drone_configs));
          state.env.drone_configs = state.droneConfigs;
          refreshDroneConfigUI();  // Update the UI table
        }

        // CRITICAL FIX: Update visited targets from segmented import data
        // This was missing - causing targets not to be marked as visited after checkpoint
        const visitedFromSegment = segmentedImport.getVisitedTargetsForSegment(newSegment.index);
        if (visitedFromSegment && visitedFromSegment.length > 0) {
          // Merge with existing (some may already be marked during animation).
          seedVisitedFromSegment(visitedFromSegment);
          appendDebugLine(`🎯 Visited targets from segment data: [${visitedFromSegment.join(',')}]`);
        }

      } else {
        // Reveal the saved environment, including targets not assigned a route.
        state.env = getCreatedReplayEnv(newSegment.index);

        // Load drone_configs from the SEGMENT (the source of truth)
        if (newSegment.drone_configs) {
          state.droneConfigs = JSON.parse(JSON.stringify(newSegment.drone_configs));
          state.env.drone_configs = state.droneConfigs;
          refreshDroneConfigUI();  // Update the UI table
        }
      }

      // SEGMENTED IMPORT: Extend trajectories for ACTIVE drones only
      // Check lostDrones to identify which drones should stop animating
      if (segmentedImport.isActive()) {
        // Collect all lost drones from previous segments
        // Try multiple sources: segmentedImport, missionReplay, newSegment
        const allLostDrones = new Set();
        for (let i = 1; i <= newSegment.index; i++) {
          // First try segmentedImport (SegmentManager)
          const segFromImport = segmentedImport.getSegment?.(i);
          if (segFromImport?.lostDrones) {
            segFromImport.lostDrones.forEach(did => allLostDrones.add(did));
          }
          // Fallback to missionReplay
          const segFromReplay = missionReplay.getSegment(i);
          if (segFromReplay?.lostDrones) {
            segFromReplay.lostDrones.forEach(did => allLostDrones.add(did));
          }
        }

        // CRITICAL FIX: Apply lost drone state to state.droneConfigs and refresh UI
        // The segment's drone_configs should already have this, but enforce it here too
        if (allLostDrones.size > 0) {
          allLostDrones.forEach(did => {
            if (state.droneConfigs?.[did]) {
              state.droneConfigs[did].enabled = false;
            }
          });
          refreshDroneConfigUI();  // Update the UI table with lost drones
        }

        // Build combined trajectories from segment 0 to current segment
        // This is the SAME as creation workflow - progressive reveal
        appendDebugLine(`🔄 Segment switch: Building combined routes for seg 0-${newSegment.index}...`);
        const combinedRoutes = buildCombinedRoutesFromSegments(newSegment.index);

        // Log trajectory changes with distances
        const oldLens = Object.entries(state.routes).map(([did, rd]) => `D${did}:${rd.trajectory?.length || 0}`).join(', ');
        const newInfo = Object.entries(combinedRoutes).map(([did, rd]) => {
          const traj = rd.trajectory || [];
          let dist = 0;
          for (let j = 1; j < traj.length; j++) {
            const dx = traj[j][0] - traj[j-1][0];
            const dy = traj[j][1] - traj[j-1][1];
            dist += Math.sqrt(dx*dx + dy*dy);
          }
          return `D${did}:${traj.length}pts/${dist.toFixed(1)}dist`;
        }).join(', ');
        appendDebugLine(`  Old trajectories: [${oldLens}]`);
        appendDebugLine(`  New trajectories: [${newInfo}]`);

        Object.entries(combinedRoutes).forEach(([did, routeData]) => {
          const isLostDrone = allLostDrones.has(did);
          let trajectory = routeData.trajectory || [];

          if (trajectory.length > 0) {
            // Ensure state.routes[did] exists
            if (!state.routes[did]) {
              state.routes[did] = {};
            }
            state.routes[did].trajectory = JSON.parse(JSON.stringify(trajectory));
            state.routes[did].route = JSON.parse(JSON.stringify(routeData.route || []));
            // The trajectory is now the stitched one: drop the first segment's
            // playback frame, or the 3D view keeps drawing (and climbing along)
            // the original route from it.
            delete state.routes[did].anim_frame;
            // Carry the REFUEL METADATA through the segment-switch rebuild. Without
            // this, state.routes[did] kept only trajectory+route, so the animation's
            // refuel-crossing detector (reads state.routes[did].refuel_stops ~12072)
            // saw an empty list and the drone flew THROUGH the tanker without
            // refueling — no refuel fired, the ⛽ leg stats stayed empty, and the cut
            // masqueraded as the refuel. These fields are already on routeData
            // (combinedRoutes carries them via the ...routeData spread).
            if (routeData.refuel_stops !== undefined)
              state.routes[did].refuel_stops = JSON.parse(JSON.stringify(routeData.refuel_stops));
            if (routeData.refuel_indices !== undefined)
              state.routes[did].refuel_indices = JSON.parse(JSON.stringify(routeData.refuel_indices));
            if (routeData.leg_distances !== undefined)
              state.routes[did].leg_distances = JSON.parse(JSON.stringify(routeData.leg_distances));
            if (routeData.refuel_airport !== undefined)
              state.routes[did].refuel_airport = routeData.refuel_airport;

            // Update cumulative distances for the combined trajectory
            const droneState = state.animation.drones[did];
            if (droneState) {
              // §10: single SAM-aware builder (matrix frame) on segment switch.
              const _acdSw = computeAnimationCumDist(trajectory, routeData.route, state.distanceMatrix, {
                waypointIndices: routeData.waypoint_indices,
                refuelAirport: routeData.refuel_airport,
              });
              const loiterFrame = addLoiterIntervals(trajectory, _acdSw.cumDist,
                routeData.replay_loiter_specs || []);
              trajectory = loiterFrame.trajectory;
              state.routes[did].trajectory = trajectory;
              _acdSw.cumDist = loiterFrame.cumDist;
              _acdSw.total = loiterFrame.total;
              droneState.cumulativeDistances = _acdSw.cumDist;
              droneState.totalDistance = _acdSw.total;
              // The rebuilt route has a new cumulative frame. Convert the saved
              // cut position into that frame; changing frames consumes no fuel.
              const cutDistanceInFrame = playbackDistanceAtPosition(trajectory,
                _acdSw.cumDist, cutPositions[did]);
              if (cutDistanceInFrame !== null) droneState.distanceTraveled = cutDistanceInFrame;

              // REBUILD the airport-departure pause schedule for the NEW segment's
              // route. It was computed ONCE at startAnimation from segment 0's full
              // uncut route (…TK1@67.7…), and the switch left it stale — so on the
              // post-cut segment the drone paused at the OLD TK1@67.7 (a phantom refuel
              // past the cut) and flew through the real tanker. Recompute from the
              // combined route + the new trajectory/cumulativeDistances so pauses land
              // at THIS segment's actual airport touches.
              droneState._waypointDistances = buildWaypointDistances(
                routeData.route || [], trajectory, _acdSw.cumDist
              );
              if (Array.isArray(routeData.replay_refuel_events)) {
                droneState._waypointDistances = routeData.replay_refuel_events
                  .map(event => ({ ...event, dist: loiterFrame.shiftDistance(event.dist) }))
                  .filter((event) => event.dist > droneState.distanceTraveled + 0.05)
                  .map((event) => ({ ...event }));
              }
              // Reset the pause cursor so the schedule is walked from the drone's
              // CURRENT distance (past pauses are skipped by the < prevDist check).
              droneState._wpPauseIdx = 0;
              const _wpDbg = droneState._waypointDistances.map(w => `${w.wp}@${w.dist.toFixed(1)}`).join(", ");
              appendDebugLine(`    D${did}: rebuilt pause schedule for new segment: [${_wpDbg}]`);

              appendDebugLine(`    D${did}: distTraveled=${droneState.distanceTraveled.toFixed(1)}, newTotalDist=${_acdSw.total.toFixed(1)}, trajLen=${trajectory.length}, isLost=${isLostDrone}`);

              // Lost drones: keep stopped and set position for drawing
              if (isLostDrone) {
                droneState.animating = false;
                state.trajectoryVisible[did] = true;

                // Find the segment where this drone was lost and get its cut position
                // This is needed so the drone icon draws inside the red diamond
                for (let segIdx = 1; segIdx <= newSegment.index; segIdx++) {
                  const seg = missionReplay.getSegment(segIdx) || segmentedImport.getSegment?.(segIdx);
                  if (seg?.lostDrones?.includes(did)) {
                    const segCutPos = seg.cutPosition || seg.cutPositions;
                    if (segCutPos?.[did]) {
                      droneState._lostAtPosition = [...segCutPos[did]];
                      droneState._lostAtCutDistance = seg.cutDistance || droneState.distanceTraveled;
                      appendDebugLine(`    D${did}: LOST - position set to [${segCutPos[did][0].toFixed(4)}, ${segCutPos[did][1].toFixed(4)}]`);
                    } else if (trajectory.length > 0) {
                      // Fallback: use end of truncated trajectory
                      const lastPt = trajectory[trajectory.length - 1];
                      droneState._lostAtPosition = [...lastPt];
                      droneState._lostAtCutDistance = droneState.distanceTraveled;
                      appendDebugLine(`    D${did}: LOST - position set to trajectory end`);
                    }
                    break;
                  }
                }
                appendDebugLine(`    D${did}: LOST - stopped`);
              } else if (droneState.distanceTraveled < droneState.totalDistance) {
                // Active drone with more to fly: re-enable animation.
                // (Was the stale outer `totalDistance` — the last drone's init value;
                // f3ace4a fixed the twin at ~12478 but left this segmented-import
                // branch reading the wrong total, so a drone whose combined total
                // differs from that stale value re-enabled/froze incorrectly.)
                droneState.animating = true;
                appendDebugLine(`    D${did}: Continuing animation`);
              } else {
                droneState.animating = false;
                state.trajectoryVisible[did] = true;
                appendDebugLine(`    D${did}: distTraveled >= totalDist - stopped`);
              }
            }
          }
        });

        // Handle lost drones that weren't in combinedRoutes (empty trajectory case)
        // These drones need their position set from the segment's cutPosition
        allLostDrones.forEach(did => {
          const droneState = state.animation.drones[did];
          if (droneState && droneState._lostAtPosition === undefined) {
            // Find the segment where this drone was lost
            for (let segIdx = 1; segIdx <= newSegment.index; segIdx++) {
              const seg = missionReplay.getSegment(segIdx) || segmentedImport.getSegment?.(segIdx);
              if (seg?.lostDrones?.includes(did)) {
                const segCutPos = seg.cutPosition || seg.cutPositions;
                if (segCutPos?.[did]) {
                  droneState._lostAtPosition = [...segCutPos[did]];
                  droneState._lostAtCutDistance = seg.cutDistance || 0;
                  droneState.animating = false;
                  state.trajectoryVisible[did] = true;
                  appendDebugLine(`    D${did}: LOST (no trajectory) - position set from cutPosition [${segCutPos[did][0].toFixed(4)}, ${segCutPos[did][1].toFixed(4)}]`);
                }
                break;
              }
            }
          }
        });
      } else {
        // LEGACY/CHECKPOINT: Build combined trajectories up to current segment only
        // This ensures progressive reveal - only show trajectories for segments reached so far

        // STEP 1: Apply lost drones FIRST (before applying routes)
        // Collect all lost drones from segments up to and including newSegment
        const allLostDrones = new Set();
        for (let i = 0; i <= newSegment.index; i++) {
          const seg = missionReplay.getSegment(i);
          if (seg?.lostDrones) {
            seg.lostDrones.forEach(did => allLostDrones.add(String(did)));
          }
        }
        // Apply lost drone state
        allLostDrones.forEach(did => {
          // Stop animation
          if (state.animation.drones[did]) {
            state.animation.drones[did].animating = false;
          }
          // Disable in config
          if (state.droneConfigs?.[did]) {
            state.droneConfigs[did].enabled = false;
          }
          // Hide trajectory (or keep visible but grayed - your choice)
          state.trajectoryVisible[did] = true; // Keep visible to show where it stopped
          appendDebugLine(`🔴 D${did}: Lost drone disabled at segment switch`);
        });

        // STEP 2: Update visited_targets from stored segment data (deterministic)
        // Union of visited_targets from all segments played so far
        const storedVisited = new Set();
        for (let i = 0; i <= newSegment.index; i++) {
          const seg = missionReplay.getSegment(i);
          if (seg?.visited_targets) {
            seg.visited_targets.forEach(t => storedVisited.add(t));
          }
        }
        if (storedVisited.size > 0) {
          // Merge with existing (some may already be marked during animation).
          seedVisitedFromSegment([...storedVisited]);
          appendDebugLine(`🎯 Visited targets from stored data: ${[...storedVisited].join(',')}`);
        }

        // STEP 3: Build combined routes (frozen + new segment)
        const combinedRoutes = buildCombinedRoutesFromSegments(newSegment.index);

        Object.entries(combinedRoutes).forEach(([did, routeData]) => {
          const isLostDrone = allLostDrones.has(did);
          let trajectory = routeData.trajectory || [];
          if (trajectory.length > 0) {
            // Ensure state.routes[did] exists (critical for lost drones that may have been cleared)
            if (!state.routes[did]) {
              state.routes[did] = {};
            }
            state.routes[did].trajectory = JSON.parse(JSON.stringify(trajectory));
            state.routes[did].route = JSON.parse(JSON.stringify(routeData.route || []));
            // The trajectory is now the stitched one: drop the first segment's
            // playback frame, or the 3D view keeps drawing (and climbing along)
            // the original route from it.
            delete state.routes[did].anim_frame;
            // Carry refuel metadata through (twin of the primary segment-switch rebuild
            // above) so the animation's refuel-crossing detection fires at the tanker
            // instead of the drone flying through it.
            if (routeData.refuel_stops !== undefined)
              state.routes[did].refuel_stops = JSON.parse(JSON.stringify(routeData.refuel_stops));
            if (routeData.refuel_indices !== undefined)
              state.routes[did].refuel_indices = JSON.parse(JSON.stringify(routeData.refuel_indices));
            if (routeData.leg_distances !== undefined)
              state.routes[did].leg_distances = JSON.parse(JSON.stringify(routeData.leg_distances));
            if (routeData.refuel_airport !== undefined)
              state.routes[did].refuel_airport = routeData.refuel_airport;

            // Update drone's cumulative distances and total distance for the new trajectory
            const droneState = state.animation.drones[did];
            if (droneState) {
              // §10: single SAM-aware builder (matrix frame) on segment switch.
              const _acdSw2 = computeAnimationCumDist(trajectory, routeData.route, state.distanceMatrix, {
                waypointIndices: routeData.waypoint_indices,
                refuelAirport: routeData.refuel_airport,
              });
              const loiterFrame = addLoiterIntervals(trajectory, _acdSw2.cumDist,
                routeData.replay_loiter_specs || []);
              trajectory = loiterFrame.trajectory;
              state.routes[did].trajectory = trajectory;
              _acdSw2.cumDist = loiterFrame.cumDist;
              _acdSw2.total = loiterFrame.total;
              droneState.cumulativeDistances = _acdSw2.cumDist;
              droneState.totalDistance = _acdSw2.total;
              const cutDistanceInFrame = playbackDistanceAtPosition(trajectory,
                _acdSw2.cumDist, cutPositions[did]);
              if (cutDistanceInFrame !== null) droneState.distanceTraveled = cutDistanceInFrame;

              // Rebuild the airport-departure pause schedule for the new segment (twin
              // of the primary switch site) so post-cut pauses land at this segment's
              // airports, not the stale seg-0 TK1@67.7 phantom.
              droneState._waypointDistances = buildWaypointDistances(
                routeData.route || [], trajectory, _acdSw2.cumDist
              );
              if (Array.isArray(routeData.replay_refuel_events)) {
                droneState._waypointDistances = routeData.replay_refuel_events
                  .map(event => ({ ...event, dist: loiterFrame.shiftDistance(event.dist) }))
                  .filter((event) => event.dist > droneState.distanceTraveled + 0.05)
                  .map((event) => ({ ...event }));
              }
              droneState._wpPauseIdx = 0;

              // Lost drones: keep stopped, don't re-enable animation
              if (isLostDrone) {
                droneState.animating = false;
                state.trajectoryVisible[did] = true;
              } else if (droneState.distanceTraveled < droneState.totalDistance) {
                // Active drone with more to fly: re-enable animation.
                // Compare against the NEW combined total (droneState.totalDistance),
                // NOT the stale outer seg-0 totalDistance.
                droneState.animating = true;
              } else {
                // Drone is frozen/completed - ensure it stays stopped but visible
                droneState.animating = false;
                state.trajectoryVisible[did] = true;
              }
            }
          }
        });
      }

      appendDebugLine(`🔄 === SEGMENT SWITCH COMPLETE ===`);

      // Clear debug flags so targets get re-evaluated with new trajectory
      Object.entries(state.animation.drones).forEach(([, ds]) => {
        // Clear _debugDist_ flags so we log distances for targets in new segment
        Object.keys(ds).filter(k => k.startsWith('_debug')).forEach(k => delete ds[k]);
      });

      // IMPORTANT: After segment switch, immediately mark any targets that should
      // have been passed based on current distanceTraveled. This catches targets
      // whose cumulative distance is <= distanceTraveled but weren't marked due
      // to the segment boundary.
      //
      // CRITICAL FIX: Only check targets from PREVIOUS segments (already in initialEnvSnapshot),
      // NOT newly added targets from the current segment. Newly added targets should only be
      // marked as the drone actually reaches them during continued animation.
      Object.entries(state.animation.drones).forEach(([did, droneState]) => {
        const routeInfo = state.routes[did];
        if (!routeInfo || !routeInfo.route || !routeInfo.trajectory) return;

        const trajectory = routeInfo.trajectory;
        const route = routeInfo.route;
        const cumulativeDistances = droneState.cumulativeDistances || [];
        const currentDistance = droneState.distanceTraveled;

        // Build set of targets that were in THIS drone's route in PREVIOUS segments
        // AND are still in this drone's route in the CURRENT segment
        // This prevents marking targets that were:
        // 1. Newly added to this drone (moved from another drone)
        // 2. Removed from this drone (reassigned to another drone)
        const prevSegTargetsForDrone = new Set();
        for (let segIdx = 0; segIdx < newSegment.index; segIdx++) {
          const prevSeg = missionReplay.getSegment(segIdx);
          const prevRoute = prevSeg?.solution?.routes?.[did]?.route || [];
          prevRoute.forEach(wp => {
            if (String(wp).startsWith("T")) {
              prevSegTargetsForDrone.add(wp);
            }
          });
        }

        // Get targets in the CURRENT segment's route for this drone
        const currentSegTargetsForDrone = new Set();
        const currentSegRoute = newSegment?.solution?.routes?.[did]?.route || [];
        currentSegRoute.forEach(wp => {
          if (String(wp).startsWith("T")) {
            currentSegTargetsForDrone.add(wp);
          }
        });

        route.forEach((wp, wpIdx) => {
          if (!String(wp).startsWith("T")) return;
          if (state.visitedTargets.includes(wp)) return;

          // CHECK 1: Target must be in the CURRENT segment's route for this drone
          // If a target was removed from this drone's route (reassigned to another drone),
          // this drone should NOT mark it - even if this drone's old trajectory passed near it
          if (!currentSegTargetsForDrone.has(wp)) {
            return; // Skip - target no longer assigned to this drone
          }

          // CHECK 2: Only mark targets that were in previous segments for this drone
          // If a target was just added to this drone's route (e.g., moved from D1 to D2),
          // it should NOT be marked at segment switch - only when drone actually reaches it
          if (!prevSegTargetsForDrone.has(wp)) {
            return; // Skip - this target is newly assigned to this drone
          }

          // Sequential order check: only mark if all previous targets visited
          // This prevents newly added targets from being marked prematurely
          let canMark = true;
          for (let i = 0; i < wpIdx; i++) {
            const prevWp = route[i];
            if (String(prevWp).startsWith("T") && !state.visitedTargets.includes(prevWp)) {
              canMark = false;
              break;
            }
          }
          if (!canMark) return;

          // Find target position - check initialEnvSnapshot first (has all targets), then env, then segments
          let target = state.initialEnvSnapshot?.targets?.find(t => t.id === wp);
          if (!target) {
            target = state.env.targets?.find(t => t.id === wp);
          }
          if (!target) {
            for (let segIdx = 0; segIdx < missionReplay.getSegmentCount(); segIdx++) {
              const seg = missionReplay.getSegment(segIdx);
              const segTarget = seg?.env?.targets?.find(t => t.id === wp);
              if (segTarget) {
                target = segTarget;
                break;
              }
            }
          }
          if (!target) return;

          // §9: shared vertex-based visited check (segment switch).
          if (isTargetVisited(target, trajectory, cumulativeDistances, currentDistance, targetHitToleranceKm(target, did))) {
            markTargetVisitedByDrone(wp, did);
            appendDebugLine(`🎯 D${did} visited ${wp} (at segment switch)`);
          }
        });
      });

      // The 3D view is only rebuilt on non-animation redraws: show it the new
      // routes, targets and cut diamonds now (camera left where it is).
      if (typeof cesiumView !== "undefined" && cesiumView.isActive()) {
        cesiumView.render({ keepCamera: true });
      }

      // AUTO-CONTINUE through checkpoints with 1 second pause.
      // The checkpointPause mechanism (set at line ~9962) handles the pause:
      // it keeps requestAnimationFrame running but skips distance advancement
      // until resumeTime is reached. No need to stop/restart animation here.
      drawEnvironment(true);  // fromAnimationLoop = true
      updateStatsFromAnimation();
    }

    drawEnvironment(true);  // fromAnimationLoop = true

    // Update stats with real-time distance traveled
    updateStatsFromAnimation();

    // Check if there are more segments to switch to (pending checkpoints).
    // During a full-mission REPLAY we deliberately ignore the cut segments (one
    // continuous flight), so there are effectively NO more segments — otherwise the
    // end-of-run lands in the "hasMoreSegments" branch and never sets
    // missionComplete, breaking the SECOND replay.
    const hasMoreSegments =
      !state.animation.continuousReplay && missionReplay.getNextSegment() !== null;

    // DEBUG: Log when all drones stop animating
    if (!anyAnimating && state.animation._wasAnimating) {
      appendDebugLine(`[ANIM END] All drones stopped. hasMoreSegments=${hasMoreSegments}, missionDist=${state.animation.missionDistance.toFixed(1)}`);
    }
    state.animation._wasAnimating = anyAnimating;

    if (anyAnimating && state.animation.active) {
      state.animation.animationId = requestAnimationFrame(animate);
    } else if (!state.animation.active) {
      // Stopped externally (pause/cut) - don't change mode
      appendDebugLine(`[ANIM] Stopped externally`);
      return;
    } else if (hasMoreSegments) {
      // No drones animating, but there are more segments pending
      // This can happen if a drone finishes early but other drones need segment switches
      // Keep the animation loop running to process segment switches
      state.animation.animationId = requestAnimationFrame(animate);
    } else {
      appendDebugLine(`[ANIM] Completing - no more segments`);
      state.animation.active = false;
      // Mission ran to its natural end (drone at the final airport, no more
      // segments). Mark complete so the NEXT Animate replays the WHOLE mission
      // start→end (not just the last segment). Cleared when a replay starts or
      // the mission is cut/reset.
      state.animation.missionComplete = true;
      state.animation.continuousReplay = false;  // this run is over
      // Animation completed naturally - go to READY_TO_ANIMATE (unless the operator
      // is mid live-edit: then the edit keeps the mode and Done replans).
      if (!finishedDuringLiveEdit()) {
        setMissionMode(MissionMode.READY_TO_ANIMATE, "animation complete");
      }
      updateAnimationButtonStates();
      // Final stats update when animation completes
      updateStatsFromAnimation();
    }
  }

  state.animation.animationId = requestAnimationFrame(animate);

  // Transition to ANIMATING state
  setMissionMode(MissionMode.ANIMATING, "animation started");

  // Replay initialization can replace routes and rewind the visible cuts.
  // The animation loop only moves 3D drone markers, so rebuild the scene now
  // instead of leaving the previous mission's routes on screen until a cut.
  if (typeof cesiumView !== "undefined" && cesiumView.isActive()) {
    cesiumView.render({ keepCamera: true });
  }

  appendDebugLine(`Started animation for: ${droneIds.map((d) => `D${d}`).join(", ")}`);
}

// The drones reached the end of their routes while the operator was live-editing
// (Edit pressed while flying). The animation stops, but the MODE belongs to the
// operator's edit: switching to READY_TO_ANIMATE here used to pull them out of the
// editor, so Done re-entered a plain edit, dropped the staged changes and never
// replanned. Staying in EDITING_ENV lets Done take the live-edit path (cut →
// Run Planner → Accept) from where the drones stopped.
function finishedDuringLiveEdit() {
  if (missionState.mode !== MissionMode.EDITING_ENV || !missionState.wasAnimating) return false;
  appendDebugLine("✈️ Drones reached the end of their routes during the live edit — press Done to replan from where they are.");
  return true;
}

function stopAnimation() {
  const wasAnimating = state.animation.active;

  if (state.animation.animationId) {
    cancelAnimationFrame(state.animation.animationId);
    state.animation.animationId = null;
  }

  state.animation.active = false;
  // DON'T clear drones - keep their current positions visible
  // state.animation.drones = {};  // Removed - drones should stay visible
  missionState.pauseContext = null;

  // Mark all animating drones as stopped (not animating)
  Object.values(state.animation.drones).forEach(droneState => {
    droneState.animating = false;
  });

  // Stopping a running OR paused animation ends that playback: back to
  // READY_TO_ANIMATE (the pause context was dropped above).
  if ((wasAnimating && missionState.mode === MissionMode.ANIMATING) ||
      missionState.mode === MissionMode.PAUSED_MID_ANIMATION) {
    setMissionMode(MissionMode.READY_TO_ANIMATE, "animation stopped");
  }

  updateAnimationButtonStates();
  drawEnvironment();
  appendDebugLine("Animation stopped. Drones remain at current positions.");
}

/**
 * Pause animation - save state for resuming
 */
/**
 * Capture the mission's freeze-frame for the AGENT: where each drone is right now
 * (the cut point), how far it has flown, the leg fuel burned, and what's been visited.
 * The agent's continue/replan tool uses this to cut at the current position and replan
 * from there — the same data acceptEditsWithCheckpoint uses for a manual C-key cut.
 * Returns null when not mid-animation (nothing to continue from).
 */
function pauseAnimation() {
  const perms = getUiPermissions();
  if (!perms.canPause) {
    appendDebugLine("Cannot pause in current state");
    return;
  }

  // Save the current animation state for resume
  missionState.pauseContext = {
    droneStates: JSON.parse(JSON.stringify(state.animation.drones)),
    animatingDrones: Object.keys(state.animation.drones).filter(
      did => state.animation.drones[did].animating
    ),
  };

  // Stop the animation frame loop but keep drone positions
  if (state.animation.animationId) {
    cancelAnimationFrame(state.animation.animationId);
    state.animation.animationId = null;
  }
  state.animation.active = false;

  // Transition to PAUSED state
  setMissionMode(MissionMode.PAUSED_MID_ANIMATION, "animation paused");

  updateAnimationButtonStates();
  drawEnvironment();
  appendDebugLine("Animation paused. Click Animate to resume.");
}

/**
 * Resume animation from pause state
 * Uses full animation loop with target marking (same as startAnimation)
 */
function resumeAnimation() {
  if (typeof window !== "undefined" && window.weatherPreview?.allowAnimation?.() === false) return;
  if (!missionState.pauseContext) {
    appendDebugLine("No pause context to resume from");
    return;
  }

  // Restore animation state
  state.animation.drones = missionState.pauseContext.droneStates;
  state.animation.active = true;

  // Re-enable animating flag for drones that were animating
  missionState.pauseContext.animatingDrones.forEach(did => {
    if (state.animation.drones[did]) {
      state.animation.drones[did].animating = true;
    }
  });

  // Clear pause context
  missionState.pauseContext = null;

  // Restart the time origin so paused wall time is never charged to the flight.
  const frameClock = createAnimationFrameClock();
  state.animation.frameClock = frameClock;

  function animate(currentTime) {
    if (!state.animation.active) return;

    const missionSeconds = frameClock.takeMissionSeconds(currentTime);
    let anyAnimating = false;
    const missionStepMinutes = advanceMissionClockSeconds(missionSeconds);

    Object.entries(state.animation.drones).forEach(([did, droneState]) => {
      if (!droneState.animating) return;
      if (droneState.distanceTraveled >= droneState.totalDistance) return;

      advanceDroneMissionTime(did, droneState, missionStepMinutes);

      if (droneState.distanceTraveled >= droneState.totalDistance) {
        // Do not charge the final animation-frame overshoot as fuel.
        const destinationOvershoot = Math.max(
          0,
          droneState.distanceTraveled - droneState.totalDistance,
        );
        if (destinationOvershoot > 0) {
          droneState.legBurnedDistance = Math.max(
            0,
            droneState.legBurnedDistance - destinationOvershoot,
          );
          state.legFuel[did] = droneState.legBurnedDistance;
        }
        droneState.distanceTraveled = droneState.totalDistance;
        droneState.animating = false;
      } else {
        anyAnimating = true;
      }

      droneState.progress = (droneState.totalDistance > 0)
        ? (droneState.distanceTraveled / droneState.totalDistance)
        : 1;

      // TARGET MARKING: Check if drone has passed any target waypoints
      const routeInfo = state.routes[did];
      if (routeInfo && routeInfo.route && routeInfo.trajectory) {
        const trajectory = routeInfo.trajectory;
        const route = routeInfo.route;
        const cumulativeDistances = droneState.cumulativeDistances || [];
        const currentDistance = droneState.distanceTraveled;

        route.forEach((wp) => {
          if (!String(wp).startsWith("T")) return;
          if (state.visitedTargets.includes(wp)) return;

          // Find target position - check initialEnvSnapshot first (has all targets), then env, then segments
          let target = state.initialEnvSnapshot?.targets?.find(t => t.id === wp);
          if (!target) {
            target = state.env.targets?.find(t => t.id === wp);
          }
          if (!target) {
            for (let segIdx = 0; segIdx < missionReplay.getSegmentCount(); segIdx++) {
              const seg = missionReplay.getSegment(segIdx);
              const segTarget = seg?.env?.targets?.find(t => t.id === wp);
              if (segTarget) {
                target = segTarget;
                break;
              }
            }
          }
          if (!target) return;

          // §9: shared vertex-based visited check (resume).
          if (isTargetVisited(target, trajectory, cumulativeDistances, currentDistance, targetHitToleranceKm(target, did))) {
            markTargetVisitedByDrone(wp, did);
            appendDebugLine(`🎯 D${did} visited ${wp}`);
          }
        });
      }
    });

    drawEnvironment(true);  // fromAnimationLoop = true
    updateStatsFromAnimation();

    if (anyAnimating && state.animation.active) {
      state.animation.animationId = requestAnimationFrame(animate);
    } else if (!state.animation.active) {
      return;  // Stopped externally
    } else {
      state.animation.active = false;
      if (!finishedDuringLiveEdit()) {
        setMissionMode(MissionMode.READY_TO_ANIMATE, "animation complete");
      }
      updateAnimationButtonStates();
      updateStatsFromAnimation();
    }
  }

  state.animation.animationId = requestAnimationFrame(animate);

  // Transition to ANIMATING state
  setMissionMode(MissionMode.ANIMATING, "animation resumed");

  updateAnimationButtonStates();
  appendDebugLine("Animation resumed with target marking.");
}

/**
 * Cut/Checkpoint - freeze current position and truncate future
 */
async function cutAtCheckpoint() {
  const perms = getUiPermissions();
  if (!perms.canCut) {
    appendDebugLine("Cannot cut in current state");
    return;
  }

  appendDebugLine(`cutAtCheckpoint called. animation.drones keys: ${Object.keys(state.animation.drones || {}).join(", ") || "NONE"}`);
  appendDebugLine(`animation.active: ${state.animation.active}`);

  // Use the existing freezeAtCheckpoint logic
  await freezeAtCheckpoint();

  // Verify checkpoint was set up
  if (!state.checkpoint?.active) {
    appendDebugLine("⚠️ WARNING: freezeAtCheckpoint did not set checkpoint.active! Check drone data.");
  } else {
    appendDebugLine(`✅ Checkpoint set with ${Object.keys(state.checkpoint.segments || {}).length} drone segments`);
  }

  // Debug: show splitPoints for all drones at cut time
  appendDebugLine(
    `CUT DEBUG: checkpoint splitPoints = ` +
    Object.entries(state.checkpoint?.segments || {})
      .map(([did, s]) => `D${did}:${s?.splitPoint ? `[${s.splitPoint.map(v=>v.toFixed(1)).join(",")}]` : "null"}`)
      .join(" ")
  );

  // Note: pendingCutPositions is now populated inside freezeAtCheckpoint()
  // Log what was captured for debugging
  if (state.pendingCutPositions && Object.keys(state.pendingCutPositions).length > 0) {
    for (const [did, pos] of Object.entries(state.pendingCutPositions)) {
      appendDebugLine(`📍 Cut pos D${did} at [${pos[0].toFixed(2)}, ${pos[1].toFixed(2)}]`);
    }
    // Set legacy single cut position from first drone (backward compatibility)
    const firstDid = Object.keys(state.pendingCutPositions)[0];
    if (firstDid) {
      const p = state.pendingCutPositions[firstDid];
      state.pendingCutPosition = [p[0], p[1]];
    }
    appendDebugLine(`📍 Cut positions captured for drones: [${Object.keys(state.pendingCutPositions).join(", ")}]`);
  } else {
    appendDebugLine("⚠️ No cut positions captured - pendingCutPositions is empty");
  }

  // Transition to CHECKPOINT state - allow solving immediately
  // (Bug fix: don't set checkpointSource = "replay_cut" which blocks solving)
  missionState.checkpointSource = null;
  setMissionMode(MissionMode.CHECKPOINT, "cut at checkpoint");

  // Assign segment numbers to newly visited targets
  const newlyAssignedTargets = [];
  for (const targetId of state.visitedTargets) {
    // Only assign if not already assigned in a previous cut
    if (!state.previouslyAssignedTargets.includes(targetId)) {
      state.targetSegmentMap[targetId] = state.currentCutSegment;
      newlyAssignedTargets.push(targetId);
    }
  }

  // Log segment assignment
  if (newlyAssignedTargets.length > 0) {
    appendDebugLine(`🏷️ Segment ${state.currentCutSegment}: Assigned targets [${newlyAssignedTargets.join(", ")}]`);
  }

  // Update previously assigned targets and increment segment counter for next cut
  state.previouslyAssignedTargets = [...state.visitedTargets];
  state.currentCutSegment++;

  // Always redraw to show visited targets with green X and segment badges
  drawEnvironment();

  // Debug: Log visited targets
  appendDebugLine(`📍 Visited targets after cut: [${state.visitedTargets.join(", ")}]`);
  appendDebugLine(`Cut at checkpoint (Segment ${state.currentCutSegment - 1} complete). Edit environment and Run Planner to continue.`);
}

function updateAnimationButtonStates() {
  for (let did = 1; did <= 5; did++) {
    const row = $(`drone-row-d${did}`);
    if (row) {
      const droneState = state.animation.drones[String(did)];
      row.classList.toggle("flying", Boolean(droneState && droneState.animating));
    }
  }

}

// Fly the drones ticked under Anim in the Env tab's drone table (Mission
// Control's Animate). All ticked: startAnimation() picks them itself once it
// knows which routes it is playing (a replay swaps in the original routes).
function animateTickedDrones() {
  const animDrones = dronesWithTrajectories();
  const ticked = animDrones.filter((did) => $(`anim-d${did}`)?.checked !== false);

  appendDebugLine(`Animate: routes=[${animDrones.join(",")}] ticked=[${ticked.join(",")}]`);

  if (animDrones.length === 0) {
    appendDebugLine("No routes to animate. Run planner first.");
  } else if (ticked.length === animDrones.length) {
    startAnimation();
  } else if (ticked.length > 0) {
    startAnimation(ticked);
  } else {
    appendDebugLine("No drone ticked under Anim (Env tab).");
  }
}

// ----------------------------------------------------
// Boot
// ----------------------------------------------------
window.addEventListener("load", () => {
  resizeCanvasToContainer();
  window.addEventListener("resize", resizeCanvasToContainer);

  attachCanvasEditing();
  attachZoomPanHandlers();
  attachConfigListeners();
  loadAssetCatalog();
  attachSequenceBar();
  attachIOHandlers();
  attachDroneTable();
  attachTargetCategories();
  attachOptimizationHandlers();
  attachEditToggle();
  attachTabSwitching();
  attachTrajectoryControls();
  attachMapControls();

  const btnRun = $("btn-run-planner");
  if (btnRun) btnRun.addEventListener("click", runPlanner);

  const btnCancel = $("btn-cancel-solve");
  if (btnCancel) btnCancel.addEventListener("click", cancelSolve);

  // Add buttons (respect editMode)
  const btnAddA = $("btn-add-airport");
  const btnAddT = $("btn-add-target");
  const btnAddS = $("btn-add-sam");
  const btnDel  = $("btn-delete-selected");

  if (btnAddA) {
    btnAddA.setAttribute("aria-pressed", "false");
    btnAddA.addEventListener("click", () => {
      if (!state.editMode) return;
      setAddMode(state.addMode === "airport" ? null : "airport");
    });
  }
  const btnAddTk = $("btn-add-tanker");
  if (btnAddTk) {
    btnAddTk.setAttribute("aria-pressed", "false");
    btnAddTk.addEventListener("click", () => {
      if (!state.editMode) return;
      setAddMode(state.addMode === "tanker" ? null : "tanker");
    });
  }
  if (btnAddT) {
    btnAddT.setAttribute("aria-pressed", "false");
    btnAddT.addEventListener("click", () => {
      if (!state.editMode) return;
      setAddMode(state.addMode === "target" ? null : "target");
    });
  }
  if (btnAddS) {
    btnAddS.setAttribute("aria-pressed", "false");
    btnAddS.addEventListener("click", () => {
      if (!state.editMode) return;
      setAddMode(state.addMode === "sam" ? null : "sam");
    });
  }
  if (btnDel) {
    btnDel.addEventListener("click", () => {
      if (!state.editMode) return;
      setAddMode(null, { preserveSelection: true });
      deleteSelected();
    });
  }

  // Status banner action buttons
  const btnAcceptEdits = $("btn-accept-edits");
  if (btnAcceptEdits) {
    btnAcceptEdits.addEventListener("click", acceptEdits);
  }

  const btnAcceptSolution = $("btn-accept-solution");
  if (btnAcceptSolution) {
    btnAcceptSolution.addEventListener("click", acceptSolution);
  }

  const btnDiscardDraft = $("btn-discard-draft");
  if (btnDiscardDraft) {
    btnDiscardDraft.addEventListener("click", discardDraftSolution);
  }

  // Mission Control Panel handlers
  attachMissionControlHandlers();

  // Agent Send button
  attachAgentHandlers();

  // Initialize status banner
  updateStatusBanner();
  updateButtonStates();

  // Initialize map view from geolocation/localStorage, then load environment
  initializeMapView().then(() => {
    initialLoadEnv();
    // Initialize Mission Control state
    updateMissionControlState();
  });
});

// ----------------------------------------------------
// Mission Control Panel
// ----------------------------------------------------
function attachMissionControlHandlers() {
  const mcRunPlanner = $("mc-run-planner");
  const mcAccept = $("mc-accept");
  const mcDiscard = $("mc-discard");
  const mcAnimate = $("mc-animate");
  const mcStop = $("mc-stop");
  const mcReset = $("mc-reset");

  // Set up segment switch callback to update UI when segment changes during animation
  missionReplay.onSegmentSwitch(() => {
    updateMissionControlState();
    syncSequencesFromRoutes();
  });

  // Step 1 — Plan
  if (mcRunPlanner) {
    mcRunPlanner.addEventListener("click", () => {
      try {
        if (getUiPermissions().canSolve) {
          runPlanner();
        } else if (missionState.mode === MissionMode.EDITING_ENV) {
          appendDebugLine("⚠️ Turn Edit off (your changes are kept) before running the planner");
        } else if (missionState.mode === MissionMode.READY_TO_ANIMATE) {
          appendDebugLine("⚠️ Solution already accepted. Click 'Reset' to start over, or 'Animate' to play.");
        } else {
          appendDebugLine(`⚠️ Cannot run planner in mode: ${missionState.mode}`);
        }
      } catch (err) {
        appendDebugLine(`Run Planner error: ${err.message}`);
      }
    });
  }

  // Step 2 — Review: Accept the draft, or Discard it (or the edits in progress)
  if (mcAccept) {
    mcAccept.addEventListener("click", () => {
      try {
        const perms = getUiPermissions();
        if (perms.canAcceptSolution ||
            (missionState.mode === MissionMode.DRAFT_READY && missionState.draftSolution)) {
          acceptSolution();
        }
      } catch (err) {
        appendDebugLine(`Accept button error: ${err.message}`);
      }
    });
  }
  if (mcDiscard) {
    mcDiscard.addEventListener("click", () => {
      const perms = getUiPermissions();
      if (perms.isEditing) {
        cancelEdits();
      } else if (perms.canDiscardDraft) {
        discardDraftSolution();
      }
    });
  }

  // Step 3 — Fly: Animate / Pause / Resume, Stop, Reset
  if (mcAnimate) {
    mcAnimate.addEventListener("click", () => {
      const perms = getUiPermissions();
      if (perms.canPause) {
        pauseAnimation();
      } else if (perms.canResume) {
        resumeAnimation();
      } else if (perms.canAnimate) {
        animateTickedDrones();
      }
    });
  }
  if (mcStop) mcStop.addEventListener("click", stopAnimation);
  if (mcReset) {
    mcReset.addEventListener("click", () => {
      resetMission();
    });
  }

  // Mission lifecycle controls (§0.6)
  const mcMissionName = $("mc-mission-name");
  if (mcMissionName) {
    // Capture the officer-declared name; it rides along on the next solve and
    // is also pushed immediately if a mission already exists.
    mcMissionName.addEventListener("change", () => {
      state.missionName = mcMissionName.value.trim() || null;
    });
  }
  const mcEndMission = $("mc-end-mission");
  if (mcEndMission) {
    mcEndMission.addEventListener("click", () => {
      endCurrentMission();
    });
  }
  syncMissionUi();

  // Animation speed slider and optional boost share one effective mission clock.
  const mcSpeedSlider = $("mc-speed-slider");
  const mcSpeedLabel = $("mc-speed-label");
  const mcSpeedBoost = $("mc-speed-boost");
  const mcSpeedBoostFactor = $("mc-speed-boost-factor");
  if (mcSpeedSlider) {
    const updateAnimationSpeed = () => {
      const baseSpeed = ANIMATION_SPEEDS[Number(mcSpeedSlider.value)] ?? 1;
      const boostFactor = Number(mcSpeedBoostFactor?.value) === 10 ? 10 : 100;
      const speed = baseSpeed * (mcSpeedBoost?.checked ? boostFactor : 1);
      if (state.animation.active) state.animation.frameClock?.changeSpeed(speed, performance.now());
      state.animation.speedMultiplier = speed;
      if (mcSpeedLabel) {
        mcSpeedLabel.textContent = speed < 1 ? `${speed === 0.25 ? "¼" : "½"}x` : `${speed}x`;
      }
    };
    mcSpeedSlider.addEventListener("input", updateAnimationSpeed);
    mcSpeedBoost?.addEventListener("change", updateAnimationSpeed);
    mcSpeedBoostFactor?.addEventListener("change", updateAnimationSpeed);
    updateAnimationSpeed();
  }
}

/**
 * Update Mission Control panel button states and status based on current mode
 */
function updateMissionControlState() {
  const perms = getUiPermissions();
  const mode = missionState.mode;

  const mcRunPlanner = $("mc-run-planner");
  const mcAccept = $("mc-accept");
  const mcDiscard = $("mc-discard");
  const mcAnimate = $("mc-animate");
  const mcStop = $("mc-stop");
  const mcReset = $("mc-reset");
  const mcStatusText = $("mc-status-text");
  const mcSegmentInfo = $("mc-segment-info");
  const mcPanel = $("mission-control");

  // Step 1 — Plan. After a cut the planner replans from the cut.
  if (mcRunPlanner) {
    mcRunPlanner.disabled = !perms.canSolve;
    mcRunPlanner.textContent = mode === MissionMode.CHECKPOINT ? "Replan from cut" : "Run Planner";
  }

  // Step 2 — Review
  const draftReady = perms.canAcceptSolution || mode === MissionMode.DRAFT_READY;
  if (mcAccept) mcAccept.disabled = !draftReady;
  if (mcDiscard) {
    mcDiscard.disabled = !perms.canDiscardDraft && !perms.isEditing;
    mcDiscard.textContent = perms.isEditing ? "Discard edits" : "Discard";
  }

  // Step 3 — Fly: Animate / Pause / Resume toggle, Stop, Reset
  if (mcAnimate) {
    let label = "▶ Animate", playing = false;
    if (perms.canPause) { label = "⏸ Pause"; playing = true; }
    else if (perms.canResume) label = "▶ Resume";
    mcAnimate.disabled = !(perms.canPause || perms.canResume || perms.canAnimate);
    mcAnimate.textContent = label;
    mcAnimate.classList.toggle("mc-btn-pause", playing);
    mcAnimate.classList.toggle("mc-btn-play", !playing);
  }
  if (mcStop) mcStop.disabled = !(state.animation.active || perms.canResume);
  if (mcReset) mcReset.disabled = !perms.canReset;

  // Which step is current: its box is outlined, earlier steps show as done.
  const step = {
    [MissionMode.DRAFT_READY]: 2,
    [MissionMode.READY_TO_ANIMATE]: 3,
    [MissionMode.ANIMATING]: 3,
    [MissionMode.PAUSED_MID_ANIMATION]: 3,
  }[mode] || 1;
  ["plan", "review", "fly"].forEach((name, i) => {
    const el = $(`mc-step-${name}`);
    if (!el) return;
    el.classList.toggle("now", i + 1 === step);
    el.classList.toggle("done", i + 1 < step);
  });

  // Status line: what to do next.
  if (mcStatusText) {
    let statusText = "";
    switch (mode) {
      case MissionMode.IDLE:
        statusText = state.env && (state.env.targets || []).length
          ? "Environment loaded. Press Run Planner."
          : "Load an environment (Env tab → Import…) or draw one with Edit.";
        break;
      case MissionMode.EDITING_ENV:
        statusText = missionState.wasAnimating
          ? "Live editing (the animation continues). Turn Edit off to keep your changes."
          : "Editing. Turn Edit off to keep your changes, or Discard edits.";
        break;
      case MissionMode.DRAFT_READY:
        statusText = "Plan ready. Accept it to fly, or Discard and change something.";
        break;
      case MissionMode.READY_TO_ANIMATE:
        statusText = "Accepted. Press Animate.";
        break;
      case MissionMode.ANIMATING:
        statusText = "Flying… press C to cut here and replan.";
        break;
      case MissionMode.PAUSED_MID_ANIMATION:
        statusText = "Paused. Resume, or Stop.";
        break;
      case MissionMode.CHECKPOINT:
        statusText = "Stopped at the cut. Edit if needed, then Replan from cut.";
        break;
      default:
        statusText = `Mode: ${mode}`;
    }
    mcStatusText.textContent = statusText;
  }

  // Update segment info
  if (mcSegmentInfo) {
    const segmentCount = missionReplay.getSegmentCount();
    const currentSegmentIdx = missionReplay.getCurrentSegmentIndex();
    if (segmentCount > 0) {
      mcSegmentInfo.textContent = `Segment ${currentSegmentIdx + 1}/${segmentCount}`;
    } else {
      mcSegmentInfo.textContent = "Segment 1";
    }
  }

  // Update panel state class for visual feedback
  if (mcPanel) {
    mcPanel.classList.remove("mc-state-animating", "mc-state-checkpoint", "mc-state-draft");
    if (mode === MissionMode.ANIMATING) {
      mcPanel.classList.add("mc-state-animating");
    } else if (mode === MissionMode.CHECKPOINT) {
      mcPanel.classList.add("mc-state-checkpoint");
    } else if (mode === MissionMode.DRAFT_READY) {
      mcPanel.classList.add("mc-state-draft");
    }
  }

  updateTimeSyncUi();
}

// ----------------------------------------------------
// Voice (speech-to-text mic + text-to-speech replies)
// ----------------------------------------------------
function speakRepliesEnabled() {
  return voiceControls.speakRepliesEnabled();
}

function wireReplyVoice(qaBlock, replyText) {
  return voiceControls.wireReplyVoice(qaBlock, replyText);
}

function attachVoiceHandlers() {
  return voiceControls.attachVoiceHandlers();
}

function attachTtsEngineSelector() {
  return voiceControls.attachTtsEngineSelector();
}

// ----------------------------------------------------
// Agent Communication
// ----------------------------------------------------
function attachAgentHandlers() {
  const btnSend = $("btn-send-agent");
  const inputEl = $("agent-input");

  if (!btnSend || !inputEl) return;

  // Send on button click
  btnSend.addEventListener("click", () => sendAgentMessage());

  // Send on Enter (Shift+Enter for new line)
  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendAgentMessage();
    }
  });

  // Voice input/output handlers
  attachVoiceHandlers();

  // Memory management handlers
  attachMemoryHandlers();

  // Trace tab handlers
  attachTraceHandlers();

  // Load memories on init
  loadAgentMemories();
}

// ----------------------------------------------------
// Agent Memory Management
// ----------------------------------------------------
function attachMemoryHandlers() {
  const btnToggle = $("btn-toggle-memory");
  const btnClear = $("btn-clear-memory");
  const btnAdd = $("btn-add-memory");
  const memoryInput = $("memory-input");

  if (btnToggle) {
    btnToggle.addEventListener("click", () => {
      const panel = $("memory-panel");
      if (panel) {
        const isHidden = panel.style.display === "none";
        panel.style.display = isHidden ? "block" : "none";
        btnToggle.textContent = isHidden ? "Hide" : "Show";
      }
    });
  }

  if (btnClear) {
    btnClear.addEventListener("click", async () => {
      if (!confirm("Clear all agent memories?")) return;
      try {
        const resp = await fetch("/api/agent/memory", { method: "DELETE" });
        const data = await resp.json();
        if (data.success) {
          loadAgentMemories();
          appendDebugLine("Cleared all agent memories");
        }
      } catch (e) {
      }
    });
  }

  if (btnAdd && memoryInput) {
    btnAdd.addEventListener("click", () => addAgentMemory());
    memoryInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        addAgentMemory();
      }
    });
  }
}

function attachTraceHandlers() {
  const btnClearTrace = $("btn-clear-trace");
  if (btnClearTrace) {
    btnClearTrace.addEventListener("click", () => {
      if (!confirm("Clear all trace logs?")) return;
      state.agentTraceRuns = [];
      renderAgentsTraceTab();
      appendDebugLine("Cleared agent trace logs");
    });
  }
}

async function loadAgentMemories() {
  try {
    const resp = await fetch("/api/agent/memory");
    const data = await resp.json();
    if (data.success) {
      renderMemoryList(data.memories);
      const countEl = $("memory-count");
      if (countEl) countEl.textContent = data.count;
    }
  } catch (e) {
  }
}

function renderMemoryList(memories) {
  return memoryView.render(memories);
}

async function addAgentMemory() {
  const inputEl = $("memory-input");
  const categoryEl = $("memory-category");
  if (!inputEl || !categoryEl) return;

  const content = inputEl.value.trim();
  if (!content) return;

  const category = categoryEl.value;

  try {
    const resp = await fetch("/api/agent/memory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, category })
    });
    const data = await resp.json();
    if (data.success) {
      inputEl.value = "";
      loadAgentMemories();
      appendDebugLine(`Added memory: [${category}] ${content}`);
    }
  } catch (e) {
  }
}

async function deleteAgentMemory(memoryId) {
  try {
    const resp = await fetch(`/api/agent/memory/${memoryId}`, { method: "DELETE" });
    const data = await resp.json();
    if (data.success) {
      loadAgentMemories();
      appendDebugLine(`Deleted memory #${memoryId}`);
    }
  } catch (e) {
  }
}

async function sendAgentMessage() {
  const inputEl = $("agent-input");
  const chatHistory = $("agent-chat-history");
  const btnSend = $("btn-send-agent");

  if (!inputEl || !chatHistory) return;

  const message = inputEl.value.trim();
  if (!message) return;

  // Sending a new message interrupts any in-progress narration.
  if (window.ISRVoice && window.ISRVoice.tts) window.ISRVoice.tts.cancel();

  // Hard guard: must have an environment loaded
  if (!state.env) {
    appendDebugLine("⚠️ No environment loaded. Import a JSON first.");
    return;
  }

  // UI-permission guard: the Agent panel must follow the same mission state machine
  // as Mission Control to avoid stale env/solution mismatches.
  const perms = (typeof getUiPermissions === "function") ? getUiPermissions() : null;
  // AGENT-DRIVEN CONTINUE: when the mission is PAUSED (or live-editing from a pause),
  // the agent IS allowed — the operator is telling it to continue / giving an order,
  // and prepareAgentContinueCut cuts, then the agent replans. Bypass the animate/edit/solve
  // guards below in that case (they otherwise silently swallow the send).
  const _agentContinueOK = perms && perms.canAgentContinue;
  if (!_agentContinueOK) {
    if (perms && perms.isAnimating) {
      appendDebugLine("⚠️ Agent Solve disabled during animation. Pause or stop the animation first.");
      updateAgentUiState(perms);
      return;
    }
    if (perms && perms.isEditing) {
      appendDebugLine("⚠️ Agent Solve disabled during Edit mode. Accept or cancel edits first.");
      updateAgentUiState(perms);
      return;
    }
    if (perms && !perms.canSolve) {
      appendDebugLine("⚠️ Agent Solve disabled in the current mission state (paused/replaying/checkpoint-locked or solution already accepted).");
      updateAgentUiState(perms);
      return;
    }
  }

  // Create a Q&A block container
  const qaBlock = createQABlock(message);
  chatHistory.appendChild(qaBlock);
  inputEl.value = "";

  // Scroll to show the new block
  chatHistory.scrollTop = chatHistory.scrollHeight;

  // Disable button while processing
  if (btnSend) btnSend.disabled = true;

  // AGENT-DRIVEN CONTINUE: PAUSED (or live-editing from a pause) → the operator is telling
  // the agent to continue / giving a mid-mission order. Route to the cut-continue flow: it
  // creates the cut, solves from it via the agent, and shows a checkpoint draft with the
  // diamond + Accept. The frontend KNOWS the mission is paused (it owns the animation
  // state), so this is deterministic — the LLM never has to guess that this is a continue.
  // The cut is made with the same engine as Edit→Done; the solve below then builds
  // its request from the (now cut) mission state, exactly as Run Planner does.
  const isAgentContinue = !!_agentContinueOK;
  if (isAgentContinue) {
    try {
      await prepareAgentContinueCut();
    } catch (err) {
      const responseArea = qaBlock.querySelector(".qa-response");
      responseArea.classList.remove("thinking");
      responseArea.innerHTML = `Error: ${escapeHtml(err.message)}`;
      responseArea.classList.add("error");
      if (btnSend) btnSend.disabled = false;
      chatHistory.scrollTop = chatHistory.scrollHeight;
      return;
    }
  }

  try {
    // Debug: log what we're sending
    const targetCount = state.env?.targets?.length || 0;
    const targetIds = (state.env?.targets || []).map(t => t.id).join(", ");
    appendDebugLine(`🚀 Agent: ${targetCount} targets [${targetIds}]`);

    // Build existing solution context for agent memory
    // This allows follow-up requests like "move T5 to D1" to work
    const existingSolution = {};
    if (state.sequences && Object.keys(state.sequences).length > 0) {
      // Convert sequences like "A1,T1,T2,A1" to route arrays like ["A1","T1","T2","A1"]
      existingSolution.routes = {};
      existingSolution.allocation = {};
      for (const [did, seq] of Object.entries(state.sequences)) {
        if (seq) {
          const routeArr = seq.split(",").map(s => s.trim()).filter(s => s);
          existingSolution.routes[did] = routeArr;
          // Extract targets (non-airport waypoints) for allocation
          existingSolution.allocation[did] = routeArr.filter(wp => wp.startsWith("T"));
        }
      }
      appendDebugLine(`📦 Agent context: ${Object.keys(existingSolution.routes).length} drone routes`);
    }

    // Get LLM settings from localStorage
    const llmSettings = LLMSettings.getCurrentSettings();

    let response, data;
    // Same request as Run Planner for the current mission state (fresh, C-key cut,
    // live-edit cut, or re-solving a checkpoint draft). The agent only swaps the
    // solver. The chat always uses the reasoning agent: the older v4 chat path
    // bypassed this shared request/apply pipeline, so it was removed.
    const solveReq = buildSolveRequest();
    const revisionAtRequest = missionState.revision;

    // Use the provider/model selected in AI Settings; credentials stay on the server.
    // Longer timeout (15 minutes) since reasoning agent may iterate many times for phased missions
    const agentModelLabel = llmSettings
      ? `${llmSettings.provider} / ${llmSettings.model}` : "server default";
    appendDebugLine(`🧠 Using Reasoning Agent (${agentModelLabel})...`);
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 900000); // 15 minute timeout

    try {
      response = await fetch("/api/agents/reason", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_request: message,
          llm_config: llmSettings,
          env: solveReq.payload.env,
          drone_configs: solveReq.payload.drone_configs,
          // The cached matrix has no cut nodes; a checkpoint solve must build its own.
          distance_matrix: solveReq.isCheckpointReplan ? null : (state.distanceMatrix || null),
          time_sync: solveReq.payload.time_sync,
          mission_id: state.missionId || null,  // continue this mission's agent thread
          is_continue_from_cut: solveReq.isCheckpointReplan,
          visited_targets: solveReq.payload.visited_targets,
          repair_routes: solveReq.payload.repair_routes,
          current_routes: currentRoutesForAgent(solveReq),
        }),
        signal: controller.signal,
      });
      clearTimeout(timeoutId);
    } catch (err) {
      clearTimeout(timeoutId);
      if (err.name === 'AbortError') {
        throw new Error('Request timed out after 15 minutes. The agent may still be processing.');
      }
      throw err;
    }

    const reasoningData = await response.json();

    // Convert reasoning agent response to standard format
    data = {
      reply: reasoningData.response,
      reasoning: reasoningData.reasoning,
      iterations: reasoningData.iterations,
      success: reasoningData.success,
      routes: reasoningData.routes || null,  // Now includes routes from reasoning agent
      routes_changed: reasoningData.routes_changed,  // false on pure followup questions
      mission_id: reasoningData.mission_id || null,
      distance_matrix: reasoningData.distance_matrix || null,
      time_sync: reasoningData.time_sync || null,
      wrapped_polygons: reasoningData.wrapped_polygons || null,
      drone_configs: reasoningData.drone_configs || null,
    };

    // Log reasoning steps
    if (reasoningData.reasoning && reasoningData.reasoning.length > 0) {
      appendDebugLine(`🔧 Reasoning steps: ${reasoningData.iterations} iterations`);
      for (const step of reasoningData.reasoning.slice(-5)) {
        appendDebugLine(`   ${step.substring(0, 100)}...`);
      }
    }

    // Log routes if present
    if (reasoningData.routes && Object.keys(reasoningData.routes).length > 0) {
      appendDebugLine(`📦 Reasoning agent returned ${Object.keys(reasoningData.routes).length} routes`);
    }
    if (reasoningData.time_sync?.enabled) {
      const syncStatus = reasoningData.time_sync.status || "unknown";
      const syncTime = reasoningData.time_sync.sync_time;
      const syncStrategy = reasoningData.time_sync.wait_strategy === "hover_wait" ? "hover wait" : "launch delay";
      appendDebugLine(`⏱ Reasoning time sync ${syncStatus}${syncTime != null ? ` at t=${syncTime}` : ""} (${syncStrategy})`);
      (reasoningData.time_sync.warnings || []).forEach((warning) => appendDebugLine(`  ⚠️ ${warning}`));
    }

    // Store mission_id returned by backend, if any
    if (data.mission_id) {
      state.missionId = data.mission_id;
      appendDebugLine("Current mission_id: " + state.missionId);
    }

    // Store trace events for the Agents Trace tab
    if (data.trace_events && Array.isArray(data.trace_events)) {
      state.agentTraceRuns.push({
        ts: Date.now(),
        user_message: message,
        mission_id: data.mission_id || null,
        trace_events: data.trace_events,
        trace: data.trace || null,
      });
      renderAgentsTraceTab();
    }

    // Debug: log the response to see what we're getting
    appendDebugLine("Agent response keys: " + Object.keys(data).join(", "));
    if (data.routes) {
      appendDebugLine("Agent routes: " + JSON.stringify(data.routes));
    }

    // Update the Q&A block with the response
    const responseArea = qaBlock.querySelector(".qa-response");

    if (data.reply) {
      // 1) Escape model reply text
      let safeReply = escapeHtml(String(data.reply || ""));
      safeReply = safeReply.replace(/\n/g, "<br>");

      // 2) Append badges (controlled HTML)
      let badgesHtml = "";
      if (data.intent) {
        badgesHtml += ` <span class="agent-intent-badge">Intent: ${escapeHtml(String(data.intent))}</span>`;
      }

      if (Array.isArray(data.actions) && data.actions.length > 0) {
        const actionNames = data.actions.map(a => a?.type).filter(Boolean).join(", ");
        if (actionNames) badgesHtml += ` <span class="agent-actions-badge">Actions: ${escapeHtml(actionNames)}</span>`;
      }

      if (data.routes && Object.keys(data.routes).length > 0) {
        for (const [did, route] of Object.entries(data.routes)) {
          if (Array.isArray(route) && route.length > 0) {
            badgesHtml += ` <span class="agent-route-badge">D${escapeHtml(String(did))}: ${route.map(x => escapeHtml(String(x))).join(" → ")}</span>`;
          }
        }
      } else if (Array.isArray(data.route) && data.route.length > 0) {
        badgesHtml += ` <span class="agent-route-badge">Route: ${data.route.map(x => escapeHtml(String(x))).join(" → ")}</span>`;
      }

      responseArea.innerHTML = safeReply + (badgesHtml ? "<br><br>" + badgesHtml : "");
      responseArea.classList.remove("thinking");

      // Voice: wire up the per-message speaker button and optional auto-speak.
      wireReplyVoice(qaBlock, String(data.reply || ""));

      // Apply routes to UI — ONLY when this turn actually changed the solution.
      // A pure followup question ("why did D2 skip T14?") returns the unchanged
      // routes for context, with routes_changed=false; leave the canvas alone.
      const routesChanged = data.routes_changed !== false; // default true (legacy/solve)
      const staleAnswer = routesChanged && missionState.revision !== revisionAtRequest;
      if (staleAnswer) {
        // The mission changed while the agent was thinking (Run Planner, Discard,
        // Accept, an edit…). Applying this answer now would silently replace
        // what the operator did in the meantime.
        responseArea.innerHTML += "<br><br><em>⚠️ This plan arrived after the mission changed — it was not applied. Ask again if you still want it.</em>";
        appendDebugLine("⚠️ Agent answer arrived after the mission changed — not applied");
      } else if (routesChanged && data.routes && Object.keys(data.routes).length > 0) {
        // Check if routes are in reasoning agent format (objects with route/trajectory)
        // vs v4 format (simple arrays)
        const firstRoute = Object.values(data.routes)[0];
        if (firstRoute && typeof firstRoute === 'object' && !Array.isArray(firstRoute) && firstRoute.route) {
          // Reasoning-agent format: the SERVER route objects, already in the SAME
          // canonical shape Run Planner returns ({route, trajectory, distance,
          // points, leg_distances, refuel_indices, refuel_stops, fuel_budget,
          // waypoint_indices, ...}). Apply them FAITHFULLY through the same path
          // Run Planner uses — NO rebuild. The old transform→applyAgentRoutes
          // stripped + recomputed distance/points and dropped fuel_budget /
          // waypoint_indices / refuel data, so the agent's solution diverged from
          // Run Planner's and accept reverted to the lossy version.
          // The agent returns only the drones it changed; every other drone keeps
          // its current plan — exactly as with a sequence-bar edit.
          if (data.drone_configs) adoptAgentDroneConfigs(solveReq, data.drone_configs);
          const agentRoutes = { ...data.routes };
          Object.assign(agentRoutes, await currentPlanRoutes(solveReq, agentRoutes));
          await applySolveResult(
            agentResultForDraft({ ...data, routes: agentRoutes }), solveReq, { allowClientInsert: false }
          );
          // Reject on a paused-continue draft re-opens the editor (discardDraftSolution).
          if (isAgentContinue && missionState.draftSolution) {
            missionState.draftSolution._agentContinuation = true;
          }
        } else {
          // V4 format: routes are already arrays (legacy multi-agent path).
          applyAgentRoutes(data.routes, data.trajectories, data.points, data.fuel);
        }
      } else if (Array.isArray(data.route) && data.route.length > 0) {
        applyAgentRoute(data.route, data.points, data.fuel);
      } else if (solveReq?.restoredCutFromDraft) {
        // No new plan (e.g. a question) while a checkpoint draft is on screen: the
        // draft keeps the cut, so switch the live copy restored for the request off.
        state.checkpoint = { active: false, pct: 0.5, segments: {} };
      }
    } else {
      responseArea.innerHTML = "No response from agent.";
      responseArea.classList.add("error");
    }
  } catch (err) {
    const responseArea = qaBlock.querySelector(".qa-response");
    responseArea.innerHTML = `Error: ${escapeHtml(err.message)}`;
    responseArea.classList.add("error");
  } finally {
    if (btnSend) btnSend.disabled = false;
    chatHistory.scrollTop = chatHistory.scrollHeight;
  }
}

/**
 * AGENT-DRIVEN CONTINUE — the operator is PAUSED (or live-editing from a pause) and tells
 * the agent to continue / gives a mid-mission order ("continue", "D1 to A5", "abort all
 * land at A5"). Makes the cut with the same engine as Edit→Done; sendAgentMessage then
 * solves it through the shared buildSolveRequest → agent → applySolveResult path.
 */
async function prepareAgentContinueCut() {
  // Snapshot the staged edits BEFORE the cut engine clears them. A Reject restores
  // this so the editor re-opens with the operator's edits intact.
  missionState.agentRejectRestore = {
    stagedEnv: missionState.stagedEnv ? JSON.parse(JSON.stringify(missionState.stagedEnv)) : null,
    stagedDroneConfigs: missionState.stagedDroneConfigs ? JSON.parse(JSON.stringify(missionState.stagedDroneConfigs)) : null,
    wasLiveEditing: !!(missionState.stagedEnv || missionState.wasAnimating),
  };
  await prepareCutFromCurrentState();
}

/**
 * Reasoning-agent routes arrive in the solver's route shape but without the
 * solver's sequences/allocations; derive them so applySolveResult gets the same
 * data a /api/solve_with_allocation response carries.
 */
function agentResultForDraft(data) {
  const routes = data.routes || {};
  const sequences = {};
  const allocations = {};
  for (const [did, rd] of Object.entries(routes)) {
    const route = rd.route || [];
    sequences[did] = rd.sequence || route.join(",");
    allocations[did] = route.filter(wp => String(wp).startsWith("T"));
  }
  return {
    ...data,
    routes,
    sequences,
    allocations,
    wrapped_polygons: data.wrapped_polygons || state.wrappedPolygons || [],
    allocation_strategy: data.allocation_strategy || state.allocationStrategy || null,
  };
}

function createQABlock(question) {
  return chatView.createQABlock(question);
}

function appendChatMessage(content) {
  return chatView.appendChatMessage(content);
}

function removeChatMessage(msgId) {
  return chatView.removeChatMessage(msgId);
}

function escapeHtml(text) {
  return domHelpers.escapeHtml(text);
}

/**
 * Render the Agents Trace tab with all captured trace runs.
 */
function renderAgentsTraceTab() {
  return traceView.render(state.agentTraceRuns);
}

function applyAgentRoutes(routes, trajectories, totalPoints, totalFuel, refuelMetadata) {
  // Apply multi-drone routes from the agent
  // routes is like {"1": ["A1", "T3", "A1"], "2": ["A2", "T5", "A2"], ...}
  // refuelMetadata is like {"1": {refuel_airport, leg1_distance, leg2_distance}} (optional)
  // trajectories is like {"1": [[x1,y1], [x2,y2], ...], ...} - SAM-avoiding paths

  appendDebugLine(`🎯 applyAgentRoutes: ${Object.keys(routes || {}).length} routes`);

  if (!routes || Object.keys(routes).length === 0) {
    appendDebugLine("❌ applyAgentRoutes: No routes to apply");
    return;
  }

  // Build waypoint lookup for calculating distances and points
  const waypoints = {};
  const targetPriorities = {};

  if (state.env) {
    for (const a of (state.env.airports || [])) {
      const id = a.id || a.label || "A?";
      waypoints[id] = [a.lat, a.lng];
    }
    for (const t of (state.env.targets || [])) {
      const id = t.id || t.label || "T?";
      waypoints[id] = [t.lat, t.lng];
      targetPriorities[id] = t.priority || t.value || 5;
    }
  }

  // Build allocations for the allocation display
  const allocations = {};

  for (const [droneId, route] of Object.entries(routes)) {
    if (!route || route.length < 2) continue;

    const routeStr = route.join(",");

    // Store sequence
    state.sequences[droneId] = routeStr;

    // Extract targets for allocation display
    const routeTargets = route.filter(wp => String(wp).startsWith('T'));
    allocations[droneId] = routeTargets;

    // Determine the trajectory FIRST so we can compute SAM-aware distance from it.
    // Server-provided trajectory contains SAM-avoiding bend points; summing those
    // gives the truthful path length. Falling back to straight lines between
    // waypoint IDs would silently under-report distance when SAMs are present.
    let trajectory;
    if (trajectories && trajectories[droneId] && trajectories[droneId].length > 0) {
      trajectory = trajectories[droneId];
      appendDebugLine(`Agent route D${droneId}: using SAM-avoiding trajectory (${trajectory.length} points)`);
    } else {
      trajectory = buildTrajectoryFromRoute(route);
    }

    // Compute SAM-aware distance from the trajectory bend points (haversine
    // sum). This matches what the solver reports and what Run Planner uses,
    // so Agent and Run Planner now report the same number for the same path.
    let distance = 0;
    if (trajectory && trajectory.length > 1) {
      for (let i = 0; i < trajectory.length - 1; i++) {
        const a = trajectory[i];
        const b = trajectory[i + 1];
        if (a && b && a.length >= 2 && b.length >= 2) {
          distance += haversineKm(a[0], a[1], b[0], b[1]);
        }
      }
    } else {
      // No trajectory available: fall back to route-waypoint sum (Euclidean
      // is forbidden by policy, so use haversine but flag the underreport).
      for (let i = 0; i < route.length - 1; i++) {
        const from = waypoints[route[i]];
        const to = waypoints[route[i + 1]];
        if (from && to) {
          distance += _distance(from, to);
        }
      }
      console.warn(`[applyAgentRoutes] D${droneId}: no trajectory; distance from waypoint IDs may under-report SAM detours.`);
    }

    // Calculate points (sum of target priorities)
    let points = 0;
    for (const tid of routeTargets) {
      points += targetPriorities[tid] || 0;
    }

    // Build route info for visualization
    state.routes[droneId] = {
      route: route,
      points: points,
      distance: distance,
      trajectory: trajectory,
    };

    // Attach refueling metadata if present. Multi-stop fields (leg_distances[],
    // refuel_indices[], refuel_stops) drive the per-leg ⛽ stats split + the
    // refuel-crossing animation; the legacy leg1_2_distance is the 2-leg
    // fallback. getLegDistances() prefers leg_distances[] when present.
    const meta = refuelMetadata && refuelMetadata[droneId];
    if (meta) {
      if (meta.refuel_airport) state.routes[droneId].refuel_airport = meta.refuel_airport;
      if (meta.leg1_distance) state.routes[droneId].leg1_distance = meta.leg1_distance;
      if (meta.leg2_distance) state.routes[droneId].leg2_distance = meta.leg2_distance;
      if (Array.isArray(meta.leg_distances) && meta.leg_distances.length) {
        state.routes[droneId].leg_distances = meta.leg_distances;
      }
      if (Array.isArray(meta.refuel_indices)) {
        state.routes[droneId].refuel_indices = meta.refuel_indices;
      }
      if (Array.isArray(meta.refuel_stops) && meta.refuel_stops.length) {
        state.routes[droneId].refuel_stops = meta.refuel_stops;
      }
    }

    // Ensure trajectory is visible for this drone
    state.trajectoryVisible[droneId] = true;

    appendDebugLine(`Agent route D${droneId}: ${routeStr} (${points} pts, ${distance.toFixed(1)} fuel)`);
  }

  // Store allocations
  state.allocations = allocations;

  // Update the sequence input to show the currently selected drone's sequence
  const curDrone = state.currentDroneForSeq || "1";
  const seqInput = $("sequence-input");
  if (seqInput && state.sequences[curDrone]) {
    seqInput.value = state.sequences[curDrone];
  }

  // Update allocation display in Env tab
  updateAllocationDisplay(allocations);

  // Update debug output
  appendDebugLine(`Agent applied ${Object.keys(routes).length} drone routes (${totalPoints || "?"} pts, ${totalFuel?.toFixed(1) || "?"} fuel)`);

  // Redraw and update stats
  drawEnvironment();
  updateStatsFromRoutes();

  // Set draftSolution so Accept Solution → Animate workflow works
  missionState.draftSolution = {
    sequences: JSON.parse(JSON.stringify(state.sequences || {})),
    routes: JSON.parse(JSON.stringify(state.routes || {})),
    wrappedPolygons: [],
    allocations: JSON.parse(JSON.stringify(allocations || {})),
    isCheckpointReplan: false,
    droneConfigs: JSON.parse(JSON.stringify(state.droneConfigs || {})),
  };
  setMissionMode(MissionMode.DRAFT_READY, "agent solution ready");
  appendDebugLine("✅ Agent solution ready for review (Accept to animate)");
}

function applyAgentRoute(route, points, fuel) {
  // Apply the agent's route to drone D1 (legacy single-drone)
  // Route is like ["A1", "T3", "T7", "A1"]

  if (!route || route.length < 2) return;

  const routeStr = route.join(",");

  // Store as D1's sequence
  state.sequences["1"] = routeStr;

  // Update the sequence input if it exists
  const seqInput = $("sequence-input");
  if (seqInput) {
    seqInput.value = routeStr;
  }

  // Calculate points and distance if not provided
  let calculatedPoints = points || 0;
  let calculatedFuel = fuel || 0;

  if (!points || !fuel) {
    // Build waypoint lookup
    const waypoints = {};
    const targetPriorities = {};

    if (state.env) {
      for (const a of (state.env.airports || [])) {
        const id = a.id || a.label || "A?";
        waypoints[id] = [a.lat, a.lng];
      }
      for (const t of (state.env.targets || [])) {
        const id = t.id || t.label || "T?";
        waypoints[id] = [t.lat, t.lng];
        targetPriorities[id] = t.priority || t.value || 5;
      }
    }

    // Calculate distance
    if (!fuel) {
      calculatedFuel = 0;
      for (let i = 0; i < route.length - 1; i++) {
        const from = waypoints[route[i]];
        const to = waypoints[route[i + 1]];
        if (from && to) {
          calculatedFuel += _distance(from, to);
        }
      }
    }

    // Calculate points
    if (!points) {
      calculatedPoints = 0;
      const routeTargets = route.filter(wp => String(wp).startsWith('T'));
      for (const tid of routeTargets) {
        calculatedPoints += targetPriorities[tid] || 0;
      }
    }
  }

  // Build route info for visualization
  state.routes["1"] = {
    route: route,
    points: calculatedPoints,
    distance: calculatedFuel,
    trajectory: buildTrajectoryFromRoute(route),
  };
}

window.addEventListener("keydown", (e) => {
  if (String(e.key).toLowerCase() === "c") {
    // Skip if user is typing in an input field
    const activeTag = document.activeElement?.tagName?.toLowerCase();
    if (activeTag === "input" || activeTag === "textarea" || activeTag === "select") {
      return;
    }

    appendDebugLine("Keypress: C detected (attempting cut/checkpoint)");
    try {
      cutAtCheckpoint();
    } catch (err) {
      appendDebugLine("Cut failed (see console)");
    }
  }

  // P key for Pause
  if (String(e.key).toLowerCase() === "p") {
    // Skip if user is typing in an input field
    const activeTag = document.activeElement?.tagName?.toLowerCase();
    if (activeTag === "input" || activeTag === "textarea" || activeTag === "select") {
      return;
    }

    const perms = getUiPermissions();
    if (perms.canPause) {
      pauseAnimation();
    }
  }
});

function buildTrajectoryFromRoute(route) {
  // Build trajectory points from waypoint IDs
  if (!state.env || !route) return [];

  const trajectory = [];
  const waypoints = {};

  // Index airports (lat/lng)
  for (const a of (state.env.airports || [])) {
    const id = a.id || a.label || "A?";
    waypoints[id] = [a.lat, a.lng];
  }

  // Index targets (lat/lng)
  for (const t of (state.env.targets || [])) {
    const id = t.id || t.label || "T?";
    waypoints[id] = [t.lat, t.lng];
  }

  // Build trajectory (lat/lng points)
  for (const wp of route) {
    if (waypoints[wp]) {
      trajectory.push(waypoints[wp]);
    }
  }

  return trajectory;
}

// =============================================================================
// AI Settings Modal
// =============================================================================

const LLMSettings = ISRLLMSettings.create({
  document,
  storage: ISRPreferences.create(() => localStorage),
  http: ISRHttp.create((url, options) => fetch(url, options)),
  createModal: options => ISRModal.create(options),
  appendDebugLine: message => appendDebugLine(message),
});

// Initialize LLM Settings when DOM is ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => LLMSettings.init());
} else {
  LLMSettings.init();
}

// ============================================================================
// Agent Comparison UI
// ============================================================================
const CompareUI = {
  _history: [],
  _lastResult: null,  // stores full comparison data including routes/trajectories

  // Every planner the compare endpoint can return, in column order. Only those
  // present in a given response are rendered, so LLM-only batches look
  // unchanged while GNN batches gain columns.
  ENTRANTS: [
    { key: "reasoning", label: "Single Agent" },
    { key: "langgraph_v4", label: "LangGraph V4" },
    { key: "crewai", label: "CrewAI" },
    { key: "gnn", label: "GNN" },
    { key: "gnn_decoder", label: "GNN (decoder)" },
    { key: "solver_control", label: "Solver control" },
  ],

  WINNER_CLASS: {
    reasoning: "winner-reasoning",
    langgraph_v4: "winner-v4",
    crewai: "winner-crewai",
    gnn: "winner-gnn",
    gnn_decoder: "winner-gnn",
    solver_control: "winner-solver",
    tie: "winner-tie",
    none: "winner-tie",
  },

  WINNER_LABEL: {
    reasoning: "Reasoning",
    langgraph_v4: "LangGraph",
    crewai: "CrewAI",
    gnn: "GNN",
    gnn_decoder: "GNN dec.",
    solver_control: "Solver",
    tie: "Tie",
    none: "None",
  },

  init() {
    const btn = $("btn-run-compare");
    if (btn) btn.addEventListener("click", () => this.runComparison());
    const input = $("compare-input");
    if (input) input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this.runComparison(); }
    });
  },

  async runComparison() {
    const input = $("compare-input");
    const status = $("compare-status");
    const results = $("compare-results");
    const btn = $("btn-run-compare");
    if (!input || !input.value.trim()) return;

    const message = input.value.trim();
    if (btn) btn.disabled = true;
    if (status) { status.style.display = "block"; status.textContent = "Running both agents... this may take 30-60 seconds."; }
    if (results) results.style.display = "none";

    try {
      const payload = {
        message,
        env: state.env,
        drone_configs: state.droneConfigs,
        llm_config: LLMSettings.getCurrentSettings(),
      };

      const resp = await fetch("/api/agents/compare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
      const data = await resp.json();
      this._lastResult = data;
      this.displayResults(data, message);
      this._history.unshift(data);
      this.updateHistory();

      // Push trace events to the Trace tab for all agents
      const v4Trace = data.langgraph_v4?.trace_events || [];
      const rTrace = data.reasoning?.trace_events || [];
      const cTrace = data.crewai?.trace_events || [];
      if (v4Trace.length > 0 || rTrace.length > 0 || cTrace.length > 0) {
        const combinedEvents = [];
        if (rTrace.length > 0) {
          combinedEvents.push({t: "compare.reasoning", msg: `Reasoning agent: ${data.reasoning?.latency_s}s, ${data.reasoning?.total_points} pts, ${((data.reasoning?.target_coverage||0)*100).toFixed(0)}% coverage`});
          combinedEvents.push(...rTrace);
        }
        if (v4Trace.length > 0) {
          combinedEvents.push({t: "compare.langgraph_v4", msg: `LangGraph V4: ${data.langgraph_v4?.latency_s}s, ${data.langgraph_v4?.total_points} pts, ${((data.langgraph_v4?.target_coverage||0)*100).toFixed(0)}% coverage`});
          combinedEvents.push(...v4Trace);
        }
        if (cTrace.length > 0) {
          combinedEvents.push({t: "compare.crewai", msg: `CrewAI: ${data.crewai?.latency_s}s, ${data.crewai?.total_points} pts, ${((data.crewai?.target_coverage||0)*100).toFixed(0)}% coverage`});
          combinedEvents.push(...cTrace);
        }
        state.agentTraceRuns.push({
          ts: Date.now(),
          user_message: `[Compare] ${message}`,
          mission_id: null,
          trace_events: combinedEvents,
        });
        renderAgentsTraceTab();
      }
    } catch (err) {
      if (status) { status.textContent = `Error: ${err.message}`; status.style.color = "#ef4444"; }
    } finally {
      if (btn) btn.disabled = false;
    }
  },

  displayResults(data, message) {
    const status = $("compare-status");
    const results = $("compare-results");
    const tbody = $("compare-tbody");
    const responses = $("compare-responses");
    if (!results || !tbody) return;

    if (status) { status.style.display = "block"; status.textContent = `Comparison complete for: "${message}"`; status.style.color = ""; }
    results.style.display = "block";

    const r = data.reasoning || {};
    const v = data.langgraph_v4 || {};
    const c = data.crewai || {};
    const w = data.winner || {};

    // Entrants are discovered from the response so the GNN variants and the
    // deterministic solver control add columns without further UI changes.
    const entrants = CompareUI.ENTRANTS.filter((e) => data[e.key]);
    const cellClassFor = (winner, key) => (winner === key ? CompareUI.WINNER_CLASS[key] || "" : "");

    const headRow = $("compare-thead-row");
    if (headRow) {
      headRow.innerHTML =
        "<th>Metric</th>" +
        entrants.map((e) => `<th>${e.label}</th>`).join("") +
        "<th>Winner</th>";
    }

    const pct = (x) => `${((x || 0) * 100).toFixed(0)}%`;
    const metrics = [
      { label: "Latency", key: "latency", get: (a) => `${a.latency_s || 0}s` },
      { label: "LLM Calls", key: "llm_calls", get: (a) => a.llm_calls || 0 },
      { label: "Total Points", key: "total_points", get: (a) => a.total_points || 0 },
      { label: "Fuel Used (km)", key: "fuel_efficiency", get: (a) => a.total_fuel_km || 0 },
      { label: "Fuel Efficiency", key: "fuel_efficiency", get: (a) => a.fuel_efficiency || 0 },
      { label: "Targets Visited", key: "target_coverage", get: (a) => `${a.targets_visited || 0} / ${a.targets_total || 0}` },
      { label: "Coverage", key: "target_coverage", get: (a) => pct(a.target_coverage) },
      { label: "Valid Solution", key: null, get: (a) => (a.valid_solution ? "Yes" : "No") },
    ];

    tbody.innerHTML = "";
    for (const m of metrics) {
      const winner = m.key ? (w[m.key] || "tie") : "tie";
      const winnerClass = CompareUI.WINNER_CLASS[winner] || "winner-tie";
      const winnerLabel = CompareUI.WINNER_LABEL[winner] || "Tie";
      const cells = entrants
        .map((e) => {
          const agent = data[e.key] || {};
          // A skipped or failed entrant shows the reason instead of zeros.
          const value = agent.error ? `<span class="compare-failed">—</span>` : m.get(agent);
          return `<td class="${cellClassFor(winner, e.key)}">${value}</td>`;
        })
        .join("");
      tbody.innerHTML += `<tr>
        <td>${m.label}</td>
        ${cells}
        <td class="winner-cell ${winnerClass}">${winnerLabel}</td>
      </tr>`;
    }

    // Show response text with Apply buttons + trace
    if (responses) {
      const _buildTraceHtml = (trace) => {
        if (!trace || trace.length === 0) return '';
        let html = '<details class="compare-trace-details"><summary>Agent Trace (' + trace.length + ' events)</summary><div class="compare-trace-list">';
        for (const evt of trace) {
          const evtType = evt.t || evt.type || '';
          const typeClass = evtType.includes('error') ? 'trace-error' : evtType.includes('warn') ? 'trace-warn' : 'trace-info';
          html += '<div class="compare-trace-event ' + typeClass + '">';
          html += '<span class="trace-type">' + evtType + '</span> ';
          html += '<span class="trace-msg">' + (evt.msg || evt.message || '') + '</span>';
          if (evt.data) html += '<pre class="trace-data">' + JSON.stringify(evt.data, null, 1) + '</pre>';
          html += '</div>';
        }
        return html + '</div></details>';
      };

      // Rendered per entrant so GNN and solver-control responses get the same
      // Apply-to-Map affordance as the LLM agents.
      responses.innerHTML = entrants
        .map((e) => {
          const agent = data[e.key] || {};
          const hasRoutes =
            agent.valid_solution && agent.routes && Object.keys(agent.routes).length > 0;
          const applyBtn = hasRoutes
            ? ` <button class="small-btn compare-apply-btn" onclick="CompareUI.applyResult('${e.key}')">Apply to Map</button>`
            : "";
          const body = agent.error
            ? `Not run / failed: ${agent.error}`
            : (agent.response_text || "No response").substring(0, 500);
          return `
        <div class="compare-response-box">
          <h4>${e.label} Response:${applyBtn}</h4>
          <pre>${body}</pre>
          ${_buildTraceHtml(agent.trace_events)}
        </div>`;
        })
        .join("");
    }
  },

  updateHistory() {
    const container = $("compare-history");
    if (!container) return;
    container.innerHTML = this._history.slice(0, 10).map((d, i) => {
      const r = d.reasoning || {};
      const v = d.langgraph_v4 || {};
      const c = d.crewai || {};
      return `<div class="compare-run-card">
        <strong>Run ${this._history.length - i}:</strong> "${d.request || ''}"<br>
        Reasoning: ${r.latency_s}s, ${r.total_points} pts, ${((r.target_coverage||0)*100).toFixed(0)}% |
        LangGraph: ${v.latency_s}s, ${v.total_points} pts, ${((v.target_coverage||0)*100).toFixed(0)}% |
        CrewAI: ${c.latency_s || 0}s, ${c.total_points || 0} pts, ${((c.target_coverage||0)*100).toFixed(0)}%
      </div>`;
    }).join("");
  },

  applyResult(agentKey) {
    if (!this._lastResult) return;
    const agentData = this._lastResult[agentKey];
    if (!agentData || !agentData.routes) {
      appendDebugLine(`[Compare] No routes to apply from ${agentKey}`);
      return;
    }

    const routes = agentData.routes;
    const trajectories = agentData.trajectories || {};

    // Debug: log what we have
    console.log(`[Compare] applyResult(${agentKey}):`, {
      routeKeys: Object.keys(routes),
      trajKeys: Object.keys(trajectories),
      trajLengths: Object.fromEntries(Object.entries(trajectories).map(([d,t]) => [d, Array.isArray(t) ? t.length : 'not-array'])),
      routeHasTraj: Object.fromEntries(Object.entries(routes).map(([d,r]) => [d, typeof r === 'object' && !Array.isArray(r) ? (r.trajectory?.length || 0) : 'not-obj'])),
    });

    // Extract route arrays and trajectories from whatever format we got.
    // Both agents return routes as {droneId: {route, distance, ...}} objects.
    // Trajectories may be inside route objects OR in the standalone dict.
    // Standalone trajectories take priority (SAM-avoiding paths).
    const extractedRoutes = {};
    const extractedTrajectories = {};
    let totalPoints = agentData.total_points || 0;
    let totalFuel = agentData.total_fuel_km || 0;

    for (const [droneId, routeData] of Object.entries(routes)) {
      if (typeof routeData === 'object' && !Array.isArray(routeData)) {
        extractedRoutes[droneId] = routeData.route || [];
        // Check inside route object
        if (routeData.trajectory && routeData.trajectory.length > 0) {
          extractedTrajectories[droneId] = routeData.trajectory;
        }
        if (!totalPoints) totalPoints += routeData.points || 0;
        if (!totalFuel) totalFuel += routeData.fuel_used || routeData.distance || 0;
      } else if (Array.isArray(routeData)) {
        extractedRoutes[droneId] = routeData;
      }
    }

    // Standalone trajectories dict takes priority (has SAM-avoiding paths)
    for (const [did, traj] of Object.entries(trajectories)) {
      if (traj && traj.length > 0) {
        extractedTrajectories[did] = traj;
      }
    }

    appendDebugLine(`[Compare] Applying ${agentKey}: ${Object.keys(extractedRoutes).length} drones, trajectories: ${Object.entries(extractedTrajectories).map(([d,t]) => 'D'+d+':'+t.length+'pts').join(', ') || 'none'}`);
    applyAgentRoutes(extractedRoutes, extractedTrajectories, totalPoints, totalFuel);

    // Visual feedback
    const status = $("compare-status");
    if (status) {
      const entrant = CompareUI.ENTRANTS.find((e) => e.key === agentKey);
      status.textContent = `Applied ${entrant ? entrant.label : agentKey} solution to map.`;
      status.style.color = "";
    }
  },
};

// ---------------------------------------------------------------------------
// GNN tab: inspect the graph the model consumes, and plan with it.
// ---------------------------------------------------------------------------
const GnnUI = {
  _lastPlan: null,
  _lastBenchmark: null,

  PLANNER_LABELS: {
    solver_control: "Existing planner (exact solver)",
    gnn: "GNN allocation + exact solver",
    gnn_decoder: "GNN end-to-end (neural routing)",
  },

  init() {
    const build = $("btn-gnn-build");
    if (build) build.addEventListener("click", () => this.buildGraph());
    const predict = $("btn-gnn-predict");
    if (predict) predict.addEventListener("click", () => this.plan());
    const bench = $("btn-gnn-benchmark");
    if (bench) bench.addEventListener("click", () => this.benchmark());
    const refresh = $("btn-gnn-refresh-models");
    if (refresh) refresh.addEventListener("click", () => this.loadModels());
    this.loadModels();
  },

  async loadModels() {
    try {
      const resp = await fetch("/api/gnn/models");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();

      const fill = (id, entries, noneLabel) => {
        const sel = $(id);
        if (!sel) return;
        const previous = sel.value;
        sel.innerHTML = `<option value="">${noneLabel}</option>`;
        for (const m of entries) {
          const opt = document.createElement("option");
          opt.value = m.path;
          const detail = m.error
            ? " — UNREADABLE"
            : m.final_loss != null
              ? ` — ${m.epochs ?? "?"} epochs, loss ${m.final_loss}`
              : "";
          opt.textContent = `${m.name}${detail}`;
          if (m.error) opt.disabled = true;
          sel.appendChild(opt);
        }
        if (previous) sel.value = previous;
      };

      fill("gnn-model-path", data.assignment || [], "None (heuristic)");
      fill("gnn-route-model-path", data.route || [], "None (greedy)");

      const total = (data.assignment || []).length + (data.route || []).length;
      if (total === 0) {
        this._status(
          "No trained models found in models/. Train one with scripts/train_gnn.py, " +
            "or run with the built-in heuristic.",
          "#fbbf24"
        );
      }
    } catch (err) {
      this._status(`Could not list models: ${err.message}`, "#f87171");
    }
  },

  _requireEnv() {
    if (!state.env) {
      this._status("Load an environment in the Env tab first.", "#fbbf24");
      return false;
    }
    return true;
  },

  _payload() {
    const modelPath = ($("gnn-model-path")?.value || "").trim();
    const routeModelPath = ($("gnn-route-model-path")?.value || "").trim();
    return {
      env: state.env,
      drone_configs: state.droneConfigs,
      buffer: 0.0,
      model_path: modelPath || null,
      route_model_path: routeModelPath || null,
      use_solver: $("gnn-use-solver")?.checked !== false,
    };
  },

  _status(text, color) {
    const el = $("gnn-status");
    if (!el) return;
    el.style.display = "block";
    el.textContent = text;
    el.style.color = color || "";
  },

  _show(summaryHtml, detailObj) {
    const empty = $("gnn-empty");
    const summary = $("gnn-summary");
    const detail = $("gnn-detail");
    const compare = $("gnn-compare");
    if (empty) empty.style.display = "none";
    if (compare) compare.style.display = "none";
    if (summary) {
      summary.style.display = "block";
      summary.innerHTML = summaryHtml;
    }
    if (detail) {
      detail.style.display = "block";
      detail.textContent = JSON.stringify(detailObj, null, 2);
    }
  },

  async benchmark() {
    if (!this._requireEnv()) return;
    const btn = $("btn-gnn-benchmark");
    if (btn) { btn.disabled = true; btn.textContent = "Running…"; }
    this._status("Running all planners on this environment…");
    try {
      const resp = await fetch("/api/gnn/benchmark", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(this._payload()),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
      const data = await resp.json();
      this._lastBenchmark = data;
      this.renderBenchmark(data);
      this._status("Comparison complete.", "#34d399");
    } catch (err) {
      this._status(`Comparison failed: ${err.message}`, "#f87171");
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "Compare all planners"; }
    }
  },

  renderBenchmark(data) {
    const empty = $("gnn-empty");
    const summary = $("gnn-summary");
    const compare = $("gnn-compare");
    const detail = $("gnn-detail");
    if (empty) empty.style.display = "none";
    if (detail) detail.style.display = "none";

    const es = data.env_summary || {};
    if (summary) {
      summary.style.display = "block";
      summary.innerHTML =
        `<span class="gnn-metric">Targets <b>${es.targets}</b></span>` +
        `<span class="gnn-metric">Airports <b>${es.airports}</b></span>` +
        `<span class="gnn-metric">SAMs <b>${es.sams}</b></span>` +
        `<span class="gnn-metric">Drones <b>${es.drones_enabled}</b></span>`;
    }
    if (!compare) return;

    const rows = Object.entries(data.results || {});
    // Best score among planners that produced a fuel-feasible solution. Points
    // from an infeasible route are not a fair comparison.
    const validPts = rows
      .filter(([, r]) => !r.error && r.valid_solution)
      .map(([, r]) => r.total_points || 0);
    const bestPts = validPts.length ? Math.max(...validPts) : null;
    const validLat = rows.filter(([, r]) => !r.error).map(([, r]) => r.latency_s || 0);
    const bestLat = validLat.length ? Math.min(...validLat) : null;

    let html = `<table class="compare-table"><thead><tr>
      <th>Planner</th><th>Time</th><th>Points</th><th>Targets hit</th>
      <th>Fuel (km)</th><th>Fuel-feasible</th><th></th></tr></thead><tbody>`;

    for (const [key, r] of rows) {
      const label = this.PLANNER_LABELS[key] || key;
      if (r.error) {
        html += `<tr><td>${label}</td><td colspan="6" class="gnn-fail">Failed: ${r.error}</td></tr>`;
        continue;
      }
      const ptsClass = bestPts !== null && r.valid_solution && (r.total_points || 0) === bestPts
        ? "gnn-best" : "";
      const latClass = bestLat !== null && (r.latency_s || 0) === bestLat ? "gnn-best" : "";
      const validCell = r.valid_solution
        ? '<span class="gnn-ok">Yes</span>'
        : '<span class="gnn-fail">No</span>';
      const hasRoutes = r.routes && Object.keys(r.routes).length > 0;
      html += `<tr>
        <td>${label}</td>
        <td class="${latClass}">${(r.latency_s || 0).toFixed(3)} s</td>
        <td class="${ptsClass}">${r.total_points || 0}</td>
        <td>${r.targets_visited || 0} / ${r.targets_total || 0}</td>
        <td>${(r.total_fuel_km || 0).toFixed(1)}</td>
        <td>${validCell}</td>
        <td>${hasRoutes ? `<button class="small-btn" onclick="GnnUI.showOnMap('${key}')">Show on map</button>` : ""}</td>
      </tr>`;
    }
    html += `</tbody></table>
      <p class="gnn-hint">Points are only comparable between fuel-feasible rows.
      A planner that overruns its fuel budget can score higher while flying a
      mission the drones cannot actually complete.</p>`;

    compare.style.display = "block";
    compare.innerHTML = html;
  },

  showOnMap(plannerKey) {
    const r = (this._lastBenchmark?.results || {})[plannerKey];
    if (!r || !r.routes) return;
    const routes = {};
    const trajectories = r.trajectories || {};
    let points = 0;
    let fuel = 0;
    for (const [did, rd] of Object.entries(r.routes)) {
      routes[did] = rd.route || [];
      if (rd.trajectory && rd.trajectory.length && !trajectories[did]) {
        trajectories[did] = rd.trajectory;
      }
      points += rd.points || 0;
      fuel += rd.distance || 0;
    }
    applyAgentRoutes(routes, trajectories, r.total_points || points, r.total_fuel_km || fuel);
    this._status(`Showing ${this.PLANNER_LABELS[plannerKey] || plannerKey} on the map.`, "#a78bfa");
  },

  async buildGraph() {
    if (!this._requireEnv()) return;
    this._status("Building graph…");
    try {
      const resp = await fetch("/api/gnn/build_graph", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(this._payload()),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
      const g = await resp.json();

      const excluded = g.excluded_targets || [];
      this._show(
        `<span class="gnn-metric">Nodes <b>${g.n_nodes}</b></span>
         <span class="gnn-metric">Edges <b>${g.n_edges}</b></span>
         <span class="gnn-metric">Drones <b>${g.n_drones}</b></span>
         <span class="gnn-metric">Node features <b>${(g.node_feature_names || []).length}</b></span>
         ${excluded.length ? `<div class="gnn-hint">Excluded (inside SAM rings or unreachable): ${excluded.join(", ")}</div>` : ""}`,
        {
          node_feature_names: g.node_feature_names,
          edge_feature_names: g.edge_feature_names,
          drone_feature_names: g.drone_feature_names,
          global_features: g.global_features,
          meta: g.meta,
        }
      );
      this._status("Graph built.", "#34d399");
    } catch (err) {
      this._status(`Build failed: ${err.message}`, "#f87171");
    }
  },

  async plan() {
    if (!this._requireEnv()) return;
    const useSolver = $("gnn-use-solver")?.checked !== false;
    this._status(`Planning (${useSolver ? "exact solver" : "learned decoder"})…`);
    try {
      const resp = await fetch("/api/gnn/predict", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(this._payload()),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
      const data = await resp.json();
      this._lastPlan = data;

      const routes = (data.solution || {}).routes || {};
      const totalPoints = Object.values(routes).reduce((s, r) => s + (r.points || 0), 0);
      const totalKm = Object.values(routes).reduce((s, r) => s + (r.distance || 0), 0);

      this._show(
        `<span class="gnn-metric">Model <b>${data.model_name}</b></span>
         <span class="gnn-metric">Latency <b>${data.latency_s}s</b></span>
         <span class="gnn-metric">Assigned <b>${Object.keys(data.assignments || {}).length}</b></span>
         <span class="gnn-metric">Points <b>${totalPoints}</b></span>
         <span class="gnn-metric">Distance <b>${totalKm.toFixed(1)} km</b></span>
         ${data.error ? `<div class="gnn-hint" style="color:#fbbf24;">${data.error}</div>` : ""}
         <div style="margin-top:6px;">
           <button class="small-btn compare-apply-btn" onclick="GnnUI.applyToMap()">Apply to Map</button>
         </div>`,
        { assignments: data.assignments, routes }
      );
      this._status("Plan complete.", "#34d399");
    } catch (err) {
      this._status(`Plan failed: ${err.message}`, "#f87171");
    }
  },

  applyToMap() {
    if (!this._lastPlan) return;
    const routes = (this._lastPlan.solution || {}).routes || {};
    const extractedRoutes = {};
    const extractedTrajectories = {};
    let totalPoints = 0;
    let totalFuel = 0;

    for (const [droneId, routeData] of Object.entries(routes)) {
      extractedRoutes[droneId] = routeData.route || [];
      if (routeData.trajectory && routeData.trajectory.length > 0) {
        extractedTrajectories[droneId] = routeData.trajectory;
      }
      totalPoints += routeData.points || 0;
      totalFuel += routeData.distance || 0;
    }

    applyAgentRoutes(extractedRoutes, extractedTrajectories, totalPoints, totalFuel);
    this._status("Applied GNN plan to map.", "#a78bfa");
  },
};

// Init Compare + GNN UI
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => { CompareUI.init(); GnnUI.init(); });
} else {
  CompareUI.init();
  GnnUI.init();
}
