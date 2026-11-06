// Asset tab rules (webapp/isr.js): drones carry their own altitude (older files'
// mission-wide value is copied in on import), loitering is on when a loiter time
// is above 0, and a tanker's clearance above the ground is flagged against the
// terrain margin. Runs the REAL code.
//   node tests/e2e/test_asset_tab.js
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const src = fs.readFileSync(path.join(__dirname, "../../webapp/isr.js"), "utf8");
function extractFunction(name) {
  const start = src.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(name);
  let i = src.indexOf(") {", start) + 2, depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(start, i + 1);
  }
  throw new Error(name);
}
function extractConst(name) {
  const m = src.match(new RegExp(`const ${name} = [^;]+;`));
  if (!m) throw new Error(name);
  return m[0].replace(/^const /, "var ");
}

const ctx = {};
vm.createContext(ctx);
vm.runInContext(
  [extractConst("DEFAULT_CLIMB_ANGLE_DEG"), extractConst("TERRAIN_MARGIN_KM"),
   extractConst("DEFAULT_TARGET_CATEGORIES"), extractFunction("targetCategories"), extractFunction("categoryIds"),
   extractFunction("perCategory"), extractFunction("withAllCategories"), extractFunction("samePerCategory"),
   extractFunction("loiterIsOn"), extractFunction("droneConfigFromSaved"),
   extractFunction("tankerClearanceHtml"), extractConst("PRESET_FIELDS"), extractConst("PRESET_TYPE_FIELDS"),
   extractFunction("droneEditedFromPreset")].join("\n\n"),
  ctx,
);
const plain = (o) => JSON.parse(JSON.stringify(o));

let passed = 0;
function test(name, fn) { fn(); passed++; console.log(`  ✓ ${name}`); }

test("an older file's mission altitude + climb become each drone's own", () => {
  const cfg = ctx.droneConfigFromSaved({}, 1, ["A1"], { altKm: 5, climbDeg: 20 });
  assert.strictEqual(cfg.cruise_altitude_km, 5);
  assert.strictEqual(cfg.climb_angle_deg, 20);
});

test("a drone's own altitude wins; 0 is kept (ground level)", () => {
  assert.strictEqual(ctx.droneConfigFromSaved({ cruise_altitude_km: 7 }, 1, ["A1"], { altKm: 5 }).cruise_altitude_km, 7);
  assert.strictEqual(ctx.droneConfigFromSaved({ cruise_altitude_km: 0 }, 1, ["A1"], { altKm: 5 }).cruise_altitude_km, 0);
});

test("no altitude anywhere: 0 km and the default climb angle", () => {
  const cfg = ctx.droneConfigFromSaved({}, 2, ["A1", "A2"]);
  assert.strictEqual(cfg.cruise_altitude_km, 0);
  assert.strictEqual(cfg.climb_angle_deg, 12);
  assert.strictEqual(cfg.start_airport, "A2");
});

test("loitering is on when a loiter time is above 0", () => {
  assert.strictEqual(ctx.loiterIsOn({ a: 0, b: 0 }), false);
  assert.strictEqual(ctx.loiterIsOn({ a: 0, b: 3 }), true);
  assert.strictEqual(ctx.loiterIsOn(undefined), false);
});

test("a file saved with loitering off imports its loiter times as 0 (plans unchanged)", () => {
  const times = { a: 5, b: 0, c: 0, d: 0, e: 0 };
  const off = ctx.droneConfigFromSaved({ loiter_enabled: false, loiter_times: times }, 1, ["A1"]);
  assert.deepStrictEqual(plain(off.loiter_times), { a: 0, b: 0, c: 0, d: 0, e: 0, f: 0, g: 0 });
  assert.strictEqual(off.loiter_enabled, false);
  const on = ctx.droneConfigFromSaved({ loiter_enabled: true, loiter_times: times }, 1, ["A1"]);
  assert.strictEqual(on.loiter_times.a, 5);
  assert.strictEqual(on.loiter_enabled, true);
});

