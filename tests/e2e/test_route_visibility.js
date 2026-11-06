// Exercise actual draft application and Map canvas drawing after an import.
// node tests/e2e/test_route_visibility.js
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const source = fs.readFileSync(path.join(__dirname, "../../webapp/isr.js"), "utf8");
function extract(name) {
  const start = source.indexOf(`function ${name}(`);
  assert(start >= 0, `${name} must exist`);
  const end = source.indexOf("\n}", start);
  return source.slice(start, end + 2);
}

const strokes = [];
const fills = [];
const cutMarkers = [];
let points = [];
let commands = [];
let shapes = [];
const ctx = {
  save() {}, restore() {}, setTransform() {}, setLineDash() {},
  clearRect() { strokes.length = 0; fills.length = 0; cutMarkers.length = 0; },
  beginPath() { points = []; commands = []; shapes = []; },
  moveTo(x, y) { points.push([x, y]); commands.push(["M", x, y]); },
  lineTo(x, y) { points.push([x, y]); commands.push(["L", x, y]); },
  closePath() { commands.push(["Z"]); },
  stroke() { strokes.push({ color: this.strokeStyle, points: points.slice(), commands: commands.slice() }); },
  rect(x, y, width, height) { shapes.push({type: "airport", center: [x + width / 2, y + height / 2]}); },
  arc(x, y) { shapes.push({type: "drone", center: [x, y]}); },
  fill() { fills.push(...shapes.map(shape => ({...shape, color: this.fillStyle}))); },
  fillRect() {}, fillText() {}, strokeText() {},
};
const canvasRect = {left: 0, top: 0, width: 1000, height: 600};
const elements = {
  "env-canvas": { width: 1000, height: 600, getContext: () => ctx,
    getBoundingClientRect: () => ({...canvasRect}) },
  "traj-all": {},
};
for (let did = 1; did <= 5; did++) elements[`traj-d${did}`] = {};
const frozen = {};
const checkpointMarkers = new Map();
const context = {
  state: {
    env: {
      airports: [
        { id: "A1", lat: 32.8998, lng: -97.0403 },
        { id: "A2", lat: 25.2532, lng: 55.3657 },
      ],
      targets: [], sams: [],
    },
    // loadFromJson clears both fields before a new solve.
    routes: {}, trajectoryVisible: {},
    animation: { active: false, drones: {} }, visitedTargets: [],
    checkpoint: { active: false },
  },
  missionState: { mode: "IDLE" }, MissionMode: { ANIMATING: "ANIMATING" },
  viewState: { centerLat: 29, centerLng: -21, zoomLevel: 2 },
  mapState: { active: true }, _drawEnvFrameCount: 0,
  $: (id) => elements[id],
  cesiumView: { isActive: () => false },
  missionReplay: {
    getAllFrozenTrajectories: () => frozen,
    setFrozenTrajectory: (did, trajectory) => { frozen[did] = trajectory; },
    getSegmentCount: () => 1,
  },
  syncTankerAltBox() {}, renderMapLegend() {}, updateCategoryCounts() {},
  drawTimeSyncMarkers() {}, collectCheckpointMarkers: () => checkpointMarkers,
  drawCutMarker: (_ctx, x, y, label) => cutMarkers.push({center: [x, y], label}),
  updateTimeSyncUi() {}, appendDebugLine() {}, updateAllocationDisplay() {},
  syncSequencesFromRoutes() {}, updateStatsFromAnimation() {}, placeLostDrones() {},
};
context.getEditableEnv = () => context.state.env;
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, "../../webapp/latlng_utils.js"), "utf8"), context);
const contractStart = source.indexOf('// SINGLE-WORLD COORDINATE CONTRACT');
const contractEnd = source.indexOf('// END SINGLE-WORLD COORDINATE CONTRACT', contractStart);
assert(contractStart >= 0 && contractEnd > contractStart);
vm.runInContext(source.slice(contractStart, contractEnd), context);
vm.runInContext([
  "applyDraftSolutionToUI", "drawEnvironment", "updateTrajectoryButtonStates",
  "toggleTrajectory", "toggleAllTrajectories", "hitTestPixel", "split_polyline_at_distance",
  "locatePlaybackPosition", "playbackDistanceAtPosition", "anchorPlaybackCheckpoint",
].map(extract).join("\n"), context);

