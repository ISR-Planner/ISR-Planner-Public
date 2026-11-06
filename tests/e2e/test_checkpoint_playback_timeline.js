// A weather/manual cut must preserve the aircraft's actual position after loiter.
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
const plain = x => JSON.parse(JSON.stringify(x));
const path = [[0, 0], [0, .1], [0, .1], [0, .2], [0, .3]];
const c = vm.createContext({});
vm.runInContext(fs.readFileSync('webapp/latlng_utils.js', 'utf8'), c);
vm.runInContext(extract('split_polyline_at_distance'), c);
const leg = c.haversineKm(0, 0, 0, .1);
const timeline = [0, leg, leg + 100, leg * 2 + 100, leg * 3 + 100];
const original = JSON.stringify({path, timeline});
for (const distance of [leg, 50, leg + 100]) {
  const split = c.split_polyline_at_distance(path, distance, timeline);
  assert.deepEqual(plain(split.splitPoint), [0, .1], 'Hold does not advance the checkpoint');
  assert.deepEqual(plain(split.suffixPoints.at(-1)), [0, .3]);
}
const afterHold = c.split_polyline_at_distance(path, leg * 1.5 + 100, timeline);
assert(Math.abs(afterHold.splitPoint[1] - .15) < 1e-10);
assert.deepEqual(plain(afterHold.suffixPoints.slice(1)), [[0, .2], [0, .3]]);
assert.equal(JSON.stringify({path, timeline}), original, 'No frame mutation');
assert.equal(c.split_polyline_at_distance(path, leg * 1.5).splitIndex, 2,
  'Explicit geometric callers retain their existing distance meaning');
assert.throws(() => c.split_polyline_at_distance(path, 50, [0, 1]), /timeline/);
assert.throws(() => c.split_polyline_at_distance(path, 50, [0, 5, 4, 6, 7]), /timeline/);

async function cut(distance, expectedLng) {
  const env = {airports: [{id: 'A1', lat: 0, lng: 0}, {id: 'A2', lat: 0, lng: .3}],
    targets: [{id: 'T1', lat: 0, lng: .1}, {id: 'T2', lat: 0, lng: .2}]};
  c.state = {env, initialEnvSnapshot: plain(env), visitedTargets: [], legFuel: {'1': distance},
    droneConfigs: {'1': {fuel_budget: 500}}, routes: {'1': {route: ['A1', 'T1', 'T2', 'A2'], trajectory: plain(path)}},
    animation: {active: true, missionDistance: distance, drones: {'1': {
      animating: true, distanceTraveled: distance, totalDistance: timeline.at(-1),
      cumulativeDistances: timeline, legBurnedDistance: distance}}}};
  c.missionState = {};
  c.MissionMode = {CHECKPOINT: 'CHECKPOINT'};
  for (const name of ['appendDebugLine', 'setAddMode', 'updateEnvPanelLocked',
    'updateCanvasPointerEvents', 'requestDraw', 'setMissionMode']) c[name] = () => {};
  c.getAllKnownTargets = () => c.state.env.targets;
  c.targetHitToleranceKm = () => .05;
  c.markTargetVisitedByDrone = id => c.state.visitedTargets.push(id);
  vm.runInContext(extract('prepareCutFromCurrentState'), c);
  const result = await c.prepareCutFromCurrentState();
  assert(Math.abs(result.cutPositions['1'][1] - expectedLng) < 1e-10);
  assert.equal(c.state.pendingCheckpoints['1'].id, 'C1-1');
  assert.deepEqual(c.state.visitedTargets, ['T1'], 'Unreached T2 must remain unvisited');
  assert.equal(c.state.droneConfigs['1'].start_fuel, 500 - distance);
  assert.equal(c.state.droneConfigs['1'].fuel_budget, 500);
  assert.equal(c.state.animation.active, false);
  assert.deepEqual(plain(c.state.checkpoint.segments['1'].suffix.at(-1)), [0, .3]);
}
(async () => {
  await cut(50, .1);
  await cut(leg * 1.5 + 100, .15);
  console.log('PASS checkpoint position, remaining path, visited targets and fuel during/after loiter');
})().catch(error => { console.error(error); process.exitCode = 1; });
