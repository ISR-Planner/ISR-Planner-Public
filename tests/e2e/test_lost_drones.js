// placeLostDrones (webapp/isr.js): killed drones sit in their red diamond —
// while a draft is shown, after Accept, and during replay. Runs the REAL code.
//   node tests/e2e/test_lost_drones.js
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const src = fs.readFileSync(path.join(__dirname, "../../webapp/isr.js"), "utf8");
function extractFunction(name) {
  const start = src.indexOf(`function ${name}(`);
  let i = src.indexOf(") {", start) + 2, depth = 0;  // body brace, not a destructured param
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(start, i + 1);
  }
  throw new Error(name);
}

function ctxWith({ segments = [], current = 0, routes = {}, drones = {}, pending = null } = {}) {
  const ctx = {
    appendDebugLine: () => {},
    missionReplay: {
      getSegmentCount: () => segments.length,
      getSegment: (k) => segments[k],
      getCurrentSegmentIndex: () => current,
    },
    state: {
      routes, trajectoryVisible: {}, animation: { drones },
      pendingLostDrones: pending ? pending.lost : [],
      pendingCutPositions: pending ? pending.positions : null,
      pendingCutDistance: pending ? pending.cutDistance : null,
    },
  };
  vm.createContext(ctx);
  vm.runInContext(extractFunction("placeLostDrones"), ctx);
  return ctx;
}

let passed = 0;
const test = (name, fn) => { fn(); passed++; console.log(`  ✓ ${name}`); };

test("kill pending in the draft: drawn in its diamond (fresh draft state replaced)", () => {
  const c = ctxWith({
    segments: [{}], routes: { "1": { trajectory: [] }, "2": { trajectory: [[0, 0], [1, 1]] } },
    drones: { "1": { distanceTraveled: 0 }, "2": { distanceTraveled: 0 } },
    pending: { lost: ["1"], positions: { "1": [32.2, -90.1] }, cutDistance: 12.5 },
  });
  c.placeLostDrones({ upToSegment: 0, includePending: true });
  const d1 = c.state.animation.drones["1"];
  assert.deepStrictEqual(Array.from(d1._lostAtPosition), [32.2, -90.1]);
  assert.strictEqual(d1._lostAtCutDistance, 12.5);
  assert.strictEqual(c.state.animation.drones["2"]._lostAtPosition, undefined);
});

test("committed kill: placed when animating that segment", () => {
  const seg1 = { lostDrones: ["1"], cutPositions: { "1": [32.3, -90.2] }, cutDistance: 8 };
  const c = ctxWith({ segments: [{}, seg1], current: 1, routes: { "1": { trajectory: [] } } });
  c.placeLostDrones();
  assert.strictEqual(c.state.animation.drones["1"]._lostAtCutDistance, 8);
});

test("replay from segment 0: a later kill is not shown yet", () => {
  const seg1 = { lostDrones: ["1"], cutPositions: { "1": [32.3, -90.2] }, cutDistance: 8 };
  const c = ctxWith({ segments: [{}, seg1], current: 0, routes: { "1": { trajectory: [[0, 0], [1, 1]] } } });
  c.placeLostDrones();
  assert.strictEqual(c.state.animation.drones["1"], undefined);
});

test("a drone that still has a path to fly is never frozen", () => {
  const seg1 = { lostDrones: ["1"], cutPositions: { "1": [32.3, -90.2] }, cutDistance: 8 };
  const c = ctxWith({ segments: [{}, seg1], current: 1, routes: { "1": { trajectory: [[0, 0], [1, 1]] } } });
  c.placeLostDrones();
  assert.strictEqual(c.state.animation.drones["1"], undefined);
});

console.log(`\n${passed} passed`);
