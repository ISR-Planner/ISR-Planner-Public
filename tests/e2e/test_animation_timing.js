// Run with: node tests/e2e/test_animation_timing.js
// These are offline tests of the shipped callbacks, not a browser FPS test.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const source = fs.readFileSync('webapp/isr.js', 'utf8');
const helperStart = source.indexOf('const ANIMATION_BASE_MISSION_SECONDS_PER_WALL_SECOND');
const helpers = source.slice(helperStart, source.indexOf('// Killed drones', helperStart));
const resumeStart = source.indexOf('function resumeAnimation()');
const resume = source.slice(resumeStart, source.indexOf('\n/**', resumeStart));
const pauseStart = source.indexOf('function pauseAnimation()');
const pause = source.slice(pauseStart, source.indexOf('\n/**', pauseStart));
const loopStart = source.indexOf('  // Start animation loop', source.indexOf('function startAnimation('));
const loopEnd = source.indexOf('  // Transition to ANIMATING state', loopStart);
assert(helperStart >= 0 && resumeStart >= 0 && pauseStart >= 0 && loopStart >= 0 && loopEnd > loopStart);
const startLoop = 'function startLoopForTest() {\n' + source.slice(loopStart, loopEnd) + '\n}';
const droneInitStart = source.indexOf('    state.animation.drones[did] = {',
  source.indexOf('const launchDelay = Math.max(0, Number(routeInfo.launch_delay'));
const droneInitEnd = source.indexOf('\n    };', droneInitStart) + '\n    };'.length;
assert(droneInitStart >= 0 && droneInitEnd > droneInitStart);
const initializeDrone = source.slice(droneInitStart, droneInitEnd);

let passed = 0;
let failed = 0;
function test(name, fn) {
  try {
    fn();
    passed++;
    console.log('PASS ' + name);
  } catch (error) {
    failed++;
    console.error('FAIL ' + name + ': ' + error.message);
  }
}
function close(actual, expected, message) {
  assert(Math.abs(actual - expected) < 1e-7, `${message}: expected ${expected}, got ${actual}`);
}

function harness({mode = 'resume', multiplier = 128, drones, routes = {}} = {}) {
  let callback = null;
  let wallMs = 0;
  const states = {};
  const configs = {};
  for (const [id, values] of Object.entries(drones || {'1': {}})) {
    const {speed = 10, ...rest} = values;
    configs[id] = {speed};
    states[id] = {
      animating: true, distanceTraveled: 0, totalDistance: 10000,
      legBurnedDistance: 0, _launchDelayRemaining: 0,
      _pauseRemaining: 0, _waypointDistances: [], _wpPauseIdx: 0,
      cumulativeDistances: [], ...rest,
    };
  }
  const context = {
    state: {
      animation: {active: false, speedMultiplier: multiplier, elapsedMissionSeconds: 0,
        drones: states, missionDistance: 0, segmentStartOffset: 0},
      droneConfigs: configs, legFuel: {}, legFuelHistory: {}, routes,
      visitedTargets: [], env: {targets: []}, initialEnvSnapshot: {targets: []},
    },
    missionState: {pauseContext: {droneStates: states, animatingDrones: Object.keys(states)}},
    MissionMode: {ANIMATING: 'ANIMATING', PAUSED_MID_ANIMATION: 'PAUSED', READY_TO_ANIMATE: 'READY'},
    performance: {now: () => wallMs},
    Date: class extends Date { static now() { return wallMs; } },
    requestAnimationFrame: fn => { callback = fn; return 1; },
    cancelAnimationFrame: () => { callback = null; },
    appendDebugLine: () => {}, drawEnvironment: () => {},
    updateStatsFromAnimation: () => {}, updateAnimationButtonStates: () => {},
    setMissionMode: mode => { context.missionState.mode = mode; },
    getUiPermissions: () => ({canPause: true}),
    finishedDuringLiveEdit: () => false,
    missionReplay: {
      getNextSegment: () => null, getSegmentCount: () => 0,
      getSegment: () => null, checkSegmentBoundary: () => ({switched: false}),
    },
    segmentedImport: {isActive: () => false},
  };
  context.window = {};
  vm.createContext(context);
  vm.runInContext(helpers + '\n' + pause + '\n' + resume + '\n' + startLoop, context);
  if (mode === 'start') {
    context.state.animation.active = true;
    context.missionState.pauseContext = null;
    vm.runInContext('startLoopForTest()', context);
  } else {
    vm.runInContext('resumeAnimation()', context);
  }
  const frame = timestamp => {
    wallMs = timestamp;
    assert(callback, 'Expected an animation callback');
    const pending = callback;
    callback = null;
    pending(timestamp);
  };
  frame(0);
  return {context, frame, run: code => vm.runInContext(code, context),
    setWall: timestamp => { wallMs = timestamp; }};
}