test("tanker clearance: fine, under the 0.6 km margin (amber), below ground (red)", () => {
  assert.strictEqual(ctx.tankerClearanceHtml({ alt_km: 4, ground_km: 1.6 }), "2.4 km");
  assert.match(ctx.tankerClearanceHtml({ alt_km: 3.2, ground_km: 2.8 }), /clearance-warn.*0\.4 km · under 0\.6 km margin/);
  assert.match(ctx.tankerClearanceHtml({ alt_km: 1.8, ground_km: 2.1 }), /clearance-bad.*-0\.3 km · below ground/);
  assert.match(ctx.tankerClearanceHtml({ alt_km: 4 }), /–/);  // ground not sampled yet
});

test("import keeps a drone's aircraft type, sensors, preset and ceiling", () => {
  const saved = { platform_id: "MQ-1B", sensor_ids: ["EOIR"], ceiling_km: 7.6,
    preset: { cruise_altitude_km: 4.6 }, cruise_altitude_km: 8 };
  const cfg = ctx.droneConfigFromSaved(saved, 1, ["A1"]);
  assert.strictEqual(cfg.platform_id, "MQ-1B");
  assert.strictEqual(JSON.stringify(cfg.sensor_ids), '["EOIR"]');
  assert.strictEqual(cfg.ceiling_km, 7.6);
  assert.strictEqual(cfg.preset.cruise_altitude_km, 4.6);
  assert.strictEqual(cfg.cruise_altitude_km, 8);  // the edit survives too
  assert.strictEqual(ctx.droneConfigFromSaved({}, 1, ["A1"]).platform_id, undefined);  // Custom stays Custom
});

test("a drone counts as edited only where it differs from its catalog preset", () => {
  const preset = { cruise_altitude_km: 7.6, climb_angle_deg: 8, speed: 313 / 60, fuel_budget: 8451, runway: "M",
    target_access: { a: true, b: true, c: true, d: true, e: true },
    detection_radius: { a: 20, b: 6, c: 25, d: 20, e: 6 }, loiter_times: { a: 2, b: 5, c: 2, d: 2, e: 5 } };
  const cfg = JSON.parse(JSON.stringify({ ...preset, preset }));
  assert.strictEqual(ctx.droneEditedFromPreset(cfg), false);
  cfg.fuel_budget = 500;
  assert.strictEqual(ctx.droneEditedFromPreset(cfg), true);
  cfg.fuel_budget = 8451; cfg.detection_radius.c = 10;
  assert.strictEqual(ctx.droneEditedFromPreset(cfg), true);
  assert.strictEqual(ctx.droneEditedFromPreset({ fuel_budget: 1 }), false);  // Custom: no preset
});

test("an older A-E file gets the new categories: a Custom drone visits them, an aircraft doesn't", () => {
  const oldAccess = { a: true, b: false, c: true, d: true, e: true };
  const custom = ctx.droneConfigFromSaved({ target_access: oldAccess }, 1, ["A1"]);
  assert.deepStrictEqual(plain(custom.target_access), { ...oldAccess, f: true, g: true });
  assert.strictEqual(custom.detection_radius.g, 0);
  const aircraft = ctx.droneConfigFromSaved({ target_access: oldAccess, platform_id: "MQ-9A" }, 1, ["A1"]);
  assert.strictEqual(aircraft.target_access.f, false);
  assert.strictEqual(aircraft.target_access.b, false);  // the file's own values are kept
});

test("a category added after the preset doesn't count as an edit", () => {
  const preset = { target_access: { a: true }, detection_radius: { a: 3 }, loiter_times: { a: 10 } };
  const cfg = { ...JSON.parse(JSON.stringify(preset)), preset };
  cfg.target_access.g = false; cfg.detection_radius.g = 0; cfg.loiter_times.g = 0;
  assert.strictEqual(ctx.droneEditedFromPreset(cfg), false);
  cfg.target_access.g = true;
  assert.strictEqual(ctx.droneEditedFromPreset(cfg), true);
});

console.log(`\n${passed} passed`);
