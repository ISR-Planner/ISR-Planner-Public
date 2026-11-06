// Runs the actual Cesium scene builder and Show controls in a Node VM.
// Geometry and camera state must survive hiding a route; Map and 3D must
// reflect the same operator selection after redraws and view changes.
// node tests/e2e/test_route_visibility_3d.js
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const read = (name) => fs.readFileSync(path.join(__dirname, "../../webapp", name), "utf8");
const cesiumSource = read("cesium_view.js");
const plannerSource = read("isr.js");
const renderSource = cesiumSource.slice(
  cesiumSource.indexOf("  function render(opts = {}) {"),
  cesiumSource.indexOf("  function populateFollowDropdown() {")
);
const controlsSource = plannerSource.slice(
  plannerSource.indexOf("function toggleTrajectory(did) {"),
  plannerSource.indexOf("function attachTrajectoryControls() {")
);

const values = [];
const entities = {
  values,
  add(entity) { values.push(entity); return entity; },
  remove(entity) { values.splice(values.indexOf(entity), 1); },
  removeAll() { values.length = 0; },
};
const trajectories = {
  1: [[32.8998, -97.0403], [64, -10], [25.2532, 55.3657]],
  2: [[34, -118], [40, -74]],
};
const frozen = { 1: [[31, -98], [32.8998, -97.0403]] };
const context = {
  state: {
    env: {},
    routes: Object.fromEntries(Object.entries(trajectories).map(([did, trajectory]) => [did, { trajectory }])),
    trajectoryVisible: { 1: true, 2: true },
  },
  viewer: { entities, zoomTo() { throw new Error("visibility redraw must preserve the camera"); } },
  framedSig: "same-mission",
  sceneSignature: () => "same-mission",
  droneMarkers: {}, droneLastPosition: {}, droneLastForward: {}, droneCamForward: {}, droneCamTime: {},
  routeCartesiansByDrone: {}, followedDroneId: "1",
  missionReplay: { getAllFrozenTrajectories: () => frozen },
  buildRoutePositions: (trajectory, altitude) => trajectory.flatMap(([lat, lng]) => [lng, lat, altitude]),
  densifyTrajectory: trajectory => trajectory,
  droneCruiseAltM: () => 18000,
  droneColor: () => ({ withAlpha: () => "dimmed" }),
  Cesium: {
    Cartesian3: { fromDegreesArrayHeights: (points) => points.slice() },
    ArcType: { GEODESIC: "geodesic", NONE: "none" },
  },
  // This source-extraction harness tests scene geometry; full lifecycle dispatch has its own checks.
  notifyLifecycle() {},
  populateFollowDropdown() {}, updateTrajectoryButtonStates() {}, appendDebugLine() {},
};
vm.createContext(context);
vm.runInContext(renderSource + "\n" + controlsSource, context);
context.drawEnvironment = () => context.render();
const entity = (name) => values.find((item) => item.name === name);
const routeVisible = (did) => entity(`D${did} route`).show;
const geometry = () => JSON.stringify(context.routeCartesiansByDrone);

context.render();
assert.strictEqual(routeVisible(1), true);
assert.strictEqual(routeVisible(2), true);
assert.strictEqual(entity("D1 flown").show, true);
const initialGeometry = geometry();

// Hiding one drone must not hide its peers or discard the camera's route.
const marker = { name: "D1 marker" };
context.droneMarkers[1] = marker;
entities.add(marker);
context.droneLastPosition[1] = { x: 1, y: 2, z: 3 };
const lastPosition = context.droneLastPosition[1];
context.toggleTrajectory("1");
assert.strictEqual(routeVisible(1), false);
assert.strictEqual(entity("D1 flown").show, false);
assert.strictEqual(routeVisible(2), true);
assert.strictEqual(geometry(), initialGeometry);
assert(values.includes(marker), "visibility redraw keeps the animation marker entity");
assert.strictEqual(context.droneLastPosition[1], lastPosition);
assert.strictEqual(context.followedDroneId, "1");
context.render();
assert.strictEqual(routeVisible(1), false, "a later scene redraw must preserve the hidden selection");

// Show all, then Hide all, operate on both current and flown 3D paths.
context.toggleAllTrajectories();
assert.strictEqual(routeVisible(1), true);
assert.strictEqual(routeVisible(2), true);
assert.strictEqual(entity("D1 flown").show, true);
context.toggleAllTrajectories();
assert.strictEqual(routeVisible(1), false);
assert.strictEqual(routeVisible(2), false);
assert.strictEqual(entity("D1 flown").show, false);
assert.strictEqual(geometry(), initialGeometry);

