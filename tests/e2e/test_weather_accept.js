// Exercise the actual request -> draft -> Accept flow without a browser.
// DOM/render/storage adapters and the solver response are fixtures; production
// weather scripts, request construction, draft application and Accept execute.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('webapp/isr.js', 'utf8');
const recorded = JSON.parse(fs.readFileSync(
  'webapp/_weather_diagnostics/noaa_20261012T012337Z.json', 'utf8'));
const copy = value => JSON.parse(JSON.stringify(value));

function extract(name) {
  const start = source.indexOf('function ' + name + '(');
  assert(start >= 0, name);
  let depth = 0;
  for (let i = source.indexOf(') {', start) + 2; i < source.length; i++) {
    if (source[i] === '{') depth++;
    if (source[i] === '}' && --depth === 0) {
      return (source.slice(start - 6, start) === 'async ' ? 'async ' : '') +
        source.slice(start, i + 1);
    }
  }
  throw Error('Unbalanced function: ' + name);
}

function harness(diagnostic = false) {
  const elements = new Map(), logs = [], segments = [], requests = [], mapFits = [];
  const coords = [-130, 20, -60, 60];
  let weather = copy(recorded), controllerConfig;
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      id, value: '', checked: true, style: {}, listeners: {},
      addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); },
      dispatchEvent(event) {
        for (const fn of this.listeners[event.type] || []) fn(event);
        this['on' + event.type]?.(event);
      },
      replaceChildren() {}, append() {},
      click() { this.onclick?.(); this.dispatchEvent(new Event('click')); },
    });
    return elements.get(id);
  }
  const layer = () => ({addTo() { return this; }, remove() {}});
  const state = {
    env: {airports: [{id: 'A1', lat: 35, lng: -115}, {id: 'A2', lat: 40, lng: -75}],
      targets: [{id: 'T1', lat: 40, lng: -100}], sams: []},
    droneConfigs: {'1': {enabled: true, start_airport: 'A1', end_airport: 'A2',
      fuel_budget: 5000, speed: 10}},
    animation: {active: false, elapsedMissionSeconds: 0}, visitedTargets: [],
    routes: {}, sequences: {}, trajectoryVisible: {}, pendingLostDrones: [],
  };
  const missionState = {mode: 'IDLE', committedSegments: [], draftSolution: null, revision: 0};
  const context = {
    document: {getElementById: element, querySelectorAll: () => [],
      createElement: () => element('new'), body: {style: {}, append() {}}},
    state, missionState,
    getEditableEnv: () => missionState.wasAnimating && missionState.stagedEnv || state.env,
    MissionMode: {IDLE: 'IDLE', DRAFT_READY: 'DRAFT_READY',
      READY_TO_ANIMATE: 'READY_TO_ANIMATE', CHECKPOINT: 'CHECKPOINT'},
    cesiumView: {subscribeLifecycle() { return () => {}; }, init() {}, render() {}, isActive: () => false},
    mapState: {leafletMap: {options: {}, on() {}, invalidateSize() {},
      getContainer: () => ({addEventListener() {}}),
      fitBounds(points, options) { mapFits.push({points: copy(points), options: copy(options)}); },
      getBounds: () => ({getWest: () => coords[0],
      getSouth: () => coords[1], getEast: () => coords[2], getNorth: () => coords[3]})}},
    L: {layerGroup: layer, rectangle: layer, geoJSON: layer},
    syncViewStateFromLeaflet() {}, loadFromJson(data) {
      data = context.migrateLegacyMapCoordinates(data);
      state.env = copy(data.env || data);
      state.droneConfigs = copy(data.drone_configs || state.env.drone_configs || state.droneConfigs);
      state.animation.elapsedMissionSeconds = 0; state.animation.active = false;
      state.checkpoint = null; state.routes = {}; state.sequences = {};
      missionState.mode = 'IDLE'; missionState.draftSolution = null;
    },
    appendDebugLine: message => logs.push(message), drawEnvironment() {},
    toggleMapMode() {}, toggle3dMode() {}, updateZoomSlider() {},
    updateTimeSyncUi() {}, updateButtonStates() {}, setInterval() {},
    setTimeout() {}, addEventListener() {}, requestAnimationFrame() {},
    Event, Response, AbortController,
    segmentedImport: {isActive: () => false},
    missionReplay: {getCurrentSegmentIndex: () => 0, getSegment: i => segments[i],
      getSegmentCount: () => segments.length,
      addSegment(segment) { segment.index = segments.length; segments.push(segment); return segment; },
      setCurrentSegmentIndex() {}, printSegmentTable: () => ''},
    readDroneConfigsFromUI: () => copy(state.droneConfigs),
    readTimeSyncOptionsFromUI: () => ({enabled: false}),
    saveInitialEnvSnapshot() { state.initialEnvSnapshot = copy(state.env); },
    syncMissionUi() {}, setMissionMode: mode => { missionState.mode = mode; },
    applyDraftSolutionToUI(draft) {
      state.routes = copy(draft.routes); state.sequences = copy(draft.sequences);
    },
    buildCleanSegment: copy, segmentStore: {addSegment: () => 0},
    mergeEnvForwardFromCurrent() {},
    fetch: async (url, init) => {
      if (url.endsWith('.json') || url.includes('/weather/current')) {
        return new Response(JSON.stringify(weather));
      }
      assert.equal(url, '/api/solve_with_allocation');
      requests.push(JSON.parse(init.body));
      return new Response(JSON.stringify({success: true, routes: {'1': {
        route: ['A1', 'T1', 'A2'], trajectory: [], distance: 100,
        anim_frame: {total_distance: 100},
      }}}));
    },
  };
  context.window = context;
  context.$ = id => id === 'allocation-strategy' ? {value: 'geographic'} :
    id === 'chk-auto-optimize' ? {checked: true} :
      id === 'btn-run-planner' ? null : element(id);
  vm.createContext(context);
  vm.runInContext(['migrateLegacyMapCoordinates', 'getUiPermissions', 'restoreCutFromCheckpointDraft',
    'buildCheckpointEnv', 'detectLostDrones', 'buildSolveRequest',
    'applySolveResult', 'runPlanner', 'acceptSolution'].map(extract).join('\n'), context);
  vm.runInContext(fs.readFileSync('webapp/weather_checkpoints.js', 'utf8'), context);
  const configure = context.weatherCheckpoints.configure;
  context.weatherCheckpoints.configure = config => { controllerConfig = config; configure(config); };
  vm.runInContext(fs.readFileSync('webapp/page_capabilities.js', 'utf8'), context);
  vm.runInContext(fs.readFileSync('webapp/weather_preview.js', 'utf8'), context);
  if (diagnostic) vm.runInContext(fs.readFileSync('webapp/_weather_diagnostics/runner.js', 'utf8'), context);
  context.PageCapabilities.initialize({ weatherPreview: true, weatherDiagnostics: diagnostic });
  function input(id, value) { element(id).value = value; element(id).dispatchEvent(new Event('input')); }
  input('wp-start', '2026-10-12T01:24');
  input('wp-end', '2026-10-12T17:24');
  return {
    context, element, state, missionState, coords, requests, logs, input, mapFits,
    getWeather: () => element('wp-get').onclick(),
    refreshAt: epoch => controllerConfig.refreshAt(epoch),
    changeWeather: fn => { fn(weather); },
    solve: async () => {
      const result = await context.runPlanner();
      assert(result?.success, logs.join('\n'));
      assert.equal(missionState.mode, 'DRAFT_READY');
    },
    accept: (allowed, message) => {
      context.acceptSolution();
      assert.equal(missionState.mode, allowed ? 'READY_TO_ANIMATE' : 'DRAFT_READY', logs.join('\n'));
      assert.equal(segments.length, allowed ? 1 : 0);
      if (message) assert.match(element('wp-status').textContent, message);
    },
  };
}

