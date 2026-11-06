// node tests/e2e/test_segment_loiter.js
// Optional integration: NODE_PATH=<playwright modules> node this-file --browser <url> <seg.json>
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const root = path.join(__dirname, "../..");
const source = fs.readFileSync(path.join(root, "webapp/isr.js"), "utf8");
function extract(text, name) {
  const start = text.indexOf(`function ${name}(`);
  assert(start >= 0, `missing ${name}`);
  let depth = 0;
  for (let i = text.indexOf("{", start); i < text.length; i++) {
    if (text[i] === "{") depth++;
    if (text[i] === "}" && --depth === 0) return text.slice(start, i + 1);
  }
  throw new Error(`unbalanced ${name}`);
}
const ctx = vm.createContext({});
const geometrySource = fs.readFileSync(path.join(root, "webapp/latlng_utils.js"), "utf8");
vm.runInContext(geometrySource +
  "\n" + ["routeLoiterSpecs", "addLoiterIntervals", "locatePlaybackPosition", "playbackDistanceAtPosition", "anchorPlaybackCheckpoint"].map(n => extract(source, n)).join("\n"), ctx);
const plain = x => JSON.parse(JSON.stringify(x));
const close = (a, b) => assert(Math.abs(a - b) < 1e-8, `${a} != ${b}`);
const env = { targets: [
  { id: "T1", type: "a", lat: 0, lng: 1 },
  { id: "T2", type: "b", lat: 0, lng: 2 },
] };
const trajectory = [[0, 0], [0, 1], [0, 2], [0, 3]];
const flight = [0, 120, 240, 360];
const cfg = { speed: 313 / 60, loiter_enabled: true, loiter_times: { a: 10, b: 0 } };
const specs = ctx.routeLoiterSpecs(["A1", "T1", "T2", "A2"], env, cfg);
assert.strictEqual(specs.length, 1);
const frame = ctx.addLoiterIntervals(trajectory, flight, specs);
assert.deepStrictEqual(plain(frame.trajectory), [[0, 0], [0, 1], [0, 1], [0, 2], [0, 3]]);
close((frame.cumDist[2] - frame.cumDist[1]) / cfg.speed, 10);
close(frame.total, 360 + 10 * cfg.speed);
close(frame.cumDist[3] - frame.cumDist[2], 120); // preserve authoritative flying distance
close(frame.shiftDistance(240), 240 + 10 * cfg.speed); // refuel follows the loiter
assert.deepStrictEqual(trajectory, [[0, 0], [0, 1], [0, 2], [0, 3]]);
assert.deepStrictEqual(flight, [0, 120, 240, 360]);

for (const speed of [1, 313 / 60]) {
  const other = { ...cfg, speed, loiter_times: { a: 0, b: 4 } };
  const holds = ctx.routeLoiterSpecs(["T1", "T2"], env, other);
  assert.strictEqual(holds[0].wp, "T2");
  close(holds[0].distance / speed, 4);
}
assert.strictEqual(ctx.routeLoiterSpecs(["T1", "T2"], env,
  { ...cfg, loiter_times: { a: 0, b: 0 } }).length, 0);
assert.strictEqual(ctx.routeLoiterSpecs(["T1"], env,
  { ...cfg, loiter_enabled: false }).length, 0);
assert.deepStrictEqual(plain(ctx.addLoiterIntervals(trajectory, flight, []).cumDist), flight);
console.log("PASS: drone/type durations, stationary holds, fuel units, flight distances, refuel timing, immutable inputs");

// The same spatial cut has different timeline values with and without loiter.
close(ctx.playbackDistanceAtPosition(frame.trajectory, frame.cumDist, [0, 1.5]),
  180 + 10 * cfg.speed);
close(ctx.playbackDistanceAtPosition(trajectory, flight, [0, 1.5]), 180);
// Distinguish later passes through an already-visited coordinate.
close(ctx.playbackDistanceAtPosition([[0, 0], [0, 1], [0, 0]],
  [0, 100, 200], [0, 0], 150), 200);
const savedFrame = { trajectory, cumulative_distances: flight, total_distance: 360,
  waypoint_indices: { 0: 0, 1: 1, 2: 2, 3: 3 }, altitudes: [0, 100, 200, 300] };