// Match Map's existing distinction: absent Show flags hide current routes,
// while flown history is displayed unless explicitly hidden.
context.state.trajectoryVisible = {};
context.render();
assert.strictEqual(routeVisible(1), false);
assert.strictEqual(routeVisible(2), false);
assert.strictEqual(entity("D1 flown").show, true);
context.toggleTrajectory("1");
assert.strictEqual(routeVisible(1), true);
assert.strictEqual(routeVisible(2), false);

console.log("PASS: 3D Show controls, flown history, repeated redraws, route geometry and camera preservation");

// Exercise the real Map playback -> Cesium marker path and real 3D route
// builder. The previous latitude/longitude lerp missed this segment's curved
// route by 165 metres halfway through, then rejoined it at each vertex.
function extractFunction(source, name, indent = "") {
  const start = source.indexOf(`${indent}function ${name}(`);
  assert(start >= 0, `${name} must exist`);
  return source.slice(start, source.indexOf(`\n${indent}}`, start) + indent.length + 2);
}
const mapCanvas = {width: 800, height: 800, getContext: () => new Proxy({}, {
  get: (target, name) => name in target ? target[name] : () => {},
  set: (target, name, value) => { target[name] = value; return true; },
})};
const elements = {"env-canvas": mapCanvas};
Object.assign(context, {
  $: id => elements[id] || null,
  viewState: {centerLat: 60.28, centerLng: -64.72, zoomLevel: 16},
  mapState: {active: true}, _drawEnvFrameCount: 0,
  MissionMode: {ANIMATING: "ANIMATING"}, missionState: {mode: "IDLE"},
  syncTankerAltBox() {}, renderMapLegend() {}, updateCategoryCounts() {},
  drawTimeSyncMarkers() {}, collectCheckpointMarkers: () => new Map(),
  getEditableEnv: () => context.state.env,
  applyChaseCam() { context.followCalls++; }, followCalls: 0,
});
Object.assign(context.state, {
  env: {airports: [], targets: [], sams: []},
  droneConfigs: {"1": {cruise_altitude_km: 16.8}},
  visitedTargets: [], checkpoint: {active: false},
  animation: {active: false, drones: {}}, trajectoryVisible: {"1": true},
});
context.missionReplay.getSegmentCount = () => 1;
delete frozen[1];
context.Cesium.Cartesian3.fromDegrees = (lng, lat, altitude) => ({lng, lat, altitude});
context.Cesium.Cartesian2 = function (x, y) { this.x = x; this.y = y; };
context.Cesium.Color = {WHITE: "white", BLACK: "black"};
context.Cesium.LabelStyle = {FILL_AND_OUTLINE: "fill-and-outline"};
context.Cesium.VerticalOrigin = {BOTTOM: "bottom"};
const contractStart = plannerSource.indexOf('// SINGLE-WORLD COORDINATE CONTRACT');
const contractEnd = plannerSource.indexOf('// END SINGLE-WORLD COORDINATE CONTRACT', contractStart);
assert(contractStart >= 0 && contractEnd > contractStart);
vm.runInContext(read("latlng_utils.js") + "\n" + plannerSource.slice(contractStart, contractEnd) +
  "\n" + extractFunction(plannerSource, "drawEnvironment"), context);
const geometryFunctions = ["trajectoryDistanceKm", "geodesicInterpolate", "densifyTrajectory", "flightProfile",
  "profileAltitudeM", "geodesicReinterpolate", "buildRoutePositions", "updateDronePosition"];
// Exercise Cesium's real route and marker helpers in their lexical scope.
vm.runInContext(`Object.assign(globalThis, (() => {
  ${geometryFunctions.map(name => extractFunction(cesiumSource, name, "  ")).join("\n")}
  return {buildRoutePositions, updateDronePosition, densifyTrajectory};
})());`, context);
context.cesiumView = {
  isActive: () => true, render: context.render,
  updateDronePosition: (...args) => context.updateDronePosition(...args),
};

const a = [60.10453860289056, -65.18062433043991];
const b = [60.45988446087058, -64.25361392871031];
const span = 64.7633;
const profile = {trajectory: [a, b], altitudes: [16.8, 16.8], cumulative_distances: [0, span]};
context.state.routes = {"1": {route: ["A1", "A2"], trajectory: [a, b], anim_frame: profile}};
context.state.animation.drones[1] = {animating: true, progress: 0, distanceTraveled: 0,
  cumulativeDistances: [0, span], totalDistance: span};
