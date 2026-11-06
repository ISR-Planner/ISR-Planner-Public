// Offline integration: real Python solver/impact + production JS clock, pause,
// checkpoint and weather controller. Synthetic mission only; no network/browser.
// Run: node tests/e2e/test_weather_detour_checkpoint.js (requires venv dependencies).
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const source = fs.readFileSync('webapp/isr.js', 'utf8');
const geometry = fs.readFileSync('webapp/latlng_utils.js', 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
function extract(text, name) {
  const start = text.indexOf(`function ${name}(`);
  assert(start >= 0, name);
  return text.slice(start, text.indexOf('\n}', start) + 2);
}
function backend(operation, args) {
  return JSON.parse(execFileSync(process.env.ISR_TEST_PYTHON || 'venv/bin/python',
    ['-m', 'work.diagnostics.weather_backend_worker'], {
      input: JSON.stringify({operation, arguments: args}) + '\n',
      encoding: 'utf8', maxBuffer: 10 * 1024 * 1024, timeout: 30000,
    }));
}
const origin = Date.parse('2026-10-12T00:00:00Z');
const boundary = origin + 5 * 60000, end = origin + 4 * 3600000;
const cell = {id: 'synthetic-storm', rule: 'No fly', validFrom: new Date(origin).toISOString(),
  validTo: new Date(boundary).toISOString(), geometry: {type: 'Polygon', coordinates: [[
    [-97.2, 31.8], [-96.8, 31.8], [-96.8, 32.2], [-97.2, 32.2], [-97.2, 31.8],
  ]]}};
const env = {
  airports: [{id: 'A1', lat: 32, lng: -98}, {id: 'A2', lat: 32, lng: -96}],
  targets: [{id: 'T1', lat: 32, lng: -97.7, priority: 5}, {id: 'T2', lat: 32, lng: -96.3, priority: 5}],
  sams: [],
};
const configs = {'1': {enabled: true, start_airport: 'A1', end_airport: 'A2', fuel_budget: 1000, speed: 10}};
const weatherEnv = clone(env);
weatherEnv.sams = [{id: 'WX-synthetic', center: {lat: 31.8, lng: -97.2}, range: 0, range_km: 0,
  weather_ring: clone(cell.geometry.coordinates[0])}];
const initial = backend('solve', {env: weatherEnv, drone_configs: configs,
  allocation_strategy: 'geographic', use_sam_aware_distances: true, post_optimize: false});
assert(initial.success, JSON.stringify(initial.errors));
const route = initial.routes['1'];
assert.deepEqual(route.route, ['A1', 'T1', 'T2', 'A2'], 'both targets already scheduled despite detour');
const helperStart = source.indexOf('const ANIMATION_BASE_MISSION_SECONDS_PER_WALL_SECOND');
const helpers = source.slice(helperStart, source.indexOf('// Killed drones', helperStart));

async function scenario(pattern, retrievedAt) {
  let frame, cuts = 0, pauses = 0, replans = 0, review, solveFailure;
  const elements = {}, logs = [], requests = [], impacts = [];
  const elapsed = (boundary - origin) / 1000 - 1;
  const distance = elapsed / 60 * configs['1'].speed;
  const drone = {animating: true, distanceTraveled: distance, legBurnedDistance: distance,
    totalDistance: route.anim_frame.total_distance,
    cumulativeDistances: clone(route.anim_frame.cumulative_distances)};
  const data = {cells: [cell], retrievedAt};
  const context = {
    window: {}, document: {getElementById: id => elements[id] ||= {checked: true, textContent: ''}},
    state: {env: clone(env), initialEnvSnapshot: clone(env), droneConfigs: clone(configs),
      routes: {'1': {...clone(route), trajectory: clone(route.anim_frame.trajectory),
        waypoint_indices: clone(route.anim_frame.waypoint_indices)}},
      visitedTargets: ['T1'], legFuel: {'1': distance}, legFuelHistory: {},
      animation: {active: false, speedMultiplier: 1, elapsedMissionSeconds: elapsed,
        missionDistance: distance, drones: {'1': drone}}},
    missionState: {pauseContext: {droneStates: {'1': drone}, animatingDrones: ['1']}},
    MissionMode: {ANIMATING: 'ANIMATING', PAUSED_MID_ANIMATION: 'PAUSED', CHECKPOINT: 'CHECKPOINT'},
    missionReplay: {getSegmentCount: () => 1, getCurrentSegmentIndex: () => 0,
      getSegment: () => ({env, solution: initial})}, segmentedImport: {isActive: () => false},
    Date, JSON, setInterval() {}, requestAnimationFrame: callback => { frame = callback; return 1; },
    cancelAnimationFrame() {}, appendDebugLine: text => logs.push(text), drawEnvironment() {},
    requestDraw() {}, updateStatsFromAnimation() {}, updateAnimationButtonStates() {},
    updateEnvPanelLocked() {}, updateCanvasPointerEvents() {}, setAddMode() {},
    getUiPermissions: () => ({canPause: true}),
    setMissionMode: mode => { context.missionState.mode = mode; },
    readDroneConfigsFromUI: () => clone(context.state.droneConfigs),
    markTargetVisitedByDrone: id => {
      if (!context.state.visitedTargets.includes(id)) context.state.visitedTargets.push(id);
    },
    fetch: async (url, options) => {
      assert.equal(url, '/api/weather/impact', 'only the actual offline impact endpoint adapter is used');
      const request = JSON.parse(options.body); requests.push(request);
      const impact = backend('impact', request); impacts.push(impact);
      return {ok: true, json: async () => impact};
    },
  };
  context.window.weatherDataSource = {current: async () => data};
  vm.createContext(context);
  vm.runInContext(geometry.match(/const EARTH_RADIUS_KM = [^;]+;/)[0] + '\nconst TARGET_HIT_KM=.05;\n' +
    ['haversineKm', 'interpolateFlightPosition', 'isTargetVisited'].map(n => extract(geometry, n)).join('\n') + '\n' +
    ['interpolateLongitude', 'split_polyline_at_distance', 'targetHitToleranceKm', 'getAllKnownTargets',
      'pauseAnimation', 'applyResumeNodeToDroneConfig', 'buildCheckpointEnv', 'buildCheckpointRepairRoutes',
      'prepareCutFromCurrentState', 'resumeAnimation'].map(n =>
      (n === 'prepareCutFromCurrentState' ? 'async ' : '') + extract(source, n)).join('\n') + '\n' + helpers, context);
  const realCut = context.prepareCutFromCurrentState, realPause = context.pauseAnimation;
  context.prepareCutFromCurrentState = async () => { cuts++; return realCut(); };
  context.pauseAnimation = () => { pauses++; return realPause(); };
  vm.runInContext(fs.readFileSync('webapp/weather_checkpoints.js', 'utf8'), context);
  const controller = context.window.weatherCheckpoints;
  controller.configure({snapshot: () => ({dirty: false}),
    epoch: () => origin + context.state.animation.elapsedMissionSeconds * 1000, end: () => end,
    data: () => data, refreshAt: async () => {}, recordReview: value => { review = value; },
    refreshAndReplan: async epoch => {
      try {
        replans++;
        const request = context.buildCheckpointEnv();
        const result = backend('solve', {env: request.env, drone_configs: request.droneConfigs,
          visited_targets: context.state.visitedTargets, is_checkpoint_replan: request.isCheckpointReplan,
          repair_routes: context.buildCheckpointRepairRoutes(request.droneConfigs), allocation_strategy: 'geographic',
          use_sam_aware_distances: true, post_optimize: false});
        assert(result.success, JSON.stringify(result.errors));
        context.missionState.draftSolution = result;
        // The refreshed planning window excludes the expired advisory, as the
        // real preview does before arming the revised draft.
        controller.arm(data.cells.filter(c => Date.parse(c.validTo) > epoch));
        return true;
      } catch (error) { solveFailure = error; throw error; }
    },
  });
  controller.arm(data.cells);
  context.resumeAnimation(); frame(0);
  for (let tick = 1; tick <= 20 && context.state.animation.active; tick++) {
    context.state.animation.speedMultiplier = pattern[(tick - 1) % pattern.length];
    frame(tick * 100);
    await controller.inspect();
  }
  if (solveFailure) throw solveFailure;
  assert.equal(cuts, 1, logs.join('\n'));
  assert.equal(pauses, 1); assert.equal(replans, 1);
  assert.equal(context.state.animation.active, false, 'draft is left paused for review and Accept');
  assert.equal(context.state.animation.elapsedMissionSeconds, (boundary - origin) / 1000,
    'high multiplier stops at exact weather boundary');
  assert.deepEqual(requests[0].missed_targets, [], 'no missed target supplies the trigger');
  assert.equal(requests[0].remaining_legs['1'].length, 2, 'remaining T1→T2 and T2→A2 legs retain target boundary');
  assert(impacts[0].releasedDetours.includes('1'), 'real backend detects the released detour');
  assert(impacts[0].affected && impacts[0].canReplan);
  const future = Date.parse(retrievedAt) + 60000 < boundary;
  assert.equal(review.requiresWeatherReview, future);
  assert.match(elements['wp-checkpoint-status'].textContent, /Review, Accept/);
  if (future) assert.match(elements['wp-checkpoint-status'].textContent, /Weather review required/);
  assert.equal(context.state.pendingCheckpoints['1'].id, 'C1-1');
  assert.deepEqual(context.state.visitedTargets, ['T1'], 'visited target preserved');
  const revised = context.missionState.draftSolution.routes['1'];
  assert.deepEqual(revised.route, ['C1-1', 'T2', 'A2'], 'remaining target retained in actual replan');
  const remainingBefore = route.anim_frame.total_distance - context.state.animation.drones['1'].distanceTraveled;
  assert(revised.anim_frame.total_distance < remainingBefore - 1, 'actual solver removes at least 1 km of detour');
  assert(Math.abs(context.state.droneConfigs['1'].start_fuel -
    (configs['1'].fuel_budget - context.state.legFuel['1'])) < 1e-7, 'fuel preserved at cut');
  // Simulate accepting the new draft, and inspect even though nothing new
  // happened: fresh arm must not generate another diamond for the same expiry.
  context.state.routes['1'] = {...clone(revised), trajectory: clone(revised.anim_frame.trajectory),
    waypoint_indices: clone(revised.anim_frame.waypoint_indices)};
  context.state.animation.drones['1'] = {animating: true, distanceTraveled: 0,
    totalDistance: revised.anim_frame.total_distance,
    cumulativeDistances: clone(revised.anim_frame.cumulative_distances)};
  context.state.animation.active = true;
  await controller.inspect(true); await controller.inspect(true);
  assert.equal(cuts, 1); assert.equal(replans, 1); assert.equal(pauses, 1);
  assert(impacts.slice(1).every(impact => !impact.affected), 'same expired cell no longer changes revised route');
  return {speed: pattern, cuts, replans, weatherReview: future,
    savedKm: Number((remainingBefore - revised.anim_frame.total_distance).toFixed(2))};
}

(async () => {
  const rows = [];
  for (const pattern of [[12800], [1, 12800, 4]]) rows.push(await scenario(pattern, new Date(origin).toISOString()));
  rows.push(await scenario([12800], new Date(boundary).toISOString()));
  console.log(JSON.stringify({initialRoute: route.route, results: rows}, null, 2));
  console.log('Weather detour expiry: actual solver, impact, clock, cut and revised draft pass.');
})().catch(error => { console.error(error); process.exitCode = 1; });