const route = { route: ["A1", "A2"], trajectory: [[32.8998, -97.0403], [25.2532, 55.3657]] };
const draft = { routes: { 1: route, 2: { route: [], trajectory: [] } } };
const routeStrokes = () => strokes.filter((stroke) => stroke.color === "#3b82f6" && stroke.points.length >= 2);

context.applyDraftSolutionToUI(draft);
assert.strictEqual(routeStrokes().length, 1, "fresh imported DFW–DXB solve must draw a Map route before Accept");
assert.strictEqual(elements["traj-d1"].checked, true, "Show checkbox must match the displayed route");
assert.strictEqual(elements["traj-d2"].checked, false, "empty routes must not be selected for display");
for (const [x, y] of routeStrokes()[0].points) {
  assert(Number.isFinite(x) && Number.isFinite(y), "route must project to finite canvas coordinates");
  assert(x >= 0 && x <= 1000 && y >= 0 && y <= 600, "DFW and DXB must draw within the fitted viewport");
}

context.toggleTrajectory("1");
assert.strictEqual(routeStrokes().length, 0);
context.applyDraftSolutionToUI(draft);
assert.strictEqual(routeStrokes().length, 0, "redrawing or re-solving must preserve an explicit Hide selection");
assert.strictEqual(elements["traj-d1"].checked, false);
context.toggleTrajectory("1");
assert.strictEqual(routeStrokes().length, 1);

// Legacy responses without detailed geometry still draw via route waypoints.
context.state.trajectoryVisible = {};
context.applyDraftSolutionToUI({ routes: { 1: { route: ["A1", "A2"] } } });
assert.strictEqual(routeStrokes().length, 1, "waypoint-only route must also be visible after import");

// Existing checkpoint behavior still displays both flown history and the new draft.
context.state.trajectoryVisible = {};
context.applyDraftSolutionToUI({
  isCheckpointReplan: true,
  checkpointSegments: { 1: { prefix: route.trajectory } },
  routes: { 1: { route: ["C1", "A2"], trajectory: [[40, 0], [25.2532, 55.3657]] } },
});
assert.strictEqual(routeStrokes().length, 2, "checkpoint draft and flown history must both draw");
context.toggleAllTrajectories();
assert.strictEqual(routeStrokes().length, 0, "Hide all must hide both current and flown routes");
context.toggleAllTrajectories();
assert.strictEqual(routeStrokes().length, 2);

function closePoint(actual, expected, message) {
  assert(actual && expected, message);
  actual.forEach((value, axis) => assert(Math.abs(value - expected[axis]) < 1e-7,
    `${message}: axis ${axis}, expected ${expected[axis]}, got ${value}`));
}

const legacy = {
  env: {airports: [{id: "A1", lat: 32.8998, lng: -457.0297908783},
    {id: "A2", lat: 25.2532, lng: -304.638004303}], targets: [], sams: []},
  map_view: {center: {lat: 29, lng: -367.734375}, zoom: 2},
};
const migrated = context.migrateLegacyMapCoordinates(legacy);
const canonicalTrajectory = migrated.env.airports.map(a => [a.lat, a.lng]);
const midpoint = context.interpolateFlightPosition(canonicalTrajectory[0], canonicalTrajectory[1], 0.5);