test('initial animation state honors scheduled zero airport pause and keeps the legacy default', () => {
  for (const [frame, expectedSeconds] of [[{airport_pause_seconds: 0}, 0], [{}, 1]]) {
    const h = harness({routes: {'1': {anim_frame: frame}}});
    h.run(`(() => {
      const did = '1', totalDistance = 44, initialDistance = 0, _seedBurned = 0;
      const launchDelay = 3, cumulativeDistances = [0, 10, 24, 30, 44];
      const _waypointDistances = [{wp: 'A1', dist: 0}], refuelDistance = null;
      ${initializeDrone}
    })()`);
    close(h.context.state.animation.drones['1']._pauseRemaining, expectedSeconds / 60,
      'Initial airport pause in mission minutes');
    h.run("advanceDroneMissionTime('1', state.animation.drones['1'], 3)");
    close(h.context.state.animation.drones['1'].distanceTraveled, 0, 'Ground launch delay does not fly');
    close(h.context.state.animation.drones['1'].legBurnedDistance, 0, 'Ground launch delay consumes no fuel');
  }
});

for (const mode of ['start', 'resume']) {
  for (const multiplier of [1, 4, 16, 64, 128, 1280, 12800]) {
    for (const frameMs of [1000 / 60, 250, 1000, 20000]) {
      test(`${mode}: ${multiplier}x preserves wall time with ${frameMs.toFixed(2)} ms callbacks`, () => {
        const h = harness({mode, multiplier, drones: {'1': {totalDistance: 100000}}});
        const frameCount = Math.round(20000 / frameMs);
        for (let i = 1; i <= frameCount; i++) h.frame(i * frameMs);
        const expectedSeconds = frameCount * frameMs / 1000 * multiplier;
        close(h.context.state.animation.elapsedMissionSeconds, expectedSeconds, 'Mission seconds');
        close(h.context.state.animation.drones['1'].distanceTraveled,
          expectedSeconds / 6, 'Flight distance');
        close(h.context.state.legFuel['1'], expectedSeconds / 6, 'Fuel');
      });
    }
  }

  test(`${mode}: one frame consumes travel, both refuels, and remaining travel`, () => {
    const h = harness({mode, drones: {'1': {_waypointDistances: [
      {wp: 'A2', dist: 1, isRefuel: true}, {wp: 'A3', dist: 2, isRefuel: true},
    ]}}});
    h.frame(1000); // 128 mission seconds = 126 traveling + two 1-second pauses.
    const s = h.context.state;
    close(s.animation.elapsedMissionSeconds, 128, 'Mission seconds');
    close(s.animation.drones['1'].distanceTraveled, 21, 'Flight distance');
    assert.equal(s.animation.drones['1']._wpPauseIdx, 2);
    assert.deepEqual(Array.from(s.legFuelHistory['1']), [1, 1]);
    close(s.legFuel['1'], 19, 'Fuel since second refuel');
  });

  test(`${mode}: waypoint arrival consumes the remainder of the current frame`, () => {
    const h = harness({mode, drones: {'1': {_waypointDistances: [{wp: 'A2', dist: 1}]}}});
    h.frame(100); // 12.8 mission seconds; 6 flight + 1 pause + 5.8 flight.
    close(h.context.state.animation.elapsedMissionSeconds, 12.8, 'Mission seconds');
    close(h.context.state.animation.drones['1'].distanceTraveled, 11.8 / 6, 'Flight distance');
    close(h.context.state.animation.drones['1']._pauseRemaining, 0, 'Unconsumed airport pause');
  });

  test(`${mode}: finishing a route includes future airport pauses and stops its clock`, () => {
    const h = harness({mode, drones: {'1': {totalDistance: 3, _waypointDistances: [
      {wp: 'A2', dist: 1, isRefuel: true}, {wp: 'A3', dist: 2, isRefuel: true},
    ]}}});
    h.frame(1000);
    const s = h.context.state;
    close(s.animation.elapsedMissionSeconds, 20, '18 flight seconds + 2 airport seconds');
    close(s.animation.drones['1'].distanceTraveled, 3, 'Destination distance');
    close(s.legFuel['1'], 1, 'Final leg fuel');
    assert.equal(s.animation.active, false);
    assert.equal(s.animation.drones['1'].animating, false);
  });

  test(`${mode}: launch delays and different aircraft speeds share one mission clock`, () => {
    const h = harness({mode, drones: {
      '1': {speed: 10, totalDistance: 10, _launchDelayRemaining: 1},
      '2': {speed: 5, totalDistance: 20},
    }});
    h.frame(1000); // 128 mission seconds. D1 finished at 120; D2 still flies.
    close(h.context.state.animation.elapsedMissionSeconds, 128, 'Shared mission seconds');
    close(h.context.state.animation.drones['1'].distanceTraveled, 10, 'Delayed aircraft');
    close(h.context.state.animation.drones['2'].distanceTraveled, 128 / 12, 'Second aircraft');
    h.frame(2000);
    close(h.context.state.animation.elapsedMissionSeconds, 240, 'Completion time of last aircraft');
    close(h.context.state.legFuel['1'], 10, 'First aircraft fuel');
    close(h.context.state.legFuel['2'], 20, 'Second aircraft fuel');
    assert.equal(h.context.state.animation.active, false);
  });

  for (const multiplier of [1, 12800]) {
    test(`${mode}: ToT at ${multiplier}x includes prior loiter and unequal refuels without extra pauses`, () => {
      // D1 flies 30 km at 2 km/min and loiters seven minutes before its
      // selected target: 22 minutes naturally, plus a three-minute launch
      // delay. D2 flies directly for 25 minutes with no intermediate refuel.
      const h = harness({mode, multiplier, routes: {
        '1': {anim_frame: {airport_pause_seconds: 0}},
        '2': {anim_frame: {airport_pause_seconds: 0}},
      }, drones: {
        '1': {speed: 2, totalDistance: 68, _launchDelayRemaining: 3,
          cumulativeDistances: [0, 10, 24, 30, 44, 48, 68],
          _waypointDistances: [{wp: 'A1', dist: 0}, {wp: 'A3', dist: 30, isRefuel: true}]},
        '2': {speed: 1, totalDistance: 50,
          cumulativeDistances: [0, 25, 50], _waypointDistances: [{wp: 'A2', dist: 0}]},
      }});
      h.frame(25 * 60 * 1000 / multiplier);
      const s = h.context.state;
      close(s.animation.elapsedMissionSeconds, 25 * 60, 'Selected arrival mission time');
      close(s.animation.drones['1'].distanceTraveled, 44, 'D1 selected arrival before its own hold');
      close(s.animation.drones['2'].distanceTraveled, 25, 'D2 selected arrival');
      close(s.animation.drones['1']._elapsedHoldMinutes, 3, 'Only launch delay added outside frame');
      assert.deepEqual(Array.from(s.legFuelHistory['1']), [30]);
      close(s.legFuel['1'], 14, 'Fuel since refuel includes prior loiter correctly');
      h.frame(27 * 60 * 1000 / multiplier);
      close(s.animation.drones['1'].distanceTraveled, 48, 'Selected target two-minute hold finishes after ToT');
      close(s.animation.drones['2'].distanceTraveled, 27, 'D2 can leave its selected target immediately');
      assert.deepEqual(Array.from(s.legFuelHistory['1']), [30]);
    });

    test(`${mode}: ToT at ${multiplier}x finishes with no unscheduled airport time`, () => {
      const h = harness({mode, multiplier, routes: {
        '1': {anim_frame: {airport_pause_seconds: 0}},
      }, drones: {'1': {speed: 2, totalDistance: 44, _launchDelayRemaining: 3,
        _waypointDistances: [{wp: 'A1', dist: 0}, {wp: 'A3', dist: 30, isRefuel: true}]}}});
      h.frame(40 * 60 * 1000 / multiplier);
      close(h.context.state.animation.elapsedMissionSeconds, 25 * 60, 'Clock caps at exact arrival');
      close(h.context.state.animation.drones['1'].distanceTraveled, 44, 'Destination distance');
      assert.equal(h.context.state.animation.active, false);
      assert.deepEqual(Array.from(h.context.state.legFuelHistory['1']), [30]);
    });
  }

  for (const frameMs of [1000 / 60, 250, 1000, 60000]) {
    test(`${mode}: delayed/refueled completion is invariant at ${frameMs.toFixed(2)} ms`, () => {
      const h = harness({mode, multiplier: 1, drones: {'1': {
        totalDistance: 3, _launchDelayRemaining: 2 / 60,
        _waypointDistances: [{wp: 'A2', dist: 1, isRefuel: true},
          {wp: 'A3', dist: 2, isRefuel: true}],
      }}});
      for (let frame = 1; h.context.state.animation.active && frame <= 2000; frame++) {
        h.frame(frame * frameMs);
      }
      const s = h.context.state;
      assert.equal(s.animation.active, false);
      close(s.animation.elapsedMissionSeconds, 22, '18 seconds flight + 2 launch + 2 airport');
      close(s.animation.drones['1']._elapsedHoldMinutes, 4 / 60, 'Consumed hold counter');
      close(s.animation.drones['1'].distanceTraveled, 3, 'Final distance');
      assert.equal(s.legFuelHistory['1'].length, 2);
      s.legFuelHistory['1'].forEach((fuel, index) => close(fuel, 1, `Completed fuel leg ${index + 1}`));
      close(s.legFuel['1'], 1, 'Final leg fuel');
    });
  }

  test(`${mode}: airport arrival exactly at a frame boundary records refuel once`, () => {
    const h = harness({mode, multiplier: 1, drones: {'1': {
      _waypointDistances: [{wp: 'A2', dist: 1, isRefuel: true}],
    }}});
    h.frame(6000);
    close(h.context.state.animation.drones['1'].distanceTraveled, 1, 'Arrival distance');
    close(h.context.state.animation.drones['1']._pauseRemaining, 1 / 60, 'Pending departure hold');
    assert.deepEqual(Array.from(h.context.state.legFuelHistory['1']), [1]);
    h.frame(7000);
    close(h.context.state.animation.drones['1'].distanceTraveled, 1, 'Position during airport hold');
    close(h.context.state.animation.drones['1']._elapsedHoldMinutes, 1 / 60, 'Airport hold consumed');
    h.frame(8000);
    close(h.context.state.animation.drones['1'].distanceTraveled, 7 / 6, 'Continued travel');
    assert.deepEqual(Array.from(h.context.state.legFuelHistory['1']), [1]);
  });

  for (const multiplier of [128, 1280, 12800]) {
    test(`${mode}: ${multiplier}x stops exactly at a weather boundary`, () => {
      const h = harness({mode, multiplier});
      let held = false;
      let released = false;
      h.context.window.weatherCheckpoints = {limitStep: seconds => {
        if (held) return 0;
        const remaining = 60 - h.context.state.animation.elapsedMissionSeconds;
        if (!released && seconds >= remaining) { held = true; return remaining; }
        return seconds;
      }};
      h.frame(1000);
      close(h.context.state.animation.elapsedMissionSeconds, 60, 'Weather boundary time');
      close(h.context.state.animation.drones['1'].distanceTraveled, 10, 'Weather boundary position');
      h.frame(2000);
      close(h.context.state.animation.elapsedMissionSeconds, 60, 'Time while held');
      close(h.context.state.animation.drones['1'].distanceTraveled, 10, 'Position while held');
      held = false; released = true;
      h.frame(3000);
      close(h.context.state.animation.elapsedMissionSeconds, 60 + multiplier, 'No catch-up for time spent in weather hold');
      close(h.context.state.animation.drones['1'].distanceTraveled, (60 + multiplier) / 6, 'Flight after weather hold');
    });
  }
}

