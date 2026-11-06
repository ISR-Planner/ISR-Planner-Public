const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createCesiumFixture } = require('./cesium-fixture.cjs');
const source = fs.readFileSync(path.join(__dirname, '../../webapp/cesium_view.js'), 'utf8');
function setup() {
  const fixture = createCesiumFixture();
  const context = vm.createContext({
    Cesium: fixture.engine,
    state: { env: { airports: [], targets: [], sams: [] }, droneConfigs: {}, routes: {} },
    $: () => null, appendDebugLine() {},
    fetch: async () => ({ ok: true, json: async () => ({}) }),
  });
  const api = vm.runInContext(source + '\ncesiumView;', context);
  return { ...fixture, api, context };
}

test('init delivers created and reused viewers before promise consumers; reopen retains subscriptions', async () => {
  const { api, viewers } = setup();
  const calls = [];
  const unsubscribe = api.subscribeLifecycle({ onInit: value => calls.push(value) });
  assert.deepEqual(calls, [], 'registration does not replay initialization');
  const first = await api.init('neutral-view').then(value => {
    assert.equal(calls.at(-1), value);
    return value;
  });
  assert.equal(await api.init('ignored-while-open'), first);
  assert.deepEqual(calls, [first, first]);
  api.destroy();
  assert.equal(first.destroyed, true);
  const second = await api.init('reopened-view');
  assert.notEqual(second, first);
  assert.equal(second.containerId, 'reopened-view');
  assert.equal(calls.at(-1), second);
  assert.equal(viewers.length, 2);
  unsubscribe(); unsubscribe();
  await api.init('still-open');
  assert.equal(calls.length, 3);
});

test('overlays run after scene clearing for both camera paths, preserve options and render return', async () => {
  const { api, events } = setup();
  const viewer = await api.init('neutral');
  const calls = [];
  const off = api.subscribeLifecycle({ onRender: (value, opts) => {
    assert.equal(value, viewer);
    assert.equal(viewer.entities.values.length, 0, 'base removed the previous overlay first');
    calls.push(opts);
    events.push('overlay');
    viewer.entities.add({ id: 'neutral-overlay' });
  } });
  const options = { keepCamera: true };
  assert.equal(api.render(options), undefined);
  assert.equal(calls[0], options);
  assert.deepEqual(events, ['overlay']); // keepCamera with an initially empty scene
  events.length = 0;
  api.render({ keepCamera: false });
  assert.deepEqual(events, ['clear:0', 'overlay']);
  events.length = 0;
  api.render(options);
  assert.deepEqual(events, ['remove:0', 'overlay']);
  off(); off();
  api.render();
  assert.equal(viewer.entities.values.length, 0);
  assert.equal(calls.length, 3);
});

test('notification order is stable; removals apply immediately and additions wait for the next emission', () => {
  const { api } = setup();
  const calls = [];
  let offSecond;
  let installed = false;
  api.subscribeLifecycle({ onRender() {
    calls.push('first');
    offSecond();
    if (!installed) {
      installed = true;
      api.subscribeLifecycle({ onRender: () => calls.push('third') });
    }
  } });
  offSecond = api.subscribeLifecycle({ onRender: () => calls.push('second') });
  api.render();
  assert.deepEqual(calls, ['first']);
  api.render();
  assert.deepEqual(calls, ['first', 'first', 'third']);
});

test('base failures suppress notifications; observer failures propagate and stop notification', async () => {
  const { api, engine } = setup();
  let calls = 0;
  api.subscribeLifecycle({ onInit: () => calls++ });
  const Viewer = engine.Viewer;
  engine.Viewer = class { constructor() { throw new Error('base init failed'); } };
  await assert.rejects(api.init('neutral'), /base init failed/);
  assert.equal(calls, 0);
  engine.Viewer = Viewer;
  const viewer = await api.init('neutral');
  const baseError = new Error('base render failed');
  const removeAll = viewer.entities.removeAll;
  viewer.entities.removeAll = () => { throw baseError; };
  const order = [];
  const observerError = new Error('observer failed');
  api.subscribeLifecycle({ onRender() { order.push('first'); throw observerError; } });
  api.subscribeLifecycle({ onRender: () => order.push('second') });
  assert.throws(() => api.render({ keepCamera: false }), error => error === baseError);
  assert.deepEqual(order, []);
  viewer.entities.removeAll = removeAll;
  assert.throws(() => api.render({ keepCamera: false }), error => error === observerError);
  assert.deepEqual(order, ['first']);
  api.subscribeLifecycle({ onInit() { throw observerError; } });
  await assert.rejects(api.init('cached'), error => error === observerError);
});

test('unavailable engine publishes null and no-viewer render still notifies, matching the prior wrappers', async () => {
  const { api, context } = setup();
  delete context.Cesium;
  let initialized = 'pending', rendered = 'pending';
  api.subscribeLifecycle({ onInit: value => { initialized = value; }, onRender: value => { rendered = value; } });
  assert.equal(await api.init('neutral'), null);
  assert.equal(initialized, null);
  assert.equal(api.render(), undefined);
  assert.equal(rendered, null);
});

test('invalid subscriptions fail without registering callbacks', () => {
  const { api } = setup();
  for (const invalid of [undefined, {}, { onInit: true }, { onRender: 'bad' }]) {
    assert.throws(() => api.subscribeLifecycle(invalid), /Lifecycle subscription/);
  }
});
