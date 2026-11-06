// Env tab fuel cell (webapp/isr.js setDroneFuel): one bar section per tank,
// amber above 90% of a tank, red over it or when the drone is lost, and one
// ticked bar once there are more legs than FUEL_BAR_MAX_SECTIONS. Runs the
// REAL code against a tiny fake DOM.
//   node tests/e2e/test_env_tab.js
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

const els = {};
const ctx = { $: (id) => (els[id] ||= { innerHTML: "", title: "", textContent: "" }) };
vm.createContext(ctx);
vm.runInContext(
  [extractConst("FUEL_BAR_MAX_SECTIONS"), extractConst("FUEL_WARN_SHARE"),
   extractFunction("fuelLegEnds"), extractFunction("setDroneFuel")].join("\n\n"),
  ctx,
);
const cell = () => els["stat-d1-fuel"];
const sections = () => (cell().innerHTML.match(/class="fuel-bar(?: [^"]*)?"/g) || []);
const text = () => cell().innerHTML.replace(/<[^>]+>/g, "");

let passed = 0;
function test(name, fn) { fn(); passed++; console.log(`  ✓ ${name}`); }

test("no refuel: one section, used / range", () => {
  ctx.setDroneFuel(1, [426], 7000, {});
  assert.strictEqual(sections().length, 1);
  assert.strictEqual(text(), "426 / 7000");
  assert.strictEqual(els["drone-legs-d1"].innerHTML, "");
});

test("refuels: a section per tank, amber near full, red over", () => {
  ctx.setDroneFuel(1, [950, 400, 1040], 990, { route: ["A1", "T1", "TK1", "T2", "TK2", "A3"] });
  assert.deepStrictEqual(sections(), ['class="fuel-bar warn"', 'class="fuel-bar"', 'class="fuel-bar over"']);
  assert.strictEqual(text(), "⛽ 2390 / 3×990");
  assert.match(cell().title, /Leg 1: A1 → TK1, 950 of 990 km/);
  assert.match(els["drone-legs-d1"].innerHTML, /<b>L2<\/b> TK1→TK2 400/);
});

test("leg ends are left out when they don't match the legs", () => {
  assert.strictEqual(ctx.fuelLegEnds(["A1", "T1", "A2"], 2), null);
  assert.deepStrictEqual(ctx.fuelLegEnds(["A1", "T1", "TK1", "A2"], 2), ["A1", "TK1", "A2"]);
});

test("more legs than sections: one bar with a tick per refuel", () => {
  const legs = Array(10).fill(500);
  ctx.setDroneFuel(1, legs, 990, {});
  assert.strictEqual(sections().length, 1);
  assert.strictEqual((cell().innerHTML.match(/<s /g) || []).length, 9);
});

test("lost drone: its whole tank, red", () => {
  ctx.setDroneFuel(1, [990], 990, { lost: true });
  assert.deepStrictEqual(sections(), ['class="fuel-bar over"']);
  assert.strictEqual(text(), "💀 990 / 990");
});

const lists = {};
const allocationCtx = {
  state: {
    env: { targets: [{ id: "T1" }, { id: "T2" }, { id: "T3" }] },
    routes: { "1": { route: ["A1", "T2", "TK1", "T3", "T3", "A2"] } },
    droneConfigs: {}, visitedTargets: ["T1", "T2"],
    visitedTargetByDrone: { T1: "1", T2: "2" },
  },
  $: () => null,
  setText: (id, value) => { lists[id] = value; },
  refreshDroneDetailRows: () => {}, updateAllocationBadge: () => {},
};
vm.createContext(allocationCtx);
vm.runInContext(["let shownAllocation;", extractFunction("getRouteTargetLabels"),
  extractFunction("getVisitedTargetsForDrone"), extractFunction("updateAllocationDisplay")].join("\n"), allocationCtx);

test("target lists retain visits, deduplicate plans, exclude tankers and other owners", () => {
  allocationCtx.updateAllocationDisplay(null, "geographic");
  assert.strictEqual(lists["drone-targets-d1"], "T1, T3");
  assert.strictEqual(lists["drone-targets-d2"], "T2");
});

test("disabled drone retains completed visits but not its remaining plan", () => {
  allocationCtx.state.droneConfigs["1"] = { enabled: false };
  allocationCtx.updateAllocationDisplay(null, "geographic");
  assert.strictEqual(lists["drone-targets-d1"], "T1");
});

test("fresh replay clears old target history", () => {
  allocationCtx.state.visitedTargets = [];
  allocationCtx.state.visitedTargetByDrone = {};
  allocationCtx.state.droneConfigs = {};
  allocationCtx.updateAllocationDisplay(null, "geographic");
  assert.strictEqual(lists["drone-targets-d1"], "T2, T3");
  assert.strictEqual(lists["drone-targets-d2"], "");
});

console.log(`\n${passed} passed`);