test('manual pause/resume excludes stopped wall time', () => {
  const h = harness({multiplier: 16});
  h.frame(1000);
  h.run('pauseAnimation()');
  h.setWall(11000);
  h.run('resumeAnimation()');
  h.frame(11000);
  close(h.context.state.animation.elapsedMissionSeconds, 16, 'Clock immediately after resuming');
  h.frame(12000);
  close(h.context.state.animation.elapsedMissionSeconds, 32, 'Clock after next active second');
  close(h.context.state.animation.drones['1'].distanceTraveled, 32 / 6, 'Resumed flight distance');
});

test('saved replay resumes from its rendered diamond without charging a delayed hold callback', () => {
  const h = harness({mode: 'start'});
  h.context.state.animation.checkpointPause = {active: true, resumeTime: 1000};
  h.frame(20000); // First visible callback releases the presentation hold.
  close(h.context.state.animation.elapsedMissionSeconds, 0, 'Hold release does not jump mission time');
  close(h.context.state.animation.drones['1'].distanceTraveled, 0, 'Aircraft remains at diamond on release');
  assert.equal(h.context.state.animation.checkpointPause, null);
  h.frame(21000);
  close(h.context.state.animation.elapsedMissionSeconds, 128, 'Next active second advances normally');
  close(h.context.state.animation.drones['1'].distanceTraveled, 128 / 6, 'Flight after presentation hold');
});

