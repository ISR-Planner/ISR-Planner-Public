// Actual Cesium scene lifecycle with deterministic property/canvas substitutes.
// No private mission fixture, browser, imagery, or network is needed.
// node tests/e2e/test_time_sync_markers_3d.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('webapp/cesium_view.js', 'utf8');
const planner = fs.readFileSync('webapp/isr.js', 'utf8');
const syncStart = planner.indexOf('function getActiveTimeSync() {');
const syncEnd = planner.indexOf('\n}', syncStart) + 2;
assert(syncStart >= 0 && syncEnd > syncStart);

let wallMs = 0;
const canvases = new Map();
const viewers = [];
class Cartesian3 {
  constructor(x, y, z) { Object.assign(this, {x, y, z}); }
  static fromDegrees(lng, lat, altitude) { return {lng, lat, altitude}; }
}
class Cartesian2 {
  constructor(x, y) { Object.assign(this, {x, y}); }
}
class Color {
  constructor(css, alpha = 1) { Object.assign(this, {css, alpha}); }
  static fromCssColorString(css) { return new Color(css); }
  withAlpha(alpha) { return new Color(this.css, alpha); }
}
Color.BLACK = new Color('#000000');
Color.WHITE = new Color('#ffffff');
class CallbackProperty {
  constructor(callback, isConstant) { Object.assign(this, {callback, isConstant}); }
  getValue() { return this.callback(); }
}
class Viewer {
  constructor(container, options) {
    this.options = options;
    this.zoomCalls = 0;
    this.entities = {
      values: [],
      add(entity) { this.values.push(entity); return entity; },
      remove(entity) { this.values.splice(this.values.indexOf(entity), 1); },
      removeAll() { this.values.length = 0; },
    };
    viewers.push(this);
  }
  zoomTo() { this.zoomCalls++; }
  destroy() { this.destroyed = true; this.entities.removeAll(); }
}
function createCanvas() {
  const calls = [];
  const ctx = new Proxy({}, {
    get(target, key) {
      if (key in target) return target[key];
      return (...args) => calls.push({method: key, args, ...target});
    },
  });
  const image = `data:image/mock,${canvases.size}`;
  canvases.set(image, calls);
  return {getContext: () => ctx, toDataURL: () => image};
}
const targets = Array.from({length: 5}, (_, i) => ({
  id: `T${i + 1}`, lat: 35 + i * 0.1, lng: -100 + i * 0.1,
  alt_km: i * 0.15, type: 'a',
}));
const members = targets.slice(0, 4).map((target, i) => ({
  drone_id: String(i + 1), waypoint: target.id,
}));
const context = {
  state: {env: {airports: [], targets, sams: []}, routes: {}, visitedTargets: [],
    trajectoryVisible: {'1': false}, timeSync: null, initialEnvSnapshot: null},
  missionState: {draftSolution: null},
  document: {createElement: name => { assert.equal(name, 'canvas'); return createCanvas(); }},
  fetch: async () => ({ok: true, json: async () => ({})}),
  Date: class extends Date { static now() { return wallMs; } },
  $: () => null,
  appendDebugLine() {},
  Cesium: {
    Viewer, Cartesian3, Cartesian2, CallbackProperty, Color,
    LabelStyle: {FILL_AND_OUTLINE: 'fill-and-outline'},
    VerticalOrigin: {BOTTOM: 'bottom', CENTER: 'center'},
    HeadingPitchRange: class {},
    Math: {toRadians: angle => angle * Math.PI / 180},
    TileMapServiceImageryProvider: class {},
    buildModuleUrl: path => path,
  },
};
vm.createContext(context);
vm.runInContext(planner.slice(syncStart, syncEnd) + '\n' + source + '\nthis.subject = cesiumView;', context);

const ringEntities = viewer => viewer.entities.values.filter(entity => entity.name.includes(' ToT'));
const outerRings = viewer => ringEntities(viewer).filter(entity => !entity.name.endsWith(' inner'));
const entity = (viewer, name) => viewer.entities.values.find(item => item.name === name);
const close = (a, b) => assert(Math.abs(a - b) < 1e-10, `${a} != ${b}`);

