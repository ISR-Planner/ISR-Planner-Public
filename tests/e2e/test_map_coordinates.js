// Offline regression tests of the shipped map/edit/import/export functions.
// Run: node tests/e2e/test_map_coordinates.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('webapp/isr.js', 'utf8');
const blockStart = source.indexOf('// SINGLE-WORLD COORDINATE CONTRACT');
const blockEnd = source.indexOf('// END SINGLE-WORLD COORDINATE CONTRACT', blockStart);
assert(blockStart >= 0 && blockEnd > blockStart, 'Production coordinate contract must exist');
const coordinateBlock = source.slice(blockStart, blockEnd);
const viewStart = source.indexOf('const viewPreferences = ISRViewPreferences.create({');
assert(viewStart >= 0, 'Production viewport composition must exist');
const viewComposition = source.slice(viewStart, source.indexOf('\n});', viewStart) + 4);
function extract(name) {
  let start = source.indexOf(`function ${name}(`);
  assert(start >= 0, `${name} must exist`);
  if (source.slice(start - 6, start) === 'async ') start -= 6;
  return source.slice(start, source.indexOf('\n}', start) + 2);
}
const close = (a, b, message) => assert(Math.abs(a - b) < 1e-7, `${message}: ${a} versus ${b}`);
const plain = value => JSON.parse(JSON.stringify(value));
let passed = 0;

function harness() {
  const events = {canvas: {}, window: {}};
  function on(surface, event, fn) { (events[surface][event] ||= []).push(fn); }
  const rect = {left: 0, top: 0, width: 1000, height: 600};
  const canvas = {width: 1000, height: 600, style: {},
    getBoundingClientRect: () => ({...rect}),
    addEventListener: (event, fn) => on('canvas', event, fn)};
  const elements = {'env-canvas': canvas};
  const storage = new Map();
  const exports = [];
  const context = {
    console, Date, Math,
    ISRViewPreferences: require('../../webapp/view_preferences.js'),
    $: id => elements[id] || null,
    window: {addEventListener: (event, fn) => on('window', event, fn)},
    document: {getElementById: id => elements[id] || null},
    localStorage: {getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value)},
    state: {env: {airports: [], targets: [], sams: []}, droneConfigs: {}, animation: {drones: {}},
      editMode: false, addMode: null, dragging: null},
    viewState: {centerLat: 0, centerLng: 0, zoomLevel: 3, minZoom: 0, maxZoom: 18},
    mapState: {active: false, leafletMap: null, bounds: {south: -1, north: 1, west: -1, east: 1}},
    MissionMode: {IDLE: 'IDLE'}, missionState: {},
    SF_DEFAULT: {centerLat: 37.775, centerLng: -122.435, zoomLevel: 13},
    VIEW_STORAGE_KEY: 'test-map-view', VIEW_STORAGE_TTL_MS: 30 * 24 * 3600000,
    DEFAULT_CLIMB_ANGLE_DEG: 12,
    missionReplay: {clear() {}, getSegmentCount: () => 0, getSegments: () => []},
    segmentedImport: {clear() {}}, segmentStore: {clear() {}, count: () => 0},
    drawEnvironment() {}, updateZoomSlider() {}, appendDebugLine() {},
    updateAirportDropdowns() {}, updateTimeSyncMemberUI() {}, fillMissingAltitudes() {},
    fillMissingAltitudesEverywhere() {}, updateSamWrappingClientSide() {},
    invalidateMissionAsyncWork() {}, updateTimeSyncUi() {}, clearVisited() {},
    restoreTimeSyncOptionsToUI() {}, refreshCategoryDisplays() {}, updateStatsFromRoutes() {},
    updateMissionControlState() {}, cancelAnimationFrame() {},
    isSegmentInfoJson: () => false, isSegmentedMissionJson: () => false,
    setMissionMode: mode => { context.missionState.mode = mode; },
    initDroneConfigsFromEnv: () => { context.state.droneConfigs = plain(context.state.env.drone_configs || {}); },
    readTimeSyncOptionsFromUI: () => ({}), generateExportFilename: () => 'test-env.json',
    downloadJsonFile: data => exports.push(plain(data)),
    newTankerAltitudeKm: () => 5,
  };
  context.getEditableEnv = () => context.state.env;
  vm.createContext(context);
  vm.runInContext(coordinateBlock + '\n' + viewComposition + '\n' + [
    'canvasToLatLng', 'canvasToWorld', 'hitTestPixel', 'attachCanvasEditing', 'attachZoomPanHandlers',
    'applyView', 'centerMapBoundsOn', 'restoreMapView', 'readSavedView', 'saveViewToLocalStorage',
    'syncViewStateFromLeaflet', 'syncLeafletToViewState', 'loadFromJson', 'buildCombinedEnv',
    'stripStartFuelDeep', 'exportEnvironment',
  ].map(extract).join('\n'), context);
  context.attachCanvasEditing();
  context.attachZoomPanHandlers();
  const dispatch = (surface, type, args = {}) => {
    const event = {clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2,
      button: 0, deltaX: 0, deltaY: 0,
      ctrlKey: false, metaKey: false, preventDefault() {}, ...args};
    for (const fn of events[surface][type] || []) fn(event);
  };
  return {context, canvas, rect, storage, exports, dispatch};
}

