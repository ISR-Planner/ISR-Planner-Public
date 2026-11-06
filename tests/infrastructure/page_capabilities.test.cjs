const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../webapp/page_capabilities.js'), 'utf8');
function setup() {
  const window = { weatherCheckpoints: { configure() {} } };
  vm.runInNewContext(source, { window });
  return window;
}
const options = (weatherPreview = false, weatherDiagnostics = false) => ({ weatherPreview, weatherDiagnostics });

test('declarations select extensions in dependency order, regardless of script presence', () => {
  for (const flags of [options(), options(true), options(true, true)]) {
    const { PageCapabilities: api } = setup();
    const calls = [];
    api.register('weatherDiagnostics', () => calls.push('diagnostic'));
    api.register('weatherPreview', () => calls.push('preview'));
    assert.deepEqual(calls, []);
    api.initialize(flags);
    assert.deepEqual(calls, flags.weatherDiagnostics ? ['preview', 'diagnostic'] : flags.weatherPreview ? ['preview'] : []);
    assert.equal(Object.isFrozen(api.capabilities), true);
    flags.weatherPreview = !flags.weatherPreview;
    assert.notEqual(api.capabilities.weatherPreview, flags.weatherPreview);
    assert.throws(() => api.initialize(options()), /already initialized/);
    assert.throws(() => api.register('weatherPreview', () => {}), /already initialized/);
  }
});

test('startup rejects missing dependencies before installing any extension', () => {
  const root = setup();
  const api = root.PageCapabilities;
  let calls = 0;
  api.register('weatherPreview', () => calls++);
  assert.throws(() => api.initialize(options(true, true)), /Missing page extension: weatherDiagnostics/);
  assert.equal(calls, 0);
  delete root.weatherCheckpoints;
  assert.throws(() => api.initialize(options(true)), /Missing weather checkpoint/);
  assert.equal(calls, 0);
});

test('invalid declarations and duplicate registrations fail explicitly', () => {
  const { PageCapabilities: api } = setup();
  for (const flags of [null, {}, { ...options(), extra: true }, options('yes'), options(false, true)]) {
    assert.throws(() => api.initialize(flags), /Invalid page capabilities/);
  }
  assert.throws(() => api.register('unknown', () => {}), /Invalid or duplicate/);
  api.register('weatherPreview', () => {});
  assert.throws(() => api.register('weatherPreview', () => {}), /Invalid or duplicate/);
});


test('real extension entrypoints remain inert when registered on a disabled page', () => {
  const window = setup();
  const context = vm.createContext({ window });
  for (const file of ['weather_preview.js', '_weather_diagnostics/runner.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../webapp', file), 'utf8'), context);
  }
  // No DOM, timers, map, or application state exists in this context.
  window.PageCapabilities.initialize(options());
  assert.equal(window.weatherPreview, undefined);
});