async function main() {
  const subject = context.subject;
  const viewer = await subject.init('test');
  assert.equal(viewer.options.requestRenderMode, false,
    'Cesium keeps sampling pulse properties while mission playback is stopped');
  subject.render();
  assert.equal(ringEntities(viewer).length, 0, 'imported choices alone have no solved markers');

  context.missionState.draftSolution = {timeSync: {
    enabled: true, status: 'synchronized', synchronized_waypoints: members,
  }};
  subject.render();
  assert.equal(ringEntities(viewer).length, 8, 'four selected targets each have two rings');
  assert.equal(outerRings(viewer).length, 4);
  assert(!ringEntities(viewer).some(item => item.name.startsWith('T5 ')), 'unselected target has no ring');
  for (let i = 0; i < 4; i++) {
    const ring = entity(viewer, `T${i + 1} ToT D${i + 1}`);
    assert.deepEqual(ring.position, entity(viewer, `T${i + 1}`).position,
      'ring shares the target longitude, latitude and elevation');
    assert.equal(ring.label.text, `SYNC D${i + 1}`);
    assert.equal(ring.label.fillColor.css, '#cffafe');
    assert.equal(ring.billboard.scale.isConstant, false);
    close(ring.billboard.scale.getValue() * 20, 16.5);
    const art = canvases.get(ring.billboard.image);
    assert.equal(art.find(call => call.method === 'stroke').strokeStyle, '#06b6d4');
    assert.equal(art.find(call => call.method === 'fill').fillStyle, 'rgba(6, 182, 212, 0.16)');
  }

  // Pulse is wall-clock decoration: it changes without a scene redraw,
  // an active flight, or an advancing Cesium/mission clock.
  const ring = outerRings(viewer)[0];
  const originalEntities = viewer.entities.values.slice();
  wallMs = 220 * Math.PI / 2;
  close(ring.billboard.scale.getValue() * 20, 20);
  close(ring.label.pixelOffset.getValue().y, -24);
  assert.deepEqual(viewer.entities.values, originalEntities, 'pulsing does not rebuild the scene');

  // Accepted data takes precedence over a stale draft, exactly as Grid/Map.
  context.state.timeSync = {enabled: true, status: 'infeasible', synchronized_waypoints: members};
  context.state.visitedTargets = ['T1', 'T2'];
  subject.render();
  assert.equal(ringEntities(viewer).length, 8, 'visited targets and hidden route flags do not hide ToT');
  const blocked = outerRings(viewer)[0];
  assert.equal(blocked.label.text, 'SYNC BLOCKED D1');
  assert.equal(blocked.label.fillColor.css, '#fde68a');
  const blockedArt = canvases.get(blocked.billboard.image);
  assert.equal(blockedArt.find(call => call.method === 'stroke').strokeStyle, '#f59e0b');
  assert.deepEqual(Array.from(blockedArt.find(call => call.method === 'setLineDash').args[0]), [5, 4]);
  close(blocked.billboard.scale.getValue() * 20, 15.45);
  wallMs += 1234;
  close(blocked.billboard.scale.getValue() * 20, 15.45);
  assert.equal(viewer.zoomCalls, 1, 'draft/status redraws retain the camera');

  // A target already removed from the live segment remains available from
  // the mission snapshot; a live target must win over an old snapshot copy.
  context.state.initialEnvSnapshot = {targets: targets.map(t => ({...t, lng: t.lng + 10}))};
  context.state.env.targets = targets.slice(1);
  subject.render({keepCamera: true});
  assert.equal(entity(viewer, 'T1 ToT D1').position.lng, targets[0].lng + 10);
  assert.equal(entity(viewer, 'T2 ToT D2').position.lng, targets[1].lng);
  assert.equal(ringEntities(viewer).length, 8);

  for (const summary of [null, {enabled: false, status: 'synchronized', synchronized_waypoints: members},
    {enabled: true, status: 'skipped', synchronized_waypoints: members}]) {
    context.state.timeSync = summary;
    context.missionState.draftSolution = null;
    subject.render({keepCamera: true});
    assert.equal(ringEntities(viewer).length, 0, 'reset/import/disabled/skipped summary removes old markers');
  }

  context.state.timeSync = {enabled: true, status: 'synchronized', synchronized_waypoints: members};
  subject.render({keepCamera: true});
  subject.render({keepCamera: true});
  assert.equal(ringEntities(viewer).length, 8, 'repeated redraw does not accumulate rings');
  subject.destroy();
  assert.equal(subject.isActive(), false);
  assert.equal(viewer.destroyed, true);
  assert.equal(viewer.entities.values.length, 0, 'leaving 3D disposes marker entities');
  const nextViewer = await subject.init('test');
  subject.render();
  assert.equal(ringEntities(nextViewer).length, 8, 'returning to 3D rebuilds current markers');
  assert.equal(nextViewer.zoomCalls, 1);
  subject.destroy();
  console.log('PASS: 3D ToT draft/accepted markers, pulse, status, target elevation, snapshot fallback, reset and view lifecycle');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
