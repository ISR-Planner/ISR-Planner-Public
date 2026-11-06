// The aircraft config stores km/min; weather uses epoch milliseconds.
// One wall second at 1× is exactly one mission second.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('webapp/isr.js', 'utf8');
const start = source.indexOf('const ANIMATION_BASE_MISSION_SECONDS_PER_WALL_SECOND');
const end = source.indexOf('// Killed drones', start);
for (const multiplier of [1, 4, 128, 1280]) {
  const context = {state: {animation: {speedMultiplier: multiplier, elapsedMissionSeconds: 0}}};
  vm.createContext(context);
  vm.runInContext(source.slice(start, end) + '\nadvanceMissionClock(1);', context);
  assert.equal(context.state.animation.elapsedMissionSeconds, multiplier);
  // At 60 km/h, a mission second corresponds to 1/60 km of flight.
  assert.equal(context.state.animation.elapsedMissionSeconds / 60, multiplier / 60);
}
console.log('Weather clock units pass at 1×, 4×, 128× and 1280×.');
// Travel and weather time must agree independently of frame size or speed changes.
for (const speeds of [[1], [4], [128], [1280], [1,1280,4]]) {
  const context={state:{animation:{speedMultiplier:1,elapsedMissionSeconds:0,
    drones:{'1':{animating:true,distanceTraveled:0,totalDistance:20}}},droneConfigs:{'1':{speed:10}}}};
  vm.createContext(context);vm.runInContext(source.slice(start,end),context);
  for(let tick=0;tick<20000&&context.state.animation.drones['1'].distanceTraveled<20-1e-9;tick++){
    context.state.animation.speedMultiplier=speeds[tick%speeds.length];
    const minutes=vm.runInContext('advanceMissionClock(0.1)',context);
    context.state.animation.drones['1'].distanceTraveled+=minutes*10;
  }
  assert(Math.abs(context.state.animation.elapsedMissionSeconds-120)<1e-7);
  assert(Math.abs(context.state.animation.drones['1'].distanceTraveled-20)<1e-7);
}
console.log('Flight and weather remain aligned through speed changes and completion.');
