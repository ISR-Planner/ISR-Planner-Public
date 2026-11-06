// refreshSamWrapping (webapp/isr.js): the dashed SAM boundary is the slice at the
// drones' cruise altitude (sqrt(r^2 - (h - e)^2), e = SAM site elevation), not the
// ground circle; with drones at different altitudes, one outline per altitude.
// Runs the REAL code.
//   node tests/e2e/test_sam_boundary_altitude.js
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const src = fs.readFileSync(path.join(__dirname, "../../webapp/isr.js"), "utf8");
function extractFunction(name) {
  const start = src.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(name);
  let i = src.indexOf("{", start), depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(start, i + 1);
  }
  throw new Error(name);
}
const FNS = ["_cross", "_convexHull", "_sampleCircle", "_samsOverlap", "_circleIntersectsPolygon",
             "_clusterOverlappingSams", "computeWrappedPolygonsClientSide", "samSlicesAtAltitude",
             "droneCruiseAltitudeKm", "droneAltitudeGroups",
             "refreshSamWrapping"];
// Every drone flies altInput's altitude unless a test gives it its own.
const altInput = { value: "0" };
const droneConfigs = { "1": { enabled: true } };
const ctx = {
  $: () => null,
  state: {},
  getEditableDroneConfigs: () => Object.fromEntries(Object.entries(droneConfigs).map(
    ([d, c]) => [d, { cruise_altitude_km: parseFloat(altInput.value), ...c }])),
  Math,
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, "../../webapp/latlng_utils.js"), "utf8"), ctx);
vm.runInContext(FNS.map(extractFunction).join("\n\n"), ctx);

const env = { sams: [{ center: { lat: 32.2, lng: -90.1 }, range_km: 5 }] };
function maxRadiusKm(polygon) {
  const c = env.sams[0].center;
  return Math.max(...polygon.map(([lat, lng]) => {
    const dlat = (lat - c.lat) * 111.0;
    const dlng = (lng - c.lng) * 111.0 * Math.cos((c.lat * Math.PI) / 180);
    return Math.hypot(dlat, dlng);
  }));
}
let passed = 0;
function test(name, fn) {
  fn();
  passed++;
  console.log(`  ✓ ${name}`);
}

test("dashed SAM boundary = slice at cruise altitude (5 → 4 km at 3 km; none above)", () => {
  altInput.value = "0";
  ctx.refreshSamWrapping(env);
  assert(Math.abs(maxRadiusKm(ctx.state.wrappedPolygons[0]) - 5) < 0.2);
  altInput.value = "3";
  ctx.refreshSamWrapping(env);
  const r = maxRadiusKm(ctx.state.wrappedPolygons[0]);
  assert(Math.abs(r - 4) < 0.2, `altitude 3 → 4 km slice, got ${r}`);
  altInput.value = "6";
  ctx.refreshSamWrapping(env);
  assert.strictEqual(ctx.state.wrappedPolygons.length, 0, "above the SAM: no boundary");
  assert.strictEqual(ctx.state.samSliceGroups.length, 0, "one altitude: no per-drone outlines");
});

test("slice is measured from the SAM site's elevation", () => {
  const high = { sams: [{ ...env.sams[0], alt_km: 2 }] };
  altInput.value = "5"; // 3 km above a site 2 km up → 4 km slice
  ctx.refreshSamWrapping(high);
  const r = maxRadiusKm(ctx.state.wrappedPolygons[0]);
  assert(Math.abs(r - 4) < 0.2, `got ${r}`);
});

test("drones at different altitudes: one outline per altitude, shared when equal", () => {
  altInput.value = "0";
  droneConfigs["2"] = { enabled: true, cruise_altitude_km: 3 };
  droneConfigs["3"] = { enabled: true, cruise_altitude_km: 3 };
  ctx.refreshSamWrapping(env);
  const groups = ctx.state.samSliceGroups;
  assert.strictEqual(groups.length, 2);
  assert.strictEqual(JSON.stringify(groups.map((g) => [g.altKm, g.dids])), '[[0,["1"]],[3,["2","3"]]]');
  assert(Math.abs(maxRadiusKm(groups[0].polygons[0]) - 5) < 0.2);
  assert(Math.abs(maxRadiusKm(groups[1].polygons[0]) - 4) < 0.2);
  // A disabled drone's altitude is not drawn.
  droneConfigs["1"].enabled = false;
  ctx.refreshSamWrapping(env);
  assert.strictEqual(ctx.state.samSliceGroups.length, 0);
});

console.log(`\n${passed} passed`);