test('speed change between callbacks splits the wall interval at the actual input time', () => {
  const h = harness({multiplier: 1});
  h.setWall(250);
  h.run('state.animation.frameClock.changeSpeed(128, 250); state.animation.speedMultiplier = 128');
  h.frame(1000);
  close(h.context.state.animation.elapsedMissionSeconds, 96.25, '250 ms at 1x + 750 ms at 128x');
  h.frame(1500);
  close(h.context.state.animation.elapsedMissionSeconds, 160.25, 'Next 500 ms at 128x');
});

test('multiple speed inputs before the next callback preserve each interval', () => {
  const h = harness({multiplier: 1});
  h.run('state.animation.frameClock.changeSpeed(4, 100); state.animation.speedMultiplier = 4');
  h.run('state.animation.frameClock.changeSpeed(16, 300); state.animation.speedMultiplier = 16');
  h.frame(1000);
  close(h.context.state.animation.elapsedMissionSeconds, 0.1 + 0.2 * 4 + 0.7 * 16, 'Piecewise speed integral');
});

test('diagnostic direct multiplier assignment still controls the next callback', () => {
  const h = harness({multiplier: 1});
  h.context.state.animation.speedMultiplier = 128;
  h.frame(1000);
  close(h.context.state.animation.elapsedMissionSeconds, 128, 'Direct diagnostic speed assignment');
});