for (const mode of ["Map", "Grid"]) {
  context.mapState.active = mode === "Map";
  context.viewState.centerLat = migrated.map_view.center.lat;
  context.viewState.centerLng = migrated.map_view.center.lng;
  context.constrainMapView();
  context.state.env = migrated.env;
  context.state.trajectoryVisible = {"1": true};
  context.state.routes = {"1": {route: ["A1", "A2"], trajectory: canonicalTrajectory}};
  context.state.animation.drones = {"1": {animating: true, progress: 0.5,
    distanceTraveled: 50, totalDistance: 100, cumulativeDistances: [0, 100]}};
  frozen["1"] = [canonicalTrajectory[0], midpoint];
  checkpointMarkers.set("cut", {pos: midpoint, label: "C1", isLost: false});
  context.drawEnvironment();
  const airportShapes = fills.filter(fill => fill.type === "airport");
  const droneShape = fills.find(fill => fill.type === "drone" && fill.color === "#3b82f6");
  const paths = routeStrokes();
  assert.strictEqual(airportShapes.length, 2);
  assert.strictEqual(paths.length, 2, `${mode}: frozen and active routes must draw`);
  closePoint(droneShape.center, cutMarkers[0].center, `${mode}: drone and diamond share one geographic frame`);
  closePoint(droneShape.center, paths[0].points.at(-1), `${mode}: frozen prefix ends at drone/diamond`);
  [paths[1].points[0], paths[1].points.at(-1)].forEach((point, index) => closePoint(point, airportShapes[index].center,
    `${mode}: canonical solver endpoint meets migrated airport ${index + 1}`));
  airportShapes.forEach((airport, index) => {
    const hit = context.hitTestPixel(...airport.center);
    assert.strictEqual(hit.kind, "airport", `${mode}: visible airport must be editable`);
    assert.strictEqual(hit.index, index);
    assert(airport.center[0] > 0 && airport.center[0] < 1000 && airport.center[1] > 0 && airport.center[1] < 600,
      `${mode}: migrated airports must stay in the viewport`);
  });
}

// Dateline crossings split at the single world's edges. Halfway through a
// short crossing, the live drone must stay at that seam, not at Greenwich.
delete frozen["1"];
checkpointMarkers.clear();
context.viewState.centerLat = 0;
context.viewState.centerLng = 0;
context.state.env.airports = [{id: "A1", lat: 0, lng: 179}, {id: "A2", lat: 0, lng: -179}];
context.state.routes["1"] = {route: ["A1", "A2"], trajectory: [[0, 179], [0, -179]]};
context.drawEnvironment();
const datelineStroke = routeStrokes()[0];
assert(datelineStroke.commands.filter(command => command[0] === "M").length >= 2,
  "Dateline route must split into separate subpaths");
let previous;
for (const [kind, x, y] of datelineStroke.commands) {
  if (kind === "L") assert(Math.abs(x - previous[0]) < 10, "Short dateline route must not create a world-spanning chord");
  if (kind === "M" || kind === "L") previous = [x, y];
}
const datelineDrone = fills.find(fill => fill.type === "drone");
assert(Math.abs(datelineDrone.center[0] - 500) >= 500, "Halfway drone stays at dateline");

function distanceToDrawnPath(point, stroke) {
  let previous;
  let nearest = Infinity;
  for (const [kind, x, y] of stroke.commands) {
    if (kind === "L" && previous) {
      const dx = x - previous[0], dy = y - previous[1];
      const squared = dx * dx + dy * dy;
      const fraction = squared ? Math.max(0, Math.min(1,
        ((point[0] - previous[0]) * dx + (point[1] - previous[1]) * dy) / squared)) : 0;
      nearest = Math.min(nearest, Math.hypot(point[0] - previous[0] - fraction * dx,
        point[1] - previous[1] - fraction * dy));
    }
    if (kind === "M" || kind === "L") previous = [x, y];
  }
  return nearest;
}

