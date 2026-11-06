// Weather impact requests must describe the same remaining flight as playback.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('webapp/isr.js', 'utf8');
const geometry = fs.readFileSync('webapp/latlng_utils.js', 'utf8');
const copy = value => JSON.parse(JSON.stringify(value));
function extract(text, name) {
  const start = text.indexOf('function ' + name + '(');
  assert(start >= 0, name);
  let depth = 0;
  for (let i = text.indexOf(') {', start) + 2; i < text.length; i++) {
    if (text[i] === '{') depth++;
    if (text[i] === '}' && --depth === 0) return text.slice(start, i + 1);
  }
  throw Error(name);
}
function harness(route, cumulative, distance, impact = {affected: false, canReplan: true}) {
  const requests = [], elements = {}, epoch = Date.parse('2026-10-13T01:00:00Z');
  let cuts = 0;
  const context = {
    window: {weatherDataSource: {current: async () => ({cells: [], retrievedAt: new Date(epoch).toISOString()})}},
    state: {routes: {'1': copy(route)}, animation: {active: true, drones: {'1': {
      animating: true, distanceTraveled: distance, totalDistance: cumulative?.at(-1) ?? 1000,
      cumulativeDistances: cumulative && [...cumulative],
    }}}, env: {targets: []}, visitedTargets: []},
    document: {getElementById: id => elements[id] ??= {checked: true, textContent: ''}},
    setInterval() {}, Date, JSON,
    pauseAnimation() { context.state.animation.active = false; },
    async prepareCutFromCurrentState() { cuts++; }, drawEnvironment() {}, appendDebugLine() {},
    async fetch(url, init) {
      assert.equal(url, '/api/weather/impact');
      requests.push(JSON.parse(init.body));
      return {ok: true, json: async () => impact};
    },
  };
  vm.createContext(context);
  vm.runInContext(geometry.match(/const EARTH_RADIUS_KM = [^;]+;/)[0] + '\n' +
    extract(geometry, 'haversineKm') + '\n' + extract(geometry, 'interpolateFlightPosition') + '\n' +
    extract(source, 'split_polyline_at_distance'), context);
  vm.runInContext(fs.readFileSync('webapp/weather_checkpoints.js', 'utf8'), context);
  const config = {snapshot: () => ({}), epoch: () => epoch, end: () => epoch + 3600000,
    data: () => ({cells: [], retrievedAt: new Date(epoch).toISOString()}), refreshAt: async () => {}};
  const controller = context.window.weatherCheckpoints;
  controller.configure(config); controller.arm([]);
  return {context, controller, config, requests, impact, cuts: () => cuts};
}
const holdingRoute = {trajectory: [[0, 0], [0, .1], [0, .1], [0, .3]],
  route: ['A1', 'T1', 'A2'], waypoint_indices: {0: 0, 1: 1, 2: 3}};