for (const [kind, route] of Object.entries({
  explicit: {refuel_stops: ['A2']},
  indices: {route: ['A1', 'A2', 'A3'], refuel_indices: [1]},
  legacy: {refuel_airport: 'A2'},
})) {
  test(`refuel identification supports ${kind} solver metadata`, () => {
    const h = harness({routes: {'1': route}, drones: {'1': {
      totalDistance: 3, _waypointDistances: [{wp: 'A2', dist: 1}],
    }}});
    h.frame(1000);
    close(h.context.state.animation.elapsedMissionSeconds, 19, 'Flight plus airport hold');
    assert.deepEqual(Array.from(h.context.state.legFuelHistory['1']), [1]);
    close(h.context.state.legFuel['1'], 2, 'Fuel after refuel');
  });
}

test('finite replay boundary includes earlier holds without overshooting time or fuel', () => {
  const h = harness({drones: {'1': {totalDistance: 10, _waypointDistances: [
    {wp: 'A2', dist: 1, isRefuel: true}, {wp: 'A3', dist: 2, isRefuel: true},
    {wp: 'A4', dist: 3, isRefuel: true},
  ]}}});
  h.run('advanceDroneMissionTime("1", state.animation.drones["1"], advanceMissionClockSeconds(128, {"1": 2}), 2)');
  const s = h.context.state;
  close(s.animation.elapsedMissionSeconds, 13, '12 seconds flight + 1 earlier hold');
  close(s.animation.drones['1'].distanceTraveled, 2, 'Replay boundary');
  close(s.animation.drones['1']._elapsedHoldMinutes, 1 / 60, 'Earlier hold consumed');
  close(s.animation.drones['1']._pauseRemaining, 1 / 60, 'Boundary departure hold preserved');
  assert.deepEqual(Array.from(s.legFuelHistory['1']), [1, 1]);
  close(s.legFuel['1'], 0, 'Fuel immediately after second refuel');
  h.run('advanceDroneMissionTime("1", state.animation.drones["1"], advanceMissionClockSeconds(128, {"1": 2}), 2)');
  close(s.animation.elapsedMissionSeconds, 13, 'No time advanced while waiting at cut');
  close(s.animation.drones['1'].distanceTraveled, 2, 'No distance advanced while waiting at cut');
  assert.deepEqual(Array.from(s.legFuelHistory['1']), [1, 1]);
});