// Independent references calculated from the initial great-circle bearing and
// angular distance on a sphere, rather than the production interpolation.
const sparseLeg = [[60.1045386, -65.1806243], [60.4598845, -64.2536139]];
const interior = [
  [0.25, [60.19397860817895, -64.95075746516638]],
  [0.37, [60.23676798022576, -64.83997653051627]],
  [0.5, [60.28301868704448, -64.71963723534061]],
  [0.61, [60.32206868915331, -64.61754578994113]],
  [0.75, [60.37165520412122, -64.48725792009652]],
  [0.83, [60.3999330603981, -64.41262970053646]],
];
const legDistance = context.haversineKm(...sparseLeg[0], ...sparseLeg[1]);

// The inverse lookup used when restoring a saved cut must recover the same
// clock fraction, including a nonzero preceding frame distance. Anchoring must
// insert that exact position without stretching the segment's distance/time.
for (const [fraction, expected] of interior) {
  const offset = 12;
  const expectedDistance = offset + fraction * legDistance;
  const cumulative = [offset, offset + legDistance];
  const location = context.locatePlaybackPosition(sparseLeg, cumulative, expected, offset);
  assert(location && Math.abs(location.fraction - fraction) < 1e-9,
    `Inverse playback lookup recovers great-circle fraction ${fraction}`);
  assert(Math.abs(location.distance - expectedDistance) < 1e-8,
    'Inverse playback lookup preserves the cumulative distance frame');
  assert(Math.abs(context.playbackDistanceAtPosition(sparseLeg, cumulative, expected, offset) - expectedDistance) < 1e-8);
  assert.strictEqual(context.locatePlaybackPosition(sparseLeg, cumulative, expected, expectedDistance + 1), null,
    'Minimum playback distance rejects an earlier cut position');
  const anchoredRoute = {trajectory: sparseLeg.map(point => point.slice()), anim_frame: {
    trajectory: sparseLeg.map(point => point.slice()), cumulative_distances: cumulative.slice(),
    waypoint_indices: {A1: 0, A2: 1}, altitudes: [1000, 2000],
  }};
  const anchoredDrone = {cumulativeDistances: cumulative.slice(), distanceTraveled: offset};
  context.anchorPlaybackCheckpoint(anchoredRoute, anchoredDrone, expected);
  assert.strictEqual(anchoredRoute.trajectory.length, 3, 'Saved interior cut becomes an explicit route vertex');
  closePoint(anchoredRoute.trajectory[1], expected, 'Anchored cut retains its saved great-circle coordinate');
  assert(Math.abs(anchoredDrone.cumulativeDistances[1] - expectedDistance) < 1e-8,
    'Anchored cut retains its correct playback distance');
  assert.strictEqual(anchoredDrone.cumulativeDistances[2], cumulative[1],
    'Anchoring does not change total playback distance');
  assert(Math.abs(anchoredRoute.anim_frame.altitudes[1] - (1000 + fraction * 1000)) < 1e-6,
    'Altitude interpolation uses the same recovered geographic fraction');
  assert.strictEqual(anchoredRoute.anim_frame.waypoint_indices.A2, 2);
  context.anchorPlaybackCheckpoint(anchoredRoute, anchoredDrone, expected);
  assert.strictEqual(anchoredRoute.trajectory.length, 3, 'Re-anchoring is idempotent');
}

