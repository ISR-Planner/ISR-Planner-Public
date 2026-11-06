// Production lifecycle/controller functions with offline DOM and network adapters.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('webapp/isr.js', 'utf8');
function extract(name) {
  const start = source.indexOf('function ' + name + '(');
  assert(start >= 0, name);
  let depth = 0;
  for (let i = source.indexOf(') {', start) + 2; i < source.length; i++) {
    if (source[i] === '{') depth++;
    if (source[i] === '}' && --depth === 0)
      return (source.slice(start - 6, start) === 'async ' ? 'async ' : '') + source.slice(start, i + 1);
  }
  throw Error(name);
}
const copy = value => JSON.parse(JSON.stringify(value));
function harness() {
  const elements = {};
  const initial = {airports: [{id: 'A1'}], targets: [{id: 'T1'}], drone_configs: {'1': {fuel_budget: 500}}};
  const solution = {routes: {'1': {route: ['A1', 'T1', 'A1'], trajectory: [[1, 1], [2, 2]]}}};
  let segments = [{env: copy(initial), solution: copy(solution)}];
  let weatherResetCount = 0, weatherRestoreCount = 0;
  const c = {
    state: {env: copy(initial), routes: {}, animation: {active: true, animationId: 7,
      elapsedMissionSeconds: 555, missionComplete: true, continuousReplay: true, drones: {'1': {}}, frameClock: {}},
      droneConfigs: {'1': {start_fuel: 20}}, visitedTargets: ['T1'], checkpoint: {active: true, segments: {'1': {}}},
      pendingCutDistance: 200, initialEnvSnapshot: copy(initial)},
    missionState: {committedSegments: [{old: true}], draftSolution: {old: true}, seg0FullSolution: copy(solution),
      pauseContext: {old: true}, stagedEnv: {}, stagedDroneConfigs: {}, wasAnimating: true},
    missionReplay: {clear() { segments = []; }, resetToStart() {}, getSegmentCount: () => segments.length,
      getSegment: i => segments[i], truncateAfter(i) { segments = segments.slice(0, i + 1); }, setCurrentSegmentIndex() {}},
    segmentStore: {clear() {}}, segmentedImport: {clear() {}, isActive: () => false},
    MissionMode: {IDLE: 'IDLE', READY_TO_ANIMATE: 'READY_TO_ANIMATE', EDITING_ENV: 'EDITING_ENV'},
    DEFAULT_CLIMB_ANGLE_DEG: 5, AbortController,
    $: id => elements[id] ??= {style: {}}, confirm: () => true,
    cancelAnimationFrame() {}, clearInterval() {}, appendDebugLine() {}, updateTimeSyncUi() {},
    clearVisited() { c.state.visitedTargets = []; },
    setMissionMode(mode) { c.missionState.mode = mode; }, getUiPermissions: () => ({canReset: true, canSolve: true}),
    initDroneConfigsFromEnv() { c.state.droneConfigs = c.state.env.drone_configs || {}; },
    isSegmentInfoJson: () => false, isSegmentedMissionJson: () => false,
    restoreTimeSyncOptionsToUI() {}, fillMissingAltitudesEverywhere() {}, updateSamWrappingClientSide() {},
    drawEnvironment() {}, updateAirportDropdowns() {}, updateAnimationButtonStates() {}, updateStatsFromRoutes() {},
    refreshCategoryDisplays() {}, updateMissionControlState() {}, updateButtonStates() {}, refreshDroneConfigUI() {},
    window: {weatherCheckpoints: {reset() { weatherResetCount++; }}, weatherPreview: {resetToStart() {
      assert.equal(c.state.animation.elapsedMissionSeconds, 0);
      assert.equal(c.state.animation.active, false);
      weatherRestoreCount++;
    }}},
  };
  vm.createContext(c);
  for (const name of ['migrateLegacyMapCoordinates', 'invalidateMissionAsyncWork', 'loadFromJson', 'resetMission', 'runPlanner']) vm.runInContext(extract(name), c);
  return {c, initial, solution, segments, elements, weatherCounts: () => [weatherResetCount, weatherRestoreCount]};
}
function importFresh() {
  const h = harness(), {c} = h;
  const abort = new AbortController(); c.state.solveAbortController = abort;
  c.loadFromJson({schema: 'isr_env_v1', is_segmented: false, env: copy(h.initial)}, 'fresh.json');
  assert(abort.signal.aborted);
  assert.equal(c.state.animation.elapsedMissionSeconds, 0);
  assert.equal(c.state.animation.missionComplete, false);
  assert.equal(c.state.animation.frameClock, null);
  assert.equal(c.state.checkpoint.active, false);
  assert.equal(c.state.pendingCutDistance, null);
  assert.equal(c.missionState.committedSegments.length, 0);
  assert.equal(c.missionState.seg0FullSolution, null);
  assert.equal(c.missionState.pauseContext, null);
  assert.equal(c.missionState.acceptedEnv, null);
  assert.equal(c.missionState.mode, 'IDLE');
  c.state.animation.elapsedMissionSeconds = 2000;
  c.loadFromJson({schema: 'isr_env_v1', is_segmented: false, env: {airports: [], targets: []}}, 'different.json');
  assert.equal(c.state.animation.elapsedMissionSeconds, 0);
  assert.equal(c.state.env.targets.length, 0);
  assert.equal(c.missionState.committedSegments.length, 0);
  console.log('PASS repeated imports clear previous clock, route, checkpoint, and acceptance history');
}
function resetBranches() {
  for (const multiple of [false, true]) {
    const h = harness();
    if (multiple) h.segments.push(copy(h.segments[0]));
    h.c.resetMission();
    assert.deepEqual(h.weatherCounts(), [1, 1]);
    assert.deepEqual(copy(h.c.state.routes), h.solution.routes);
    assert.equal(h.c.state.animation.missionComplete, false);
    assert.equal(h.c.missionState.mode, 'READY_TO_ANIMATE');
  }
  const h = harness(); h.segments.push(copy(h.segments[0])); h.c.confirm = () => false;
  h.c.resetMission();
  assert.deepEqual(h.weatherCounts(), [0, 0]);
  assert.equal(h.c.state.animation.elapsedMissionSeconds, 555);
  console.log('PASS single/segmented Reset restores weather only after confirmed mission reset');
}
async function staleSolve() {
  const {c, initial} = harness(); let resolveResponse, applied = 0;
  c.buildSolveRequest = () => ({payload: {env: c.state.env}});
  c.applySolveResult = async () => { applied++; };
  c.fetch = () => new Promise(resolve => { resolveResponse = resolve; });
  const pending = c.runPlanner();
  c.loadFromJson({schema: 'isr_env_v1', is_segmented: false, env: copy(initial)}, 'new.json');
  resolveResponse({ok: true, json: async () => ({success: true, routes: {}})});
  await pending;
  assert.equal(applied, 0);
  assert.equal(c.missionState.mode, 'IDLE');
  console.log('PASS a completed old solve cannot overwrite a newly imported mission');
}
async function staleWeather(stage) {
  const {c} = harness(); let deferred, cuts = 0, currentCalls = 0;
  c.document = {getElementById: id => c.$(id)}; c.$('wp-auto-checkpoint').checked = true;
  c.setInterval = () => {}; c.split_polyline_at_distance = () => ({suffixPoints: [[1, 1], [2, 2]]});
  c.pauseAnimation = () => { c.state.animation.active = false; };
  c.prepareCutFromCurrentState = async () => { cuts++; };
  const weather = {cells: [], retrievedAt: new Date().toISOString()};
  c.window.weatherDataSource = {current: async () => {
    currentCalls++;
    if (stage === 'fetch') return new Promise(resolve => { deferred = () => resolve(weather); });
    return weather;
  }};
  c.fetch = async url => {
    assert.equal(url, '/api/weather/impact', 'recorded source must not fall through to live weather');
    const impact = {affected: true, canReplan: true, reasons: ['changed']};
    if (stage === 'impact') return new Promise(resolve => { deferred = () => resolve({ok: true, json: async () => impact}); });
    return {ok: true, json: async () => impact};
  };
  vm.runInContext(fs.readFileSync('webapp/weather_checkpoints.js', 'utf8'), c);
  const controller = c.window.weatherCheckpoints;
  controller.configure({snapshot: () => ({}), epoch: () => Date.now(), end: () => Date.now() + 100000,
    refreshAndReplan: async () => true});
  c.state.animation.drones = {'1': {animating: true, distanceTraveled: 1, totalDistance: 100}};
  controller.arm([]);
  const checking = controller.inspect(true);
  for (let i = 0; !deferred && i < 10; i++) await Promise.resolve();
  assert(deferred);
  controller.reset();
  controller.arm([]);
  deferred(); await checking;
  assert.equal(cuts, 0);
  assert.equal(c.state.animation.active, true);
  assert.equal(controller.limitStep(5), 5, 'old request must not leave the new controller busy');
  assert.equal(currentCalls, 1);
  console.log(`PASS stale weather ${stage} response ignored after reset; recorded source retained`);
}
(async () => {
  importFresh(); resetBranches(); await staleSolve(); await staleWeather('fetch'); await staleWeather('impact');
})().catch(error => { console.error(error); process.exitCode = 1; });