function bindSpeedControls(h, {index = 2, boosted = false, factor = 100} = {}) {
  const handlers = {};
  const slider = {value: String(index), addEventListener: (event, handler) => {
    assert.equal(event, 'input'); handlers.slider = handler;
  }};
  const boost = {checked: boosted, addEventListener: (event, handler) => {
    assert.equal(event, 'change'); handlers.boost = handler;
  }};
  const boostFactor = {value: String(factor), addEventListener: (event, handler) => {
    assert.equal(event, 'change'); handlers.factor = handler;
  }};
  const label = {textContent: ''};
  const elements = {'mc-speed-slider': slider, 'mc-speed-boost': boost,
    'mc-speed-boost-factor': boostFactor, 'mc-speed-label': label};
  h.context.$ = id => elements[id] || null;
  const bindingStart = source.indexOf('  const mcSpeedSlider = $("mc-speed-slider");');
  const bindingEnd = source.indexOf('\n}\n\n/**', bindingStart);
  assert(bindingStart >= 0 && bindingEnd > bindingStart, 'Actual speed bindings must be present');
  h.run(source.slice(bindingStart, bindingEnd));
  return {slider, boost, boostFactor, label, handlers};
}

test('actual speed slider input handler records the change before mutating multiplier', () => {
  const h = harness({multiplier: 1});
  const controls = bindSpeedControls(h);
  h.setWall(250);
  controls.slider.value = '9';
  controls.handlers.slider();
  assert.equal(h.context.state.animation.speedMultiplier, 128);
  assert.equal(controls.label.textContent, '128x');
  h.frame(1000);
  close(h.context.state.animation.elapsedMissionSeconds, 96.25, 'Actual slider timing');
});

test('boost on and off between callbacks preserves every interval at the displayed speed', () => {
  const h = harness({multiplier: 128});
  const controls = bindSpeedControls(h, {index: 9, factor: 10});
  assert.equal(controls.boost.checked, false, 'Boost starts disabled');
  h.setWall(250);
  controls.boost.checked = true;
  controls.handlers.boost();
  assert.equal(h.context.state.animation.speedMultiplier, 1280);
  assert.equal(controls.label.textContent, '1280x');
  h.setWall(750);
  controls.boost.checked = false;
  controls.handlers.boost();
  assert.equal(h.context.state.animation.speedMultiplier, 128);
  assert.equal(controls.label.textContent, '128x');
  h.frame(1000);
  close(h.context.state.animation.elapsedMissionSeconds, 704, '250 ms at 128x + 500 ms at 1280x + 250 ms at 128x');
  close(h.context.state.animation.drones['1'].distanceTraveled, 704 / 6, 'Movement follows boosted clock');
  close(h.context.state.legFuel['1'], 704 / 6, 'Fuel follows boosted movement');
});