let largestInteriorError = 0;
let largestCssError = 0;
for (const mode of ["Map", "Grid"]) {
  for (const zoom of [16, 18]) {
    for (const [width, height] of [[1000, 600], [1800, 450]]) {
      Object.assign(canvasRect, {width, height});
      for (const [fraction, expected] of interior) {
        context.mapState.active = mode === "Map";
        context.viewState.zoomLevel = zoom;
        context.viewState.centerLat = expected[0];
        context.viewState.centerLng = expected[1];
        context.state.env.airports = sparseLeg.map(([lat, lng], i) => ({id: `A${i + 1}`, lat, lng}));
        context.state.routes["1"] = {route: ["A1", "A2"], trajectory: sparseLeg};
        context.state.animation.drones["1"] = {animating: true, progress: fraction,
          distanceTraveled: fraction * legDistance, totalDistance: legDistance,
          cumulativeDistances: [0, legDistance]};
        const split = context.split_polyline_at_distance(sparseLeg, fraction * legDistance);
        closePoint(split.splitPoint, expected, `${mode} z${zoom}: cut uses independent great-circle position`);
        frozen["1"] = split.prefixPoints;
        checkpointMarkers.set("cut", {pos: split.splitPoint, label: "C1", isLost: false});
        context.drawEnvironment();
        const marker = fills.find(fill => fill.type === "drone");
        const path = routeStrokes().at(-1);
        const expectedPixel = context.mapPointToCanvas(elements["env-canvas"], ...expected);
        assert(Math.hypot(marker.center[0] - expectedPixel[0], marker.center[1] - expectedPixel[1]) < 0.01,
          `${mode} z${zoom} t${fraction}: live marker follows great circle`);
        const error = distanceToDrawnPath(marker.center, path);
        largestInteriorError = Math.max(largestInteriorError, error);
        assert(error <= 1, `${mode} z${zoom} t${fraction}: marker is ${error.toFixed(3)} px from its rendered route`);
        const cssPoint = [marker.center[0] * width / 1000, marker.center[1] * height / 600];
        const cssPath = {commands: path.commands.map(([kind, x, y]) => [kind, x * width / 1000, y * height / 600])};
        const cssError = distanceToDrawnPath(cssPoint, cssPath);
        largestCssError = Math.max(largestCssError, cssError);
        assert(cssError <= 0.25, `${mode} z${zoom} t${fraction}: responsive route differs by ${cssError.toFixed(3)} CSS px`);
        closePoint(marker.center, cutMarkers[0].center, `${mode}: checkpoint and live marker agree at interior point`);
        closePoint(marker.center, routeStrokes()[0].points.at(-1), `${mode}: frozen prefix meets checkpoint marker`);
      }
    }
  }
}

// Loiter uses repeated coordinates with nonzero cumulative time/distance. The
// great-circle interpolation must preserve those stationary intervals exactly.
delete frozen["1"];
checkpointMarkers.clear();
context.state.routes["1"] = {route: ["A1", "A2"], trajectory: [sparseLeg[0], sparseLeg[0], sparseLeg[1]]};
context.state.animation.drones["1"] = {animating: true, progress: 0.05,
  distanceTraveled: 5, totalDistance: 10 + legDistance, cumulativeDistances: [0, 10, 10 + legDistance]};
context.drawEnvironment();
closePoint(fills.find(fill => fill.type === "drone").center,
  context.mapPointToCanvas(elements["env-canvas"], ...sparseLeg[0]), 'Loiter remains at its vertex');
context.state.animation.drones["1"].distanceTraveled = 10 + legDistance;
context.drawEnvironment();
closePoint(fills.find(fill => fill.type === "drone").center,
  context.mapPointToCanvas(elements["env-canvas"], ...sparseLeg[1]), 'Arrival remains exactly at destination');

// Cached display geometry must follow view changes and in-place trajectory
// edits made by checkpoint anchoring. Exercise rendered output, not cache state.
Object.assign(canvasRect, {width: 1000, height: 600});
context.state.animation.drones = {};
context.state.env.airports = [];
const mutableTrajectory = [sparseLeg[0].slice(), [60.35, -64.72], sparseLeg[1].slice()];
context.state.routes['1'] = {route: ['A1', 'A2'], trajectory: mutableTrajectory};
Object.assign(context.viewState, {zoomLevel: 8, centerLat: 60.3, centerLng: -64.7});
context.drawEnvironment();
const lowZoom = routeStrokes()[0];
context.viewState.zoomLevel = 18;
context.drawEnvironment();
const highZoom = routeStrokes()[0];
assert(highZoom.points.length > lowZoom.points.length,
  'Zooming in must refine cached geographic display samples');