const routeInfo = { trajectory, anim_frame: savedFrame };
const drone = { cumulativeDistances: flight, distanceTraveled: 0 };
const cutPosition = [0.001, 0.5];
ctx.anchorPlaybackCheckpoint(routeInfo, drone, cutPosition);
assert.deepStrictEqual(plain(routeInfo.trajectory[1]), cutPosition);
close(drone.cumulativeDistances[1], 60);
close(routeInfo.anim_frame.total_distance, 360);
assert.deepStrictEqual(plain(routeInfo.anim_frame.waypoint_indices), { 0: 0, 1: 2, 2: 3, 3: 4 });
assert.deepStrictEqual(plain(routeInfo.anim_frame.altitudes), [0, 50, 100, 200, 300]);
assert.strictEqual(savedFrame.trajectory.length, 4); // stored solution remains immutable
ctx.anchorPlaybackCheckpoint(routeInfo, drone, cutPosition);
assert.strictEqual(routeInfo.trajectory.length, 5); // repeated setup adds no duplicate
console.log("PASS: cut timeline includes loiter; exact junction, altitude alignment and waypoint indices preserved");

async function browserCheck(url, filename) {
  const { chromium } = require("playwright");
  const data = JSON.parse(fs.readFileSync(filename, "utf8"));
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    page.on("dialog", d => d.dismiss());
    await page.goto(url);
    await page.waitForFunction(() => typeof loadSegmentedMissionFromJson === "function");
    const result = await page.evaluate(data => {
      // Run the shipped animation naturally with a deterministic clock. No
      // seeking or changing drone distances: that would hide cut-position drift.
      let callback;
      window.requestAnimationFrame = fn => { callback = fn; return 1; };
      window.cancelAnimationFrame = () => {};
      loadSegmentedMissionFromJson(data, "loiter-regression.json");
      document.getElementById("mc-replay-segments").checked = true;
      const holdsFor = did => {
        const tr = state.routes[did]?.trajectory || [];
        const cd = state.animation.drones[did]?.cumulativeDistances || [];
        const holds = [];
        for (let i = 1; i < tr.length; i++) {
          if (tr[i][0] === tr[i - 1][0] && tr[i][1] === tr[i - 1][1] && cd[i] > cd[i - 1]) {
            holds.push({ start: cd[i - 1], end: cd[i] });
          }
        }
        return holds;
      };
      const fuelFor = did => (state.legFuelHistory[did] || []).reduce((a, b) => a + b, 0)
        + (state.legFuel[did] || 0);
      const positionFor = did => {
        const drone = state.animation.drones[did];
        const tr = state.routes[did].trajectory, cd = drone.cumulativeDistances;
        let i = 1;
        while (i < cd.length - 1 && cd[i] < drone.distanceTraveled) i++;
        const span = cd[i] - cd[i - 1];
        const fraction = span > 0 ? (drone.distanceTraveled - cd[i - 1]) / span : 0;
        return tr[i - 1].map((x, k) => x + fraction * (tr[i][k] - x));
      };
      const beforeTransitions = [];
      const check = missionReplay.checkSegmentBoundary.bind(missionReplay);
      missionReplay.checkSegmentBoundary = function(distance, reached) {
        if (reached) {
          const segment = this.getNextSegment();
          beforeTransitions.push({ cut: segment.index, drones:
            Object.fromEntries(Object.entries(data.segments[segment.index].cutPosition).map(([did, pos]) =>
              [did, { gap: haversineKm(...positionFor(did), ...pos), fuel: fuelFor(did) }])) });
        }
        return check(distance, reached);
      };
      const runs = [];
      for (let replay = 0; replay < 2; replay++) {
        startAnimation();
        state.animation.speedMultiplier = 2;
        const cuts = [], stationary = {};
        let previousSegment = 0;
        let waitingFuelError = 0;
        for (let tick = 0; tick < 30000; tick++) {
          const previous = {};
          Object.entries(state.animation.drones).forEach(([did, d]) => {
            previous[did] = { distance: d.distanceTraveled, fuel: fuelFor(did),
              position: positionFor(did), holds: holdsFor(did), animating: d.animating };
          });
          callback(tick * 100);
          const current = missionReplay.getCurrentSegmentIndex();
          if (current > previousSegment) {
            const cut = data.segments[current];
            const before = beforeTransitions.at(-1);
            cuts.push({ cut: current, drones:
              Object.fromEntries(Object.entries(cut.cutPosition).map(([did, pos]) =>
                [did, { beforeGap: before.drones[did].gap,
                  afterGap: haversineKm(...positionFor(did), ...pos),
                  cutFuel: fuelFor(did) - before.drones[did].fuel,
                  holds: holdsFor(did) }])) });
            previousSegment = current;
            // Skip the wall-clock checkpoint presentation delay; its geometry
            // and timeline are already captured above.
            state.animation.checkpointPause = null;
          } else {
            Object.entries(previous).forEach(([did, prev]) => {
              const d = state.animation.drones[did];
              const delta = d.distanceTraveled - prev.distance;
              if (prev.animating && delta === 0) {
                waitingFuelError = Math.max(waitingFuelError, Math.abs(fuelFor(did) - prev.fuel));
              }
              if (delta > 0 && prev.holds.some(h => prev.distance > h.start && d.distanceTraveled < h.end)) {
                const key = `${current}:${did}`;
                const sample = stationary[key] ||= { samples: 0, movement: 0, fuelError: 0 };
                sample.samples++;
                sample.movement = Math.max(sample.movement,
                  haversineKm(...prev.position, ...positionFor(did)));
                sample.fuelError = Math.max(sample.fuelError,
                  Math.abs(fuelFor(did) - prev.fuel - delta));
              }
            });
          }
          if (state.animation.missionComplete) break;
        }
        runs.push({ cuts, stationary, waitingFuelError, complete: state.animation.missionComplete });
      }
      return runs;
    }, data);
    for (const run of result) {
      assert.strictEqual(run.complete, true, "replay must finish");
      assert.strictEqual(run.cuts.length, data.segments.length - 1);
      close(run.waitingFuelError, 0);
      for (const cut of run.cuts) {
        for (const [did, drone] of Object.entries(cut.drones)) {
          assert(drone.beforeGap < 0.001, `D${did} short of C${cut.cut} before rebuild: ${drone.beforeGap} km`);
          assert(drone.afterGap < 0.001, `D${did} moved at C${cut.cut}: ${drone.afterGap} km`);
          close(drone.cutFuel, 0);
          const expected = [];
          for (let i = 0; i <= cut.cut; i++) {
            const seg = data.segments[i], cfg = seg.drone_configs[did];
            const visited = i < cut.cut ? data.segments[i + 1].frozenTargets : null;
            for (const wp of seg.routes[did].route) {
              if (visited && !visited.includes(wp)) continue;
              const target = seg.env.targets.find(t => t.id === wp);
              const minutes = target && cfg.loiter_enabled && cfg.loiter_times[target.type.toLowerCase()];
              if (minutes > 0) expected.push(minutes * cfg.speed);
            }
          }
          assert.strictEqual(drone.holds.length, expected.length, `D${did}: lost or duplicated loiters at C${cut.cut}`);
          expected.forEach((fuel, i) => close(drone.holds[i].end - drone.holds[i].start, fuel));
        }
      }
      const lastSegment = data.segments.length - 1;
      assert(run.stationary[`${lastSegment}:1`]?.samples > 0, "must observe a real post-cut D1 loiter");
      assert(!Object.keys(run.stationary).some(key => key.endsWith(":2")), "D2 must not loiter");
      for (const sample of Object.values(run.stationary)) {
        close(sample.movement, 0);
        close(sample.fuelError, 0);
      }
    }
    assert.deepStrictEqual(errors, []);
    console.log(`PASS: ${path.basename(filename)}: both full replays reach every diamond; loiter durations, stationary fuel burn, fuel-neutral cuts and waiting verified`);
  } finally {
    await browser.close();
  }
}
if (process.argv[2] === "--browser") {
  browserCheck(process.argv[3], process.argv[4]).catch(e => { console.error(e); process.exitCode = 1; });
}