const holdingDistances = [0, 11.1195, 111.1195, 133.3585];
async function holdingPosition() {
  const h = harness(holdingRoute, holdingDistances, 50);
  await h.controller.inspect(true);
  assert.deepEqual(h.requests[0].remaining_routes, {'1': [[0, .1], [0, .1], [0, .3]]});
  assert.deepEqual(h.requests[0].remaining_legs, {'1': [[[0, .1], [0, .1], [0, .3]]]});
  assert.equal(h.cuts(), 0);
  console.log('PASS weather suffix stays at the actual loiter position and retains the remaining hold');
}
async function scaledFlight() {
  const route = {trajectory: [[0, 0], [0, 2], [0, 4]], route: ['A1', 'T1', 'A2'],
    waypoint_indices: {0: 0, 1: 1, 2: 2}};
  const h = harness(route, [0, 1000, 2000], 500);
  await h.controller.inspect(true);
  const p = h.requests[0];
  assert(Math.abs(p.remaining_routes['1'][0][1] - 1) < 1e-12);
  assert.deepEqual(p.remaining_routes['1'].slice(1), [[0, 2], [0, 4]]);
  assert.equal(p.remaining_legs['1'].length, 2);
  assert.deepEqual(p.remaining_legs['1'][0].at(-1), [0, 2]);
  assert.deepEqual(p.remaining_legs['1'][1], [[0, 2], [0, 4]]);
  console.log('PASS solver-scaled playback distance uses its authoritative interpolation fraction');
}
async function requiredStopsAndRevisits() {
  const path = [[0, 0], [1, 1], [0, 2], [1, 3], [0, 4], [1, 3], [0, 2], [1, 1], [0, 0]];
  const route = {trajectory: path, route: ['A1', 'T1', 'A2', 'T1', 'A1'],
    waypoint_indices: {0: 0, 1: 2, 2: 4, 3: 6, 4: 8}};
  for (const distance of [50, 200, 250, 450]) {
    const h = harness(route, path.map((_, i) => i * 100), distance);
    h.context.state.visitedTargets = ['T1'];
    await h.controller.inspect(true);
    const legs = h.requests[0].remaining_legs['1'];
    const nextAnchor = distance < 200 ? 2 : distance < 400 ? 4 : 6;
    assert.equal(legs.length, (10 - nextAnchor) / 2);
    const suffix = h.requests[0].remaining_routes['1'];
    assert.deepEqual(legs[0][0], suffix[0]);
    for (let i = 0; i < legs.length; i++) {
      const anchor = nextAnchor + i * 2;
      assert.deepEqual(legs[i].at(-1), path[anchor]);
      if (i) assert.deepEqual(legs[i], path.slice(anchor - 2, anchor + 1));
    }
  }
  console.log('PASS semantic legs keep target, refuel, repeated target and return-airport occurrences separate');
}
async function uncertainMetadata() {
  const invalid = [undefined, {A1: 0, T1: 1, A2: 3}, {0: 0, 2: 3}, {0: 0, 1: 3, 2: 1},
    {0: 0, 1: 1.5, 2: 3}, {0: 0, 1: 1, 2: 4}, {0: 0, 1: 1, 2: 2}, {0: 0, 1: 0, 2: 3}];
  for (const metadata of invalid) {
    const h = harness({...holdingRoute, waypoint_indices: metadata}, holdingDistances, 50);
    await h.controller.inspect(true);
    assert.deepEqual(h.requests[0].remaining_routes['1'], [[0, .1], [0, .1], [0, .3]]);
    assert.deepEqual(h.requests[0].remaining_legs, {});
  }
  for (const cumulative of [undefined, [0, 10], [0, 20, 10, 100]]) {
    const h = harness(holdingRoute, cumulative, 1);
    await h.controller.inspect(true);
    assert.deepEqual(h.requests[0].remaining_routes['1'], holdingRoute.trajectory);
    assert.deepEqual(h.requests[0].remaining_legs, {});
  }
  console.log('PASS uncertain waypoint metadata omits shortcut legs; missing frames retain the full conservative path');
}
async function frozenPendingPaths() {
  const h = harness(holdingRoute, holdingDistances, 50,
    {affected: true, canReplan: false, reasons: ['Weather review needed.']});
  h.config.refreshAt = async () => {
    h.context.state.routes['1'].trajectory[1][1] = 77;
    h.context.state.routes['1'].trajectory[3][1] = 88;
    h.context.state.animation.drones['1'].distanceTraveled = 120;
  };
  await h.controller.inspect(true);
  assert.equal(h.cuts(), 1);
  assert.equal(h.context.state.animation.active, false);
  assert.equal(h.requests.length, 2);
  assert.deepEqual(h.requests[1].remaining_routes, h.requests[0].remaining_routes);
  assert.deepEqual(h.requests[1].remaining_legs, h.requests[0].remaining_legs);
  h.impact.canReplan = true;
  assert.equal(await h.controller.allowSolve(), true);
  assert.deepEqual(h.requests[2].remaining_routes, h.requests[0].remaining_routes);
  assert.deepEqual(h.requests[2].remaining_legs, h.requests[0].remaining_legs);
  assert.equal(h.cuts(), 1);
  console.log('PASS a pending weather checkpoint freezes both route and semantic legs across refresh/retry');
}
(async () => {
  await holdingPosition(); await scaledFlight(); await requiredStopsAndRevisits();
  await uncertainMetadata(); await frozenPendingPaths();
})().catch(error => {console.error(error); process.exitCode = 1;});