context.droneMarkers = {};
context.render();
const renderedRoute = entity("D1 route").polyline;
assert.strictEqual(renderedRoute.arcType, "none", "Cesium draws the sampled flight curve without replacing its arc model");
const flat = renderedRoute.positions;
assert(flat.length > 150, "the real builder samples the long frame interval at <=1 km");
const close = (actual, expected, message) => assert(Math.abs(actual - expected) < 1e-9, message);
function routePositionAtFraction(f) {
  const segments = flat.length / 3 - 1;
  const i = Math.min(Math.floor(f * segments), segments - 1);
  const start = [flat[i * 3 + 1], flat[i * 3]];
  const end = [flat[(i + 1) * 3 + 1], flat[(i + 1) * 3]];
  return context.interpolateFlightPosition(start, end, f * segments - i);
}
const midpoint = routePositionAtFraction(0.5);
close(midpoint[1], -64.71963726462079, "static route uses the known backend great-circle midpoint longitude");
close(midpoint[0], 60.283018668928364, "static route uses the known backend great-circle midpoint latitude");

for (const f of [0, 0.25, 0.5, 0.75, 1]) {
  const drone = context.state.animation.drones[1];
  drone.progress = f; drone.distanceTraveled = span * f;
  context.drawEnvironment(true);
  const marker = entity("D1 marker").position;
  // Locate the same fraction on the actual static builder's subdivided path.
  const point = routePositionAtFraction(f);
  close(marker.lat, point[0], `Map -> 3D marker latitude follows the route at ${f}`);
  close(marker.lng, point[1], `Map -> 3D marker longitude follows the route at ${f}`);
  close(marker.altitude, 16800, `marker altitude follows the same profile at ${f}`);
}
assert.strictEqual(context.followCalls, 5, "Follow still receives each marker update");

// Quantify the real Cartesian chord error, including WGS84 conversion and
// altitude, instead of only checking that all consumers name the same helper.
function cartesian([lat, lng], altitude) {
  const phi = lat * Math.PI / 180, lambda = lng * Math.PI / 180;
  const e2 = 6.6943799901413165e-3;
  const n = 6378137 / Math.sqrt(1 - e2 * Math.sin(phi) ** 2);
  return [(n + altitude) * Math.cos(phi) * Math.cos(lambda),
    (n + altitude) * Math.cos(phi) * Math.sin(lambda),
    (n * (1 - e2) + altitude) * Math.sin(phi)];
}
for (let i = 3; i < flat.length; i += 3) {
  const p = [flat[i - 2], flat[i - 3]], q = [flat[i + 1], flat[i]];
  assert(context.haversineKm(...p, ...q) <= 1 + 1e-9, "rendering segments stay within 1 km");
  const start = cartesian(p, flat[i - 1]), end = cartesian(q, flat[i + 2]);
  const curve = cartesian(context.interpolateFlightPosition(p, q, 0.5), (flat[i - 1] + flat[i + 2]) / 2);
  const error = Math.hypot(...curve.map((coordinate, axis) => coordinate - (start[axis] + end[axis]) / 2));
  assert(error < 0.025, `Cartesian line stays within 2.5 cm of the marker curve, got ${error} m`);
}
frozen[1] = [a, b];
context.render();
const flown = entity("D1 flown").polyline;
assert.strictEqual(flown.arcType, "none");
assert.strictEqual(flown.positions.length, flat.length, "frozen history uses the same flight-curve samples");
for (let i = 0; i < flat.length; i++) {
  if (i % 3 !== 2) close(flown.positions[i], flat[i], "frozen and current ground tracks coincide");
}
for (const altitudes of [[0, 6.8], [6.8, 0]]) {
  profile.altitudes = altitudes;
  context.render();
  const climbingRoute = entity("D1 route").polyline.positions;
  for (const f of [0.25, 0.5, 0.75]) {
    const drone = context.state.animation.drones[1];
    drone.progress = f; drone.distanceTraveled = span * f;
    context.drawEnvironment(true);
    const segments = climbingRoute.length / 3 - 1;
    const index = Math.floor(f * segments), fraction = f * segments - index;
    const routeAltitude = climbingRoute[index * 3 + 2] * (1 - fraction) +
      climbingRoute[(index + 1) * 3 + 2] * fraction;
    close(entity("D1 marker").position.altitude, routeAltitude,
      `marker and sampled route share the climb/descent profile at ${f}`);
  }
}
console.log("PASS: actual Map playback and Cesium route/marker share one flight curve through every segment fraction");
