// Whole-mission drone sequence (webapp/isr.js buildMissionSequence).
// Extracts the REAL functions from isr.js and runs them against stub mission
// state, so this tests the shipped code, not a copy.
//   node tests/e2e/test_mission_sequence.js
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const src = fs.readFileSync(path.join(__dirname, "../../webapp/isr.js"), "utf8");

function extractFunction(name) {
  const start = src.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`function ${name} not found`);
  let i = src.indexOf("{", start);
  let depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(start, i + 1);
  }
  throw new Error(`unbalanced ${name}`);
}

const FNS = [
  "closestTrajectoryPiece",
  "waypointLatLng",
  "routeWaypointTrajectoryIndices",
  "splitRouteAtCut",
  "buildMissionSequence",
  "missionSequenceText",
];

function makeContext(overrides) {
  const MissionMode = {
    IDLE: "IDLE", DRAFT_READY: "DRAFT_READY", CHECKPOINT: "CHECKPOINT",
    PAUSED_MID_ANIMATION: "PAUSED_MID_ANIMATION", EDITING_ENV: "EDITING_ENV",
    READY_TO_ANIMATE: "READY_TO_ANIMATE", ANIMATING: "ANIMATING",
  };
  const ctx = {
    MissionMode,
    state: { env: { airports: [], targets: [] }, checkpoint: { active: false }, animation: { drones: {} } },
    missionState: { mode: MissionMode.READY_TO_ANIMATE, draftSolution: null },
    missionReplay: null,
    getAllKnownTargets: () => [],
    appendDebugLine: () => {},
    ...overrides,
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../../webapp/latlng_utils.js"), "utf8"), ctx);
  vm.runInContext(FNS.map(extractFunction).join("\n\n"), ctx);
  return ctx;
}

function replay(segments) {
  return { getSegmentCount: () => segments.length, getSegment: (k) => segments[k] };
}

// A straight-line world: x = lat, y = lng, each waypoint on the trajectory.
// Segment 0: A1 → T2 → T5 → T7 → A3; cut C1 between T5 and T7.
const seg0 = {
  solution: { routes: { "1": {
    route: ["A1", "T2", "T5", "T7", "A3"],
    trajectory: [[0, 0], [1, 0], [2, 0], [3, 0], [4, 0]],
    waypoint_indices: { 0: 0, 1: 1, 2: 2, 3: 3, 4: 4 },
  } } },
};
// Segment 1 starts at C1 (2.5,0): C1-1 → T9 → T1 → A3; cut C2 between T1 and A3.
const seg1 = {
  cutPositions: { "1": [2.5, 0] },
  solution: { routes: { "1": {
    route: ["C1-1", "T9", "T1", "A3"],
    trajectory: [[2.5, 0], [2.5, 1], [2.5, 2], [2.5, 3]],
    waypoint_indices: { 0: 0, 1: 1, 2: 2, 3: 3 },
  } } },
};
// Segment 2 starts at C2 (2.5,2.5): C2-1 → A3.
const seg2 = {
  cutPositions: { "1": [2.5, 2.5] },
  solution: { routes: { "1": {
    route: ["C2-1", "A3"],
    trajectory: [[2.5, 2.5], [4, 0]],
    waypoint_indices: { 0: 0, 1: 1 },
  } } },
};

// Arrays built inside the vm context have another realm's prototype; compare as plain data.
const eq = (actual, expected) =>
  assert.deepStrictEqual(JSON.parse(JSON.stringify(actual)), expected);

let passed = 0;
function test(name, fn) {
  fn();
  passed++;
  console.log(`  ✓ ${name}`);
}

test("single segment: whole route editable, no history", () => {
  const c = makeContext({ missionReplay: replay([seg0]) });
  const s = c.buildMissionSequence("1");
  eq(s.labels, ["A1", "T2", "T5", "T7", "A3"]);
  assert.strictEqual(s.historyLength, 0);
});

test("two cuts: flown targets + diamonds + remaining (the operator's example)", () => {
  const c = makeContext({ missionReplay: replay([seg0, seg1, seg2]) });
  const s = c.buildMissionSequence("1");
  eq(s.labels, ["A1", "T2", "T5", "C1", "T9", "T1", "C2", "A3"]);
  eq(s.nodes, ["A1", "T2", "T5", "C1-1", "T9", "T1", "C2-1", "A3"]);
  assert.strictEqual(s.historyLength, 7);  // up to and including C2
});

test("unflown targets of a cut segment are NOT in the history", () => {
  const c = makeContext({ missionReplay: replay([seg0, seg1, seg2]) });
  assert(!c.buildMissionSequence("1").labels.slice(0, 7).includes("T7"));
});

test("C-key cut waiting for a plan: diamond + old route's unreached part", () => {
  const c = makeContext({ missionReplay: replay([seg0]) });
  c.state.checkpoint = { active: true };
  c.state.pendingCutPositions = { "1": [2.5, 0] };
  c.state.pendingCheckpoints = { "1": { id: "C1-1" } };
  c.missionState.mode = c.MissionMode.CHECKPOINT;
  const s = c.buildMissionSequence("1");
  eq(s.labels, ["A1", "T2", "T5", "C1", "T7", "A3"]);
  eq(s.nodes.slice(0, 4), ["A1", "T2", "T5", "C1-1"]);
  assert.strictEqual(s.historyLength, 4);
});

test("paused mid-flight: previewed as the cut it would become", () => {
  const c = makeContext({ missionReplay: replay([seg0]) });
  c.missionState.mode = c.MissionMode.PAUSED_MID_ANIMATION;
  c.state.animation.drones["1"] = { _currentLat: 1.5, _currentLng: 0 };
  const s = c.buildMissionSequence("1");
  eq(s.labels, ["A1", "T2", "C1", "T5", "T7", "A3"]);
  assert.strictEqual(s.historyLength, 3);
});

test("checkpoint draft on screen: its route is the remaining part", () => {
  const c = makeContext({ missionReplay: replay([seg0]) });
  c.missionState.mode = c.MissionMode.DRAFT_READY;
  c.missionState.draftSolution = {
    isCheckpointReplan: true,
    pendingCutPositions: { "1": [2.5, 0] },
    routes: { "1": { route: ["C1-1", "A1"], trajectory: [[2.5, 0], [0, 0]] } },
  };
  const s = c.buildMissionSequence("1");
  eq(s.labels, ["A1", "T2", "T5", "C1", "A1"]);  // abort to A1
  assert.strictEqual(s.historyLength, 4);
});

test("drone killed at a diamond: sequence ends there", () => {
  const killed = { ...seg1, lostDrones: ["1"], solution: { routes: {} } };
  const c = makeContext({ missionReplay: replay([seg0, killed]) });
  const s = c.buildMissionSequence("1");
  eq(s.labels, ["A1", "T2", "T5", "C1"]);
});

test("cut exactly at a target vertex counts that target as flown", () => {
  const atT5 = { ...seg1, cutPositions: { "1": [2, 0] } };
  const c = makeContext({ missionReplay: replay([seg0, atT5]) });
  const s = c.buildMissionSequence("1");
  eq(s.labels.slice(0, 4), ["A1", "T2", "T5", "C1"]);
});

test("drones finished the route, then replanned from where they landed: no diamond", () => {
  const fromLanding = {
    cutPositions: {},
    solution: { routes: { "1": {
      route: ["D1_START", "T9", "A1"],
      trajectory: [[4, 0], [4, 1], [0, 0]],
      waypoint_indices: { 0: 0, 1: 1, 2: 2 },
    } } },
  };
  const c = makeContext({ missionReplay: replay([seg0, fromLanding]) });
  const s = c.buildMissionSequence("1");
  eq(s.labels, ["A1", "T2", "T5", "T7", "A3", "T9", "A1"]);
  assert.strictEqual(s.historyLength, 5);  // the whole first route was flown
});

test("replanned in place before moving: nothing of the first route is history", () => {
  const inPlace = {
    cutPositions: {},
    solution: { routes: { "1": {
      route: ["D1_START", "T9", "A3"],
      trajectory: [[0, 0], [4, 1], [4, 0]],
      waypoint_indices: { 0: 0, 1: 1, 2: 2 },
    } } },
  };
  const c = makeContext({ missionReplay: replay([seg0, inPlace]) });
  const s = c.buildMissionSequence("1");
  eq(s.labels, ["A1", "T9", "A3"]);
  assert.strictEqual(s.historyLength, 1);
});

test("paused after the drone landed: whole route is history, no diamond", () => {
  const c = makeContext({ missionReplay: replay([seg0]) });
  c.missionState.mode = c.MissionMode.PAUSED_MID_ANIMATION;
  c.state.animation.drones["1"] = { _currentLat: 4, _currentLng: 0, distanceTraveled: 4, totalDistance: 4 };
  const s = c.buildMissionSequence("1");
  eq(s.labels, ["A1", "T2", "T5", "T7", "A3"]);
  assert.strictEqual(s.historyLength, 5);
});

test("display never throws: bad state falls back to the current route", () => {
  const c = makeContext({ missionReplay: { getSegmentCount: () => { throw new Error("boom"); } } });
  c.state.sequences = { "1": "A1,T1,A2" };
  assert.strictEqual(c.missionSequenceText("1"), "A1,T1,A2");
});

console.log(`\n${passed} passed`);
