// resumeLandedDrone (webapp/isr.js): a drone that landed before the cut ends its
// mission unless it may refuel at that airport. Runs the REAL function.
//   node tests/e2e/test_landed_drone.js
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const src = fs.readFileSync(path.join(__dirname, "../../webapp/isr.js"), "utf8");
function extractFunction(name) {
  const start = src.indexOf(`function ${name}(`);
  let i = src.indexOf("{", start);
  let depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(start, i + 1);
  }
  throw new Error(name);
}

function run(cfg, { route = ["C1-1", "T2", "A2"], legFuelHistory = [] } = {}) {
  const ctx = {
    state: { routes: { "1": { route } }, legFuelHistory: { "1": legFuelHistory } },
    appendDebugLine: () => {},
  };
  vm.createContext(ctx);
  vm.runInContext(["syntheticStartNode", "resumeLandedDrone"].map(extractFunction).join("\n"), ctx);
  const env2 = { airports: [{ id: "A1" }, { id: "A2" }] };
  ctx.resumeLandedDrone(cfg, "1", { splitPoint: [32.2, -90.0] }, env2);
  return { cfg, env2 };
}

let passed = 0;
const test = (name, fn) => { fn(); passed++; console.log(`  ✓ ${name}`); };

test("landed where it may not refuel: parked, mission over", () => {
  const { cfg } = run({ fuel_budget: 150, end_airport: "A1", refuel_options: ["A1"] });
  assert.strictEqual(cfg.start_airport, "A2");
  assert.strictEqual(cfg.end_airport, "A2");
  assert.strictEqual(cfg.start_fuel, 0);
});

test("landed where it may refuel: full tank, can fly again", () => {
  const { cfg } = run({ fuel_budget: 150, end_airport: "A1", refuel_options: ["A2"] });
  assert.strictEqual(cfg.start_airport, "A2");
  assert.strictEqual(cfg.end_airport, "A1");  // its planned landing airport is kept
  assert.strictEqual(cfg.start_fuel, 150);
  assert.strictEqual(cfg.refuels_used, 1);
});

test("legacy single refuel_airport also counts", () => {
  const { cfg } = run({ fuel_budget: 150, refuel_airport: "A2" });
  assert.strictEqual(cfg.start_fuel, 150);
});

test("no refuels left: parked", () => {
  const { cfg } = run({ fuel_budget: 150, refuel_options: ["A2"], max_refuels: 1 },
    { legFuelHistory: [42] });
  assert.strictEqual(cfg.start_fuel, 0);
  assert.strictEqual(cfg.end_airport, "A2");
});

test("route ended somewhere that is not an airport: parked there", () => {
  const { cfg, env2 } = run({ fuel_budget: 150, refuel_options: ["A2"] }, { route: ["C1-1", "T2"] });
  assert.strictEqual(cfg.start_airport, "D1_START");
  assert.strictEqual(cfg.start_fuel, 0);
  assert.ok(env2.synthetic_starts.D1_START.lat !== undefined);
});

console.log(`\n${passed} passed`);