let cases = 0;
async function check(name, callback) {
  await callback(); cases++; console.log('PASS ' + name);
}
module.exports = {harness};
if (require.main === module) (async () => {
  await check('recorded NOAA request and draft accept unchanged', async () => {
    const h = harness(true); await h.getWeather(); await h.solve();
    assert.deepEqual(copy(h.missionState.draftSolution.weatherSnapshot), h.requests[0].env.weather_snapshot);
    h.accept(true);
  });
  await check('viewing a different map area preserves the solved weather', async () => {
    const h = harness(); await h.getWeather(); await h.solve();
    h.coords[0] -= 5; h.element('zoom-slider').dispatchEvent(new Event('input'));
    h.accept(true);
  });
  await check('same-value zoom input does not block Accept', async () => {
    const h = harness(); await h.getWeather(); await h.solve();
    h.element('zoom-slider').dispatchEvent(new Event('input')); h.accept(true);
  });
  await check('changing mission times still blocks Accept', async () => {
    const h = harness(); await h.getWeather(); await h.solve();
    h.input('wp-end', '2026-10-12T18:24'); h.accept(false, /Mission times changed/);
  });
  await check('invalid date input blocks Accept', async () => {
    const h = harness(); await h.getWeather(); await h.solve();
    h.input('wp-start', ''); h.accept(false, /complete date and time/);
  });
  await check('retrieving a changed area requires a new solve', async () => {
    const h = harness(true); await h.getWeather(); await h.solve();
    h.coords[0] -= 5; await h.getWeather(); h.accept(false, /loaded forecast changed/);
  });
  await check('changed advisory data blocks even with the same retrieval timestamp', async () => {
    const h = harness(); await h.getWeather(); await h.solve();
    const usedId = h.missionState.draftSolution.weatherSnapshot.advisories[0].id;
    h.changeWeather(weather => { weather.cells.find(cell => cell.id === usedId).name += ' revised'; });
    await h.getWeather(); h.accept(false, /loaded forecast changed/);
  });
  await check('new weather after a weatherless solve requires a new solve', async () => {
    const h = harness(); await h.solve(); await h.getWeather();
    h.accept(false, /solved without the loaded weather/);
  });
  await check('a mission solved without weather remains acceptable', async () => {
    const h = harness(); await h.solve(); h.accept(true);
  });
  await check('checkpoint time keeps exact seconds while calendar displays minutes', async () => {
    const h = harness(); await h.getWeather();
    const epoch = Date.parse('2026-10-12T02:55:12.500Z');
    await h.refreshAt(epoch); await h.solve();
    assert.equal(h.requests[0].env.weather_snapshot.start, epoch);
    assert.equal(h.element('wp-start').value, '2026-10-12T02:55'); h.accept(true);
  });
  await check('Central Time display preserves the UTC solve epoch', async () => {
    const h = harness(true); await h.getWeather(); await h.solve();
    h.element('wp-zone').value = 'America/Chicago';
    h.element('wp-zone').dispatchEvent(new Event('change'));
    assert.equal(h.element('wp-start').value, '2026-10-11T20:24'); h.accept(true);
  });
  console.log(`${cases} weather acceptance pipeline scenarios passed.`);
})().catch(error => { console.error(error); process.exitCode = 1; });