for (const factor of [10, 100]) {
  test(`moving the slider with ${factor}x boost preserves the boost and each interval`, () => {
    const h = harness({multiplier: 1});
    const controls = bindSpeedControls(h, {factor});
    h.setWall(100);
    controls.boost.checked = true;
    controls.handlers.boost();
    assert.equal(controls.label.textContent, `${factor}x`);
    h.setWall(300);
    controls.slider.value = '4';
    controls.handlers.slider();
    assert.equal(h.context.state.animation.speedMultiplier, 4 * factor);
    assert.equal(controls.label.textContent, `${4 * factor}x`);
    h.setWall(800);
    controls.boost.checked = false;
    controls.handlers.boost();
    assert.equal(h.context.state.animation.speedMultiplier, 4);
    assert.equal(controls.label.textContent, '4x');
    h.frame(1000);
    const expectedSeconds = 0.1 + 0.2 * factor + 0.5 * 4 * factor + 0.2 * 4;
    close(h.context.state.animation.elapsedMissionSeconds, expectedSeconds, 'Piecewise boost and slider timing');
    close(h.context.state.animation.drones['1'].distanceTraveled, expectedSeconds / 6, 'Distance follows each speed change');
  });

  test(`${factor}x boost on fractional speeds shows the actual multiplier and restores the label`, () => {
    const h = harness({multiplier: 0.25});
    const controls = bindSpeedControls(h, {index: 0, factor});
    controls.boost.checked = true;
    controls.handlers.boost();
    assert.equal(h.context.state.animation.speedMultiplier, 0.25 * factor);
    assert.equal(controls.label.textContent, `${0.25 * factor}x`);
    controls.slider.value = '1';
    controls.handlers.slider();
    assert.equal(h.context.state.animation.speedMultiplier, 0.5 * factor);
    assert.equal(controls.label.textContent, `${0.5 * factor}x`);
    controls.boost.checked = false;
    controls.handlers.boost();
    assert.equal(h.context.state.animation.speedMultiplier, 0.5);
    assert.equal(controls.label.textContent, '½x');
    controls.slider.value = '0';
    controls.handlers.slider();
    assert.equal(h.context.state.animation.speedMultiplier, 0.25);
    assert.equal(controls.label.textContent, '¼x');
  });

  test(`${128 * factor}x completes refuel stops and arrival without overshooting the mission clock`, () => {
    const h = harness({multiplier: 128, drones: {'1': {totalDistance: 3, _waypointDistances: [
      {wp: 'A2', dist: 1, isRefuel: true}, {wp: 'A3', dist: 2, isRefuel: true},
    ]}}});
    const controls = bindSpeedControls(h, {index: 9, factor});
    controls.boost.checked = true;
    controls.handlers.boost();
    h.frame(1000);
    close(h.context.state.animation.elapsedMissionSeconds, 20, 'Flight and refuel holds cap completion time');
    close(h.context.state.animation.drones['1'].distanceTraveled, 3, 'Final route distance');
    assert.deepEqual(Array.from(h.context.state.legFuelHistory['1']), [1, 1]);
    close(h.context.state.legFuel['1'], 1, 'Final leg fuel');
    assert.equal(h.context.state.animation.active, false);
  });
}

test('100x boost is the default factor and applies only when enabled', () => {
  const h = harness({multiplier: 128});
  const controls = bindSpeedControls(h, {index: 9});
  assert.equal(controls.boostFactor.value, '100');
  assert.equal(controls.boost.checked, false);
  assert.equal(h.context.state.animation.speedMultiplier, 128);
  controls.handlers.factor();
  assert.equal(h.context.state.animation.speedMultiplier, 128, 'Selecting a factor does not enable boost');
  controls.boost.checked = true;
  controls.handlers.boost();
  assert.equal(h.context.state.animation.speedMultiplier, 12800);
  assert.equal(controls.label.textContent, '12800x');
});

test('changing boost factor while flying preserves old and new speeds before the next frame', () => {
  const h = harness({multiplier: 128});
  const controls = bindSpeedControls(h, {index: 9});
  h.setWall(250);
  controls.boost.checked = true;
  controls.handlers.boost();
  assert.equal(controls.label.textContent, '12800x');
  h.setWall(500);
  controls.boostFactor.value = '10';
  controls.handlers.factor();
  assert.equal(h.context.state.animation.speedMultiplier, 1280);
  assert.equal(controls.label.textContent, '1280x');
  h.setWall(750);
  controls.boostFactor.value = '100';
  controls.handlers.factor();
  assert.equal(h.context.state.animation.speedMultiplier, 12800);
  assert.equal(controls.label.textContent, '12800x');
  h.frame(1000);
  const expectedSeconds = 0.25 * (128 + 12800 + 1280 + 12800);
  close(h.context.state.animation.elapsedMissionSeconds, expectedSeconds, 'Piecewise boost-factor timing');
  close(h.context.state.animation.drones['1'].distanceTraveled, expectedSeconds / 6, 'Distance follows factor changes');
  close(h.context.state.legFuel['1'], expectedSeconds / 6, 'Fuel follows factor changes');
  h.setWall(1250);
  controls.boost.checked = false;
  controls.handlers.boost();
  assert.equal(controls.label.textContent, '128x');
  h.frame(1500);
  close(h.context.state.animation.elapsedMissionSeconds, expectedSeconds + 0.25 * (12800 + 128),
    'Disabling high boost also splits the wall interval');
});

console.log(`Animation timing: ${passed} passed, ${failed} failed.`);
if (failed) process.exitCode = 1;