function assertCurrentCurve(stroke, message) {
  for (let i = 1; i < mutableTrajectory.length; i++) {
    for (const fraction of [0.37, 0.61, 0.83]) {
      const point = context.interpolateFlightPosition(mutableTrajectory[i - 1], mutableTrajectory[i], fraction);
      const pixel = context.mapPointToCanvas(elements['env-canvas'], ...point);
      const error = distanceToDrawnPath(pixel, stroke);
      assert(error <= 0.25, `${message}: leg ${i} t${fraction} differs by ${error.toFixed(3)} px`);
    }
  }
}
assertCurrentCurve(highZoom, 'Zoom invalidates cached sampling resolution');
const oldCenter = context.mercatorPoint(context.viewState.centerLat, context.viewState.centerLng, 18);
context.viewState.centerLat += 0.05;
context.viewState.centerLng -= 0.02;
const newCenter = context.mercatorPoint(context.viewState.centerLat, context.viewState.centerLng, 18);
context.drawEnvironment();
const panned = routeStrokes()[0];
assert.strictEqual(panned.points.length, highZoom.points.length, 'Panning preserves geographic sampling');
panned.points.forEach((point, index) => closePoint(point,
  [highZoom.points[index][0] + oldCenter[0] - newCenter[0],
    highZoom.points[index][1] + oldCenter[1] - newCenter[1]],
  'Cached route must reproject every point after panning'));
assertCurrentCurve(panned, 'Panning preserves curve alignment');

mutableTrajectory[1][0] -= 0.15;
context.drawEnvironment();
assertCurrentCurve(routeStrokes()[0], 'In-place waypoint edits invalidate cached curve samples');
context.viewState.zoomLevel = 8;
context.drawEnvironment();
assert(routeStrokes()[0].points.length < highZoom.points.length,
  'Zooming back out must use the current route at the new resolution');
assertCurrentCurve(routeStrokes()[0], 'Zooming out after an edit preserves the new curve');

console.log(`PASS: route visibility, migrated Map/Grid alignment, dateline, loiter, great-circle interiors, and cache invalidation; max error ${largestInteriorError.toFixed(3)} canvas px / ${largestCssError.toFixed(3)} CSS px`);

// Optional CPU-only benchmark of the actual renderer with canvas calls recorded
// by the harness. This is not a browser/GPU or frame-rate measurement.
if (process.argv.includes('--benchmark')) {
  const {performance} = require('node:perf_hooks');
  Object.assign(canvasRect, {width: 1000, height: 600});
  const from = [32.8998, -97.0403], to = [25.2532, 55.3657];
  const points = Array.from({length: 201}, (_, i) => context.interpolateFlightPosition(from, to, i / 200));
  const distance = context.haversineKm(...from, ...to);
  context.state.routes['1'] = {route: ['A1', 'A2'], trajectory: points};
  context.state.animation.drones['1'] = {animating: true, progress: 0.5,
    distanceTraveled: distance / 2, totalDistance: distance,
    cumulativeDistances: points.map((_, i) => distance * i / 200)};
  context.state.env.airports = [from, to].map(([lat, lng], i) => ({id: `A${i + 1}`, lat, lng}));
  for (const zoom of [16, 18]) {
    context.viewState.zoomLevel = zoom;
    const durations = [];
    for (let i = 0; i < 11; i++) {
      const start = performance.now();
      context.drawEnvironment();
      durations.push(performance.now() - start);
    }
    const cold = durations.shift();
    const mean = durations.reduce((a, b) => a + b, 0) / durations.length;
    console.log(`CPU renderer benchmark z${zoom}: 201 geographic vertices, ${routeStrokes()[0].points.length} drawn points; first ${cold.toFixed(1)} ms, repeated mean ${mean.toFixed(1)} ms`);
  }
}