function validView(h, message) {
  const v = h.context.viewState;
  assert(Number.isFinite(v.centerLat) && Number.isFinite(v.centerLng), message);
  assert(v.centerLng >= -180 && v.centerLng <= 180, `${message}: geographic longitude`);
  const [x, y] = h.context.mercatorPoint(v.centerLat, v.centerLng, v.zoomLevel);
  const world = 256 * 2 ** v.zoomLevel;
  for (const [center, viewport] of [[x, h.rect.width], [y, h.rect.height]]) {
    if (world >= viewport) {
      assert(center - viewport / 2 >= -1e-7 && center + viewport / 2 <= world + 1e-7,
        `${message}: viewport stays in one world`);
    } else close(center, world / 2, `${message}: small world stays centered`);
  }
}

async function test(name, run) {
  await run();
  passed++;
  console.log('PASS ' + name);
}

(async () => {
  await test('Grid wheel and drag pans stop at the geographic boundary', () => {
    const h = harness();
    h.dispatch('canvas', 'wheel', {deltaX: 100000, deltaY: -100000});
    validView(h, 'Positive wheel pan');
    h.dispatch('canvas', 'wheel', {deltaX: -200000, deltaY: 200000});
    validView(h, 'Negative wheel pan');
    h.dispatch('canvas', 'mousedown');
    h.dispatch('window', 'mousemove', {clientX: 100000, clientY: -100000});
    validView(h, 'Drag pan');
    h.dispatch('window', 'mouseup');
    const saved = JSON.parse(h.storage.get('test-map-view'));
    assert(saved.centerLng >= -180 && saved.centerLng <= 180, 'Panning must not persist a repeated-world center');
  });

  await test('Grid pinch uses latitude/longitude objects and keeps zoomed views finite', () => {
    const h = harness();
    h.dispatch('canvas', 'wheel', {ctrlKey: true, deltaY: -100, clientX: 650, clientY: 250});
    assert(h.context.viewState.zoomLevel > 3, 'Pinch must increase zoom');
    validView(h, 'Pinch in');
    h.dispatch('canvas', 'wheel', {ctrlKey: true, deltaY: 10000});
    validView(h, 'Pinch out to full world');
  });

  await test('resized canvas overlays and editing follow Leaflet CSS pixels at a container offset', () => {
    const h = harness();
    h.canvas.width = 800;
    h.canvas.height = 600;
    Object.assign(h.rect, {left: 120, top: 40, width: 1200, height: 450});
    const [worldX, worldY] = h.context.mercatorPoint(20, 30, 3);
    const center = h.context.mercatorPoint(0, 0, 3);
    const expectedClient = [h.rect.left + h.rect.width / 2 + worldX - center[0],
      h.rect.top + h.rect.height / 2 + worldY - center[1]];
    const projected = h.context.mapPointToCanvas(h.canvas, 20, 30);
    close(projected[0] * h.rect.width / h.canvas.width + h.rect.left, expectedClient[0],
      'Horizontal overlay position matches tile pixels');
    close(projected[1] * h.rect.height / h.canvas.height + h.rect.top, expectedClient[1],
      'Vertical overlay position matches tile pixels');
    const inverse = h.context.canvasToLatLng(h.canvas, ...expectedClient);
    close(inverse.lat, 20, 'Resized latitude roundtrip');
    close(inverse.lng, 30, 'Resized longitude roundtrip');
    h.context.state.editMode = true;
    h.context.state.addMode = 'airport';
    h.dispatch('canvas', 'mousedown', {clientX: expectedClient[0], clientY: expectedClient[1]});
    close(h.context.state.env.airports[0].lat, 20, 'Real add event uses CSS scale');
    close(h.context.state.env.airports[0].lng, 30, 'Real add event uses CSS scale');
    const hit = h.context.hitTestPixel(...projected);
    assert.equal(hit.kind, 'airport');
    assert.equal(hit.index, 0);
  });

  await test('resized Grid pinch anchors its geographic point and panning advances in CSS pixels', () => {
    const h = harness();
    h.canvas.width = 800;
    Object.assign(h.rect, {left: 100, top: 20, width: 1200, height: 450});
    const clientX = h.rect.left + h.rect.width / 2 + 120;
    const clientY = h.rect.top + h.rect.height / 2 - 60;
    const before = h.context.canvasToLatLng(h.canvas, clientX, clientY);
    h.dispatch('canvas', 'wheel', {ctrlKey: true, deltaY: -100, clientX, clientY});
    const after = h.context.canvasToLatLng(h.canvas, clientX, clientY);
    close(after.lat, before.lat, 'Pinch keeps latitude beneath cursor');
    close(after.lng, before.lng, 'Pinch keeps longitude beneath cursor');
    validView(h, 'Resized pinch');
    const pointBeforePan = h.context.mapPointToCanvas(h.canvas, before.lat, before.lng);
    h.dispatch('canvas', 'wheel', {deltaX: 80, deltaY: 40});
    const pointAfterPan = h.context.mapPointToCanvas(h.canvas, before.lat, before.lng);
    close((pointBeforePan[0] - pointAfterPan[0]) * h.rect.width / h.canvas.width, 80,
      'Horizontal wheel pan tracks CSS pixels');
    close((pointBeforePan[1] - pointAfterPan[1]) * h.rect.height / h.canvas.height, 40,
      'Vertical wheel pan tracks CSS pixels');
    h.dispatch('canvas', 'wheel', {deltaX: 100000, deltaY: 100000});
    validView(h, 'Resized view boundary');
  });

  for (const mapActive of [false, true]) {
    await test(`${mapActive ? 'Map' : 'Grid'} adds and drags only inside the geographic world`, async () => {
      const h = harness();
      h.context.mapState.active = mapActive;
      h.context.viewState.zoomLevel = 0;
      h.context.constrainMapView();
      h.context.state.editMode = true;
      h.context.state.addMode = 'airport';
      h.dispatch('canvas', 'mousedown', {clientX: 5});
      assert.equal(h.context.state.env.airports.length, 0, 'Blank area outside world cannot create an airport');
      h.dispatch('canvas', 'mousedown');
      assert.equal(h.context.state.env.airports.length, 1);
      close(h.context.state.env.airports[0].lng, 0, 'Center airport longitude');
      h.context.state.addMode = null;
      h.dispatch('canvas', 'mousedown');
      assert.equal(h.context.state.dragging.kind, 'airport', 'Real edit hit-test selects the airport');
      h.dispatch('canvas', 'mousemove', {clientX: 999});
      close(h.context.state.env.airports[0].lng, 0, 'Outside-world drag must not write an invalid longitude');
      h.dispatch('canvas', 'mousemove', {clientX: 550});
      close(h.context.state.env.airports[0].lng, 70.3125, 'Inside-world drag follows inverse projection');
      h.dispatch('window', 'mouseup');
      await h.context.exportEnvironment();
      assert.equal(h.exports.length, 1, 'Actual export must produce a file');
      close(h.exports[0].env.airports[0].lng, 70.3125, 'Export preserves the valid source coordinate');
      const second = harness();
      second.context.loadFromJson(h.exports[0], 'roundtrip.json');
      close(second.context.state.env.airports[0].lng, 70.3125, 'Import/export roundtrip keeps the edited airport');
      validView(second, 'Roundtrip view');
    });
  }

  await test('actual legacy import migrates objects and saved view without modifying its input', async () => {
    const h = harness();
    const legacy = {schema: 'isr_env_v1', is_segmented: false,
      env: {airports: [{id: 'A1', lat: 32.8998, lng: -457.0297908783},
        {id: 'A2', lat: 25.2532, lng: -304.638004303}], targets: [],
        sams: [{center: {lat: 35, lng: -455}, range_km: 5}]},
      map_view: {center: {lat: 29, lng: -367.734375}, zoom: 3,
        bounds: {south: 20, north: 40, west: -480, east: -290}}};
    const before = plain(legacy);
    h.context.loadFromJson(legacy, 'legacy-dfw-dxb.json');
    assert.deepEqual(legacy, before, 'Migration must not mutate the caller’s saved file');
    close(h.context.state.env.airports[0].lng, -97.0297908783, 'DFW migration');
    close(h.context.state.env.airports[1].lng, 55.361995697, 'DXB migration');
    close(h.context.state.env.sams[0].center.lng, -95, 'SAM center migration');
    validView(h, 'Imported legacy view');
    await h.context.exportEnvironment();
    assert.equal(h.exports.length, 1);
    const second = harness();
    second.context.loadFromJson(h.exports[0], 'repaired-roundtrip.json');
    assert.deepEqual(plain(second.context.state.env.airports), plain(h.context.state.env.airports));
    validView(second, 'Migrated file roundtrip');
  });

  await test('saved browser views migrate once before they enter active map state', () => {
    const h = harness();
    h.storage.set('test-map-view', JSON.stringify({centerLat: 29, centerLng: -367.734375,
      zoomLevel: 3, timestamp: Date.now()}));
    const saved = h.context.readSavedView();
    assert(saved, 'Legacy saved view is recoverable');
    h.context.applyView(saved);
    close(h.context.viewState.centerLng, -7.734375, 'Saved world-copy view migration');
    validView(h, 'Restored saved view');
    h.context.saveViewToLocalStorage();
    assert.equal(JSON.parse(h.storage.get('test-map-view')).centerLng, -7.734375);
  });

  await test('legacy migration repairs known coordinate arrays without changing numeric mission data', () => {
    const h = harness();
    const input = {
      segments: [{env: {airports: [{id: 'A1', lat: 32, lng: -457}], targets: [], sams: []},
        cutPositions: {'1': [33, -455]}, solution: {routes: {'1': {
          trajectory: [[32, -457], [33, -455]],
          anim_frame: {trajectory: [[32, -457], [33, -455]], cumulative_distances: [0, 720]},
        }}}}],
      wrapped_polygons: [[[31, -458], [32, -457], [31, -456]]],
      arbitrary_values: [[1, 720], [2, -720]], fuel_budget: 22780,
    };
    const before = plain(input);
    const repaired = h.context.migrateLegacyMapCoordinates(input);
    assert.deepEqual(input, before, 'Legacy input remains untouched');
    assert.deepEqual(plain(repaired.segments[0].cutPositions['1']), [33, -95]);
    assert.deepEqual(plain(repaired.segments[0].solution.routes['1'].trajectory), [[32, -97], [33, -95]]);
    assert.deepEqual(plain(repaired.segments[0].solution.routes['1'].anim_frame.trajectory), [[32, -97], [33, -95]]);
    assert.deepEqual(plain(repaired.wrapped_polygons), [[[31, -98], [32, -97], [31, -96]]]);
    assert.deepEqual(plain(repaired.arbitrary_values), input.arbitrary_values,
      'Unrelated arrays must not be treated as lat/lng coordinates');
    assert.deepEqual(plain(repaired.segments[0].solution.routes['1'].anim_frame.cumulative_distances), [0, 720]);
    assert.equal(repaired.fuel_budget, 22780);
    assert.deepEqual(plain(h.context.migrateLegacyMapCoordinates(repaired)), plain(repaired),
      'Migration is idempotent');
  });

  await test('Leaflet movement cannot introduce a repeated-world center into planner state', () => {
    const h = harness();
    let position = {lat: 90, lng: -367.734375};
    h.context.mapState.active = true;
    h.context.mapState.leafletMap = {
      getCenter: () => position, getZoom: () => 3,
      setView: ([lat, lng]) => { position = {lat, lng}; }, on() {}, off() {},
    };
    h.context.syncViewStateFromLeaflet();
    validView(h, 'Leaflet move');
    assert.equal(position.lng, h.context.viewState.centerLng, 'Tile map follows constrained planner center');
  });

  await test('Mercator pole projection stays finite and rendering does not silently wrap invalid data', () => {
    const h = harness();
    for (const lat of [-90, 90]) {
      const point = h.context.mercatorPoint(lat, 0, 3);
      assert(point.every(Number.isFinite), 'Pole projection must be finite');
    }
    const raw = h.context.mapPointToCanvas(h.canvas, 0, -457.0297908783);
    const canonical = h.context.mapPointToCanvas(h.canvas, 0, -97.0297908783);
    close(canonical[0] - raw[0], 2048, 'Runtime projection does not disguise unmigrated data with modulo');
  });

  await test('dateline paths split in both directions and closed polygons have no cross-world chord', () => {
    const h = harness();
    h.context.viewState.zoomLevel = 2;
    for (const [points, closed] of [
      [[[0, 179], [0, -179]], false],
      [[[0, -179], [0, 179]], false],
      [[[0, 179], [1, -179], [2, 179]], true],
    ]) {
      const commands = [];
      const ctx = {moveTo: (x, y) => commands.push(['M', x, y]),
        lineTo: (x, y) => commands.push(['L', x, y])};
      h.context.appendMapPath(ctx, points, h.canvas, closed);
      assert(commands.filter(command => command[0] === 'M').length >= 2, 'Crossing must create two subpaths');
      let last;
      for (const [kind, x, y] of commands) {
        assert(Number.isFinite(x) && Number.isFinite(y));
        if (kind === 'L') assert(Math.abs(x - last[0]) < 10, 'Local dateline leg cannot span the world');
        last = [x, y];
      }
    }
    close(Math.abs(h.context.interpolateLongitude(179, -179, 0.5)), 180, 'Eastward midpoint stays on seam');
    close(Math.abs(h.context.interpolateLongitude(-179, 179, 0.5)), 180, 'Westward midpoint stays on seam');
  });

  console.log(`Map coordinate regressions: ${passed} passed.`);
})().catch(error => { console.error(error); process.exitCode = 1; });
