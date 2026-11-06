// Offline integration regression: production map controls + weather Fit Mission,
// with the real Leaflet 1.9.4 GridLayer/event lifecycle in jsdom. No tile network.
// Install outside the repository:
//   npm install --prefix /tmp/isr-fit-mission-tests leaflet@1.9.4 jsdom@26
// Run:
//   NODE_PATH=/tmp/isr-fit-mission-tests/node_modules node tests/e2e/test_leaflet_fit_mission.js
// Add --baseline to exercise the committed source before a pending fix.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {execFileSync} = require('node:child_process');
const {JSDOM} = require('jsdom');

const baseline = process.argv.includes('--baseline');
const read = path => baseline ? execFileSync('git', ['show', `HEAD:${path}`], {encoding: 'utf8'}) :
  fs.readFileSync(path, 'utf8');
const source = read('webapp/isr.js');
const weatherSource = read('webapp/weather_preview.js');
const pause = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
function extract(name) {
  const start = source.indexOf(`function ${name}(`);
  assert(start >= 0, `${name} exists`);
  return source.slice(start, source.indexOf('\n}', start) + 2);
}

async function harness() {
  const dom = new JSDOM('<!doctype html><body><section><canvas id="env-canvas"></canvas></section></body>',
    {runScripts: 'outside-only', pretendToBeVisual: true, url: 'https://offline.invalid/'});
  const w = dom.window;
  await new Promise(resolve => w.addEventListener('load', resolve, {once: true}));
  const dimensions = {width: 1000, height: 900};
  // jsdom has no layout engine. Supply only container measurements; projection,
  // event dispatch, tile selection, pan and zoom transforms remain real Leaflet.
  for (const [name, axis] of [['clientWidth', 'width'], ['clientHeight', 'height'],
    ['offsetWidth', 'width'], ['offsetHeight', 'height']]) {
    Object.defineProperty(w.HTMLElement.prototype, name, {configurable: true,
      get() { return this.id === 'leaflet-map' || this.id === 'env-canvas' ? dimensions[axis] : 0; }});
  }
  w.HTMLElement.prototype.getBoundingClientRect = function() {
    return {left: 0, top: 0, right: this.clientWidth, bottom: this.clientHeight,
      width: this.clientWidth, height: this.clientHeight};
  };
  const canvas = w.document.getElementById('env-canvas');
  canvas.width = dimensions.width; canvas.height = dimensions.height;
  const element = id => {
    let result = w.document.getElementById(id);
    if (!result) {
      result = w.document.createElement(id.includes('slider') || id === 'wp-start' || id === 'wp-end' ? 'input' : 'button');
      result.id = id; w.document.body.append(result);
    }
    return result;
  };
  for (const match of weatherSource.matchAll(/'(wp-[a-z0-9-]+|btn-view-[a-z0-9]+)'/g)) element(match[1]);
  for (const id of ['btn-view-grid', 'btn-view-map', 'map-tile-select', 'zoom-slider', 'btn-reset-view',
    'wp-start-display', 'wp-end-display']) element(id);
  w.eval(fs.readFileSync(require.resolve('leaflet/dist/leaflet-src.js'), 'utf8'));
  // Exercise the same CSS transform code as Chrome, including fractional zoom.
  w.L.Browser.any3d = true;
  w.L.Browser.svg = true;
  w.$ = id => w.document.getElementById(id);
  w.mapState = {active: false, leafletMap: null, tileLayer: null, mapType: 'street'};
  w.viewState = {centerLat: 40, centerLng: 15, zoomLevel: 3, minZoom: 0, maxZoom: 18};
  w.state = {env: {airports: [{id: 'A1', lat: 51.47, lng: -0.45}, {id: 'A2', lat: 30.12, lng: 31.40}],
    targets: [{id: 'T1', lat: 45, lng: 24}, {id: 'T2', lat: 36, lng: 16}, {id: 'T3', lat: 32, lng: 35}]},
  droneConfigs: {}, animation: {elapsedMissionSeconds: 0}};
  w.getEditableEnv = () => w.state.env;
  const noop = () => {};
  w.MAP_TILES = {street: {url: 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==', attr: ''}};
  Object.assign(w, {saveViewToLocalStorage: noop, drawEnvironment: noop,
    updateZoomSlider: noop, updateCanvasPointerEvents: noop, toggle3dMode: noop,
    loadFromJson: noop, buildSolveRequest: noop, acceptSolution: noop, runPlanner: noop,
    appendDebugLine: noop, missionState: {}, weatherCheckpoints: {configure: noop},
    cesiumView: {subscribeLifecycle: () => noop, init: noop, render: noop, isActive: () => false}});
  w.eval(['mercatorPoint', 'mercatorLat', 'mapCanvasScale', 'mapPointToCanvas', 'constrainMapView',
    'syncViewStateFromLeaflet', 'syncLeafletToViewState', 'initLeafletMap', 'destroyLeafletMap',
    'toggleMapMode', 'applyZoomFromSlider'].map(extract).join('\n'));
  if (!baseline) w.eval(read('webapp/page_capabilities.js'));
  w.eval(weatherSource);
  if (!baseline) w.PageCapabilities.initialize({ weatherPreview: true, weatherDiagnostics: false });
  w.toggleMapMode(true);
  const fits = [];
  const originalFit = w.L.Map.prototype.fitBounds;
  w.L.Map.prototype.fitBounds = function(points, options) {
    fits.push({points, options}); return originalFit.call(this, points, options);
  };
  const syncs = [];
  const originalSync = w.syncViewStateFromLeaflet;
  w.syncViewStateFromLeaflet = (...args) => { syncs.push(1); return originalSync(...args); };
  return {w, dom, canvas, dimensions, element, fits, syncs,
    fit: async () => { element('wp-fit').click(); await pause(350); },
    close: () => { w.destroyLeafletMap(); w.close(); }};
}

function listeners(map) {
  return Object.fromEntries(['move', 'moveend', 'zoom'].map(type =>
    [type, (map._events[type] || []).map(listener => ({fn: listener.fn, ctx: listener.ctx}))]));
}
function unchangedListeners(map, before, label) {
  for (const [type, expected] of Object.entries(before)) {
    const actual = map._events[type] || [];
    assert.equal(actual.length, expected.length, `${label}: ${type} handler count`);
    expected.forEach(listener => assert(actual.some(current => current.fn === listener.fn && current.ctx === listener.ctx),
      `${label}: retained original ${type} handler (including Leaflet and weather listeners)`));
  }
}

function assertAligned(h, label) {
  const {w, canvas} = h;
  const map = w.mapState.leafletMap, layer = w.mapState.tileLayer;
  const level = layer._levels[layer._tileZoom];
  assert(level, `${label}: raster level exists`);
  assert.equal(layer._tileZoom, Math.round(map.getZoom()), `${label}: current raster resolution`);
  const style = level.el.style[w.L.DomUtil.TRANSFORM];
  const transform = /translate3d\(([-\d.e]+)px,\s*([-\d.e]+)px,\s*0(?:px)?\)\s*scale\(([-\d.e]+)\)/.exec(style);
  assert(transform, `${label}: actual tile CSS transform ${style}`);
  const [, x, y, scale] = transform.map(Number);
  const pane = w.L.DomUtil.getPosition(map._mapPane);
  const env = w.getEditableEnv();
  for (const point of [...env.airports, ...env.targets]) {
    const projected = map.project([point.lat, point.lng], level.zoom).subtract(level.origin);
    // Reconstruct where the actual tile image puts this landmark from its
    // existing DOM transform, independently of the canvas viewState.
    const raster = [projected.x * scale + x + pane.x, projected.y * scale + y + pane.y];
    const overlay = w.mapPointToCanvas(canvas, point.lat, point.lng);
    const error = Math.hypot(raster[0] - overlay[0], raster[1] - overlay[1]);
    assert(error <= 1.5, `${label}: ${point.id} raster/overlay error ${error.toFixed(3)}px`);
  }
}

let passed = 0, failed = 0;
async function check(name, fn) {
  const h = await harness();
  try { await fn(h); passed++; console.log('PASS ' + name); }
  catch (error) { failed++; console.error('FAIL ' + name + '\n' + error.stack); }
  finally { h.close(); }
}

(async () => {
  await check('Fit Mission preserves real Leaflet tile listeners and weather listeners', async h => {
    // Connect the weather moveend callback, then inspect its identity, not only
    // a total count that could be replaced by newly created application handlers.
    h.element('wp-fit').click();
    const map = h.w.mapState.leafletMap, initial = listeners(map);
    assert(initial.moveend.length >= 4, 'map, canvas, raster and weather callbacks are attached');
    await pause(350);
    unchangedListeners(map, initial, 'after Fit Mission settles');
    for (let i = 0; i < 3; i++) {
      await h.fit();
      unchangedListeners(map, initial, 'repeated fit');
      h.w.applyZoomFromSlider(String(5.3 + i / 10));
      unchangedListeners(map, initial, 'slider sync');
      h.element('wp-area').textContent = 'old area';
      const count = h.syncs.length;
      map.fire('moveend');
      assert.equal(h.element('wp-area').textContent, 'Current Map view', 'weather moveend callback still runs');
      assert.equal(h.syncs.length - count, 2, 'one canvas and one weather synchronization; no duplicates');
    }
  });

  await check('UK/Egypt airports and tiles agree after fits, slider zoom and pan', async h => {
    const originalMission = JSON.stringify(h.w.state.env);
    await h.fit();
    const map = h.w.mapState.leafletMap;
    assertAligned(h, 'first fit');
    for (const p of [...h.w.state.env.airports, ...h.w.state.env.targets]) {
      const pixel = map.latLngToContainerPoint([p.lat, p.lng]);
      assert(pixel.x >= 44 && pixel.y >= 44 && pixel.x <= h.dimensions.width - 44 &&
        pixel.y <= h.dimensions.height - 44, `${p.id} is in the padded mission viewport`);
    }
    for (const zoom of [5.3, 6, 4.7]) {
      h.w.state.editMode = !h.w.state.editMode;
      h.w.applyZoomFromSlider(String(zoom));
      assertAligned(h, `slider ${zoom}`);
      map.panBy([130, -70], {animate: false});
      assertAligned(h, `pan at ${zoom}`);
      await h.fit();
      assertAligned(h, `fit after ${zoom}`);
    }
    assert(h.fits.every(fit => fit.options.animate === false), 'fit is atomic; no delayed animation conflicts');
    assert.equal(JSON.stringify(h.w.state.env), originalMission, 'fitting preserves original mission coordinates');
  });

  await check('Grid to Map creates fresh working listeners and preserves fitted geography', async h => {
    await h.fit();
    for (let i = 0; i < 3; i++) {
      const oldMap = h.w.mapState.leafletMap;
      h.w.toggleMapMode(false);
      Object.assign(h.w.viewState, {centerLat: 35 + i, centerLng: 22 + i, zoomLevel: 5});
      await h.fit();
      const map = h.w.mapState.leafletMap;
      assert.notEqual(map, oldMap, 'new map instance');
      assertAligned(h, 'Grid -> Map fit');
      const initial = listeners(map);
      h.w.applyZoomFromSlider('5.4');
      unchangedListeners(map, initial, 'new map slider');
      assertAligned(h, 'new map slider');
      h.element('wp-area').textContent = 'old area';
      map.panBy([30, 40], {animate: false});
      assert.equal(h.element('wp-area').textContent, 'Current Map view');
    }
  });
  await check('Fit Mission follows the displayed live-edit environment without mutating it', async h => {
    const originalMission = JSON.stringify(h.w.state.env);
    const staged = {airports: [{id: 'A1', lat: 47.45, lng: -122.31}, {id: 'A2', lat: 37.62, lng: -122.38}],
      targets: [{id: 'T1', lat: 43, lng: -121}]};
    const originalStaged = JSON.stringify(staged);
    h.w.state.editMode = true;
    h.w.getEditableEnv = () => staged;
    await h.fit();
    const map = h.w.mapState.leafletMap;
    for (const point of [...staged.airports, ...staged.targets]) {
      assert(map.getBounds().contains([point.lat, point.lng]), 'staged airport or target is visible');
    }
    assertAligned(h, 'live edit fit');
    assert.equal(JSON.stringify(staged), originalStaged, 'staged coordinates unchanged');
    assert.equal(JSON.stringify(h.w.state.env), originalMission, 'committed mission unchanged');
  });
  console.log(`${passed} real Leaflet Fit Mission regressions passed; ${failed} failed`);
  if (failed) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
