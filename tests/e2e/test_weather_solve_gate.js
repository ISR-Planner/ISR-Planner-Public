// A blocked click must explain the next action where the operator clicked.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('webapp/weather_preview.js', 'utf8');
const start = source.indexOf('  function reportWeatherSolveBlock(');
const end = source.indexOf('  let lastFollowTime=', start);
assert(start >= 0 && end > start);

(async () => {
  for (const gate of ['dirty', 'retrieving', 'failed', 'checkpoint', 'ready']) {
    const elements = new Map();
    let solves = 0;
    const logs = [];
    const context = {
      get: id => {
        if (!elements.has(id)) elements.set(id, {textContent: id === 'wp-checkpoint-status' ? 'Paused for weather: review required.' : ''});
        return elements.get(id);
      },
      snapshot: gate === 'dirty' ? {dirty: true} : null,
      retrieving: gate === 'retrieving', weatherFailed: gate === 'failed',
      window: {weatherCheckpoints: {allowSolve: async () => gate !== 'checkpoint'}},
      appendDebugLine: text => logs.push(text),
      runPlanner: async () => { solves++; return {success: true}; },
    };
    vm.createContext(context);
    vm.runInContext(source.slice(start, end), context);
    const result = await context.runPlanner();
    if (gate === 'ready') {
      assert.equal(solves, 1);
      assert.equal(result.success, true);
      assert.equal(logs.length, 0);
    } else {
      assert.equal(solves, 0);
      assert.equal(result, undefined);
      assert.equal(context.get('wp-solve-mode').textContent, 'Planner has not started');
      assert.equal(context.get('mc-status-text').textContent, context.get('wp-status').textContent);
      assert.match(logs[0], /Solve blocked:/);
      if (gate === 'dirty') {
        assert.match(context.get('mc-status-text').textContent, /Update forecast, then Run Planner/);
        context.snapshot = null;
        assert.equal((await context.runPlanner()).success, true);
        assert.equal(solves, 1);
      }
    }
  }
  console.log('Weather solve gates explain blocked attempts in Mission Control; a current selection solves.');
})().catch(error => {console.error(error); process.exitCode = 1;});
