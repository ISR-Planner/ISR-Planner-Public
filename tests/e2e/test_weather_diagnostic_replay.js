// Exercise diagnostic retrieval/import behavior without browser access.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const capture = JSON.parse(fs.readFileSync(
  'webapp/_weather_diagnostics/noaa_20261012T012337Z.json', 'utf8'));
const elements = new Map();
let recordedRequests = 0, solveRequests = 0, retrieved;
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    style: {}, value: '', textContent: '',
    dispatchEvent: () => {}, append: () => {}, click: () => {},
  });
  return elements.get(id);
}
const context = {
  window: {
    fetch: async url => {
      if (url === '/static/_weather_diagnostics/noaa_20261012T012337Z.json') {
        recordedRequests++;
        return new Response(JSON.stringify(capture));
      }
      if (url.includes('/api/solve_with_allocation')) {
        solveRequests++;
        return new Response(JSON.stringify({success: true, routes: {}}));
      }
      throw Error('Unexpected live retrieval: ' + url);
    },
    requestAnimationFrame: () => 1,
  },
  document: {
    createElement: () => ({style: {}}),
    body: {style: {}, append: () => {}},
    getElementById: element,
  },
  loadFromJson: () => {},
  stopAnimation: () => {}, resetMission: () => {},
  runPlanner: async () => {throw Error('Fixture solve failure');},
  Response, Event, Date, JSON, setTimeout: fn => {queueMicrotask(fn); return 1;},
};
element('wp-get').onclick = async () => {
  retrieved = await (await context.window.fetch('/api/weather/current')).json();
  element('wp-get').textContent = '✓ Forecast loaded';
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('webapp/page_capabilities.js', 'utf8'), context);
context.window.weatherCheckpoints = { configure() {} };
context.window.PageCapabilities.register('weatherPreview', () => {});
vm.runInContext(fs.readFileSync('webapp/_weather_diagnostics/runner.js', 'utf8'), context);
context.window.PageCapabilities.initialize({ weatherPreview: true, weatherDiagnostics: true });
(async () => {
  context.loadFromJson({env: {targets: []}}, 'mission-one.json');
  await element('wp-get').onclick();
  assert.equal(recordedRequests, 1);
  assert.deepEqual(retrieved, capture);
  assert.equal(element('wp-zone').value, 'UTC');
  assert.equal(element('wp-start').value, '2026-10-12T01:24');
  assert.equal(element('wp-end').value, '2026-10-12T17:24');
  assert.equal(element('wp-get').textContent, '✓ Recorded NOAA loaded');
  // Editing the selected time filters the same retained capture.
  element('wp-start').value = '2026-10-12T02:00';
  await element('wp-get').onclick();
  assert.equal(recordedRequests, 1);
  assert.equal(element('wp-start').value, '2026-10-12T02:00');
  await context.window.fetch('/api/solve_with_allocation');
  await context.window.fetch('/api/solve_with_allocation');
  assert.equal(solveRequests, 2); // Manual planning/replanning never reuses a timing response.
  // A different imported mission cannot inherit the previous cached solution.
  context.loadFromJson({env: {targets: [{id: 'T1'}]}}, 'mission-two.json');
  await element('wp-get').onclick();
  assert.equal(element('wp-start').value, '2026-10-12T01:24');
  await context.window.fetch('/api/solve_with_allocation');
  assert.equal(solveRequests, 3);
  assert.equal(recordedRequests, 1);
  const controls={'wp-auto-checkpoint':true,'wp-no-fly':true,'wp-other':false,'mc-speed-boost':true};
  for(const [id,checked] of Object.entries(controls))element(id).checked=checked;
  await element('diag-run').onclick();
  for(const [id,checked] of Object.entries(controls))assert.equal(element(id).checked,checked);
  assert.equal(element('diag-run').disabled,false);
  await element('diag-background').onclick();
  for(const [id,checked] of Object.entries(controls))assert.equal(element(id).checked,checked);
  console.log('Recorded NOAA replay preserves data and timestamps; manual replans request fresh solver results.');
  console.log('Timing diagnostics restore checkpoint and overlay preferences after failure.');
})().catch(error => {console.error(error); process.exitCode = 1;});
