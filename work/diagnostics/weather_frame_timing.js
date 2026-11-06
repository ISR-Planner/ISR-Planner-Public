// Offline diagnostic: execute the actual resume loop with controlled frame
// timestamps. This does not measure Chrome's real foreground/background FPS.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('webapp/isr.js', 'utf8');
const mission = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const speed = mission.drone_configs['1'].speed;
const helperStart = source.indexOf('const ANIMATION_BASE_MISSION_SECONDS_PER_WALL_SECOND');
const helpers = source.slice(helperStart, source.indexOf('// Killed drones', helperStart));
const resumeStart = source.indexOf('function resumeAnimation()');
const resume = source.slice(resumeStart, source.indexOf('\n/**', resumeStart));
const rows = [];
for (const frameMs of [1000 / 60, 250, 1000]) {
  for (const requested of [1, 4, 16, 64, 128]) {
    let callback;
    const drone = {animating: true, distanceTraveled: 0, totalDistance: 100000};
    const context = {
      state: {
        animation: {active: false, speedMultiplier: requested, elapsedMissionSeconds: 0},
        droneConfigs: {'1': {speed}}, legFuel: {}, routes: {},
      },
      missionState: {pauseContext: {droneStates: {'1': drone}, animatingDrones: ['1']}},
      MissionMode: {ANIMATING: 'ANIMATING'},
      requestAnimationFrame: fn => { callback = fn; return 1; },
      appendDebugLine: () => {}, drawEnvironment: () => {},
      updateStatsFromAnimation: () => {}, updateAnimationButtonStates: () => {},
      setMissionMode: () => {},
    };
    vm.createContext(context);
    vm.runInContext(helpers + '\n' + resume + '\nresumeAnimation();', context);
    callback(0);
    const frames = Math.round(20000 / frameMs);
    for (let i = 1; i <= frames; i++) callback(i * frameMs);
    const wallSeconds = frames * frameMs / 1000;
    const missionSeconds = context.state.animation.elapsedMissionSeconds;
    const error = drone.distanceTraveled - missionSeconds * speed / 60;
    assert(Math.abs(error) < 1e-7, 'Movement and weather clock differ');
    rows.push({frameMs: Number(frameMs.toFixed(2)), requested,
      wallSeconds, missionSeconds: Number(missionSeconds.toFixed(3)),
      effectiveMultiplier: Number((missionSeconds / wallSeconds).toFixed(3)),
      lostWallSeconds: Number((wallSeconds - missionSeconds / requested).toFixed(3)),
      clockDistanceErrorKm: Number(error.toFixed(8))});
  }
}
console.log(JSON.stringify({kind: 'offline controlled frame timestamps',
  aircraftSpeedKmPerHour: speed * 60, rows}, null, 2));
