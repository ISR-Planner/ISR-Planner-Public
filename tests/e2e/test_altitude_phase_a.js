// Altitude model Phase A (docs/architecture/ALTITUDE_MODEL.md): every item gets
// an altitude, and 3-number positions work where 2-number ones did.
//   node tests/e2e/test_altitude_phase_a.js
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const src = fs.readFileSync(path.join(__dirname, "../../webapp/isr.js"), "utf8");
function extractFunction(name) {
  let start = src.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(name);
  if (src.slice(start - 6, start) === "async ") start -= 6;  // keep async functions async
  let i = src.indexOf(") {", start) + 2, depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(start, i + 1);
  }
  throw new Error(name);
}
const eq = (a, b) => assert.deepStrictEqual(JSON.parse(JSON.stringify(a)), b);

let passed = 0;
async function test(name, fn) { await fn(); passed++; console.log(`  ✓ ${name}`); }

(async () => {
  await test("ground items get terrain elevation, tankers the Edit-panel altitude + their ground, existing values kept", async () => {
    const sampled = [];
    const ctx = {
      $: (id) => (id === "tanker-alt" ? { value: "3" } : null),
      getEditableEnv: () => null,
      populateRefuelStopsBox: () => {},
      cesiumView: {
        groundElevationsKm: async (pts) => { sampled.push(...pts.map((p) => [p.lat, p.lng])); return pts.map(() => 1.6); },
      },
    };
    vm.createContext(ctx);
    vm.runInContext(extractFunction("fillMissingAltitudes") + "\n" + extractFunction("newTankerAltitudeKm"), ctx);
    const env = {
      airports: [{ id: "A1", lat: 39.86, lng: -104.67 }, { id: "TK1", lat: 39.9, lng: -104.8, type: "tanker" },
                 { id: "A2", lat: 39.7, lng: -104.9, alt_km: 1.5 }],
      targets: [{ id: "T1", lat: 39.8, lng: -104.7 }],
      sams: [{ center: { lat: 39.75, lng: -104.75 }, range_km: 5 }],
    };
    await ctx.fillMissingAltitudes(env);
    assert.strictEqual(env.airports[0].alt_km, 1.6);   // Denver airport on its ground
    assert.strictEqual(env.airports[1].alt_km, 3);     // tanker: the Edit panel's tanker altitude
    assert.strictEqual(env.airports[1].ground_km, 1.6); // ...and the ground under it
    assert.strictEqual(env.airports[2].alt_km, 1.5);   // already set: kept
    assert.strictEqual(env.targets[0].alt_km, 1.6);
    assert.strictEqual(env.sams[0].alt_km, 1.6);       // SAM site elevation
    assert.strictEqual(sampled.length, 4);             // A1, tanker ground, T1, SAM — not A2
  });

  await test("without terrain the ground is 0 (matches the flat 3D view)", async () => {
    const ctx = { $: () => null, getEditableEnv: () => null, populateTankerTable: () => {} };
    vm.createContext(ctx);
    vm.runInContext(extractFunction("fillMissingAltitudes") + "\n" + extractFunction("newTankerAltitudeKm"), ctx);
    const env = { airports: [{ id: "A1", lat: 1, lng: 1 }], targets: [], sams: [] };
    await ctx.fillMissingAltitudes(env);
    assert.strictEqual(env.airports[0].alt_km, 0);
  });

  await test("sequence code handles [lat, lng, alt] path and cut points", async () => {
    const FNS = ["closestTrajectoryPiece", "waypointLatLng", "routeWaypointTrajectoryIndices",
                 "splitRouteAtCut", "buildMissionSequence"];
    const MissionMode = { DRAFT_READY: "DRAFT_READY", CHECKPOINT: "CHECKPOINT",
                          PAUSED_MID_ANIMATION: "PAUSED", EDITING_ENV: "EDITING" };
    const seg0 = { solution: { routes: { "1": {
      route: ["A1", "T2", "T5", "T7", "A3"],
      trajectory: [[0, 0, 1.6], [1, 0, 3], [2, 0, 3], [3, 0, 3], [4, 0, 1.7]],
      waypoint_indices: { 0: 0, 1: 1, 2: 2, 3: 3, 4: 4 } } } } };
    const seg1 = { cutPositions: { "1": [2.5, 0, 3] }, solution: { routes: { "1": {
      route: ["C1-1", "T9", "A3"], trajectory: [[2.5, 0, 3], [2.5, 1, 3], [4, 0, 1.7]],
      waypoint_indices: { 0: 0, 1: 1, 2: 2 } } } } };
    const ctx = {
      MissionMode, getAllKnownTargets: () => [],
      state: { env: { airports: [] }, checkpoint: { active: false }, animation: { drones: {} } },
      missionState: { mode: "READY", draftSolution: null },
      missionReplay: { getSegmentCount: () => 2, getSegment: (k) => [seg0, seg1][k] },
    };
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(path.join(__dirname, "../../webapp/latlng_utils.js"), "utf8"), ctx);
    vm.runInContext(FNS.map(extractFunction).join("\n\n"), ctx);
    eq(ctx.buildMissionSequence("1").labels, ["A1", "T2", "T5", "C1", "T9", "A3"]);
  });

  console.log(`\n${passed} passed`);
})().catch((err) => { console.error(err); process.exit(1); });
