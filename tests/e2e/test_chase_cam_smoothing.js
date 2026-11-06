// smoothedCamForward (webapp/cesium_view.js): the chase-cam heading eases toward
// the drone's instead of snapping at a waypoint. Runs the REAL function against a
// minimal stand-in for the few Cesium vector operations it uses.
//   node tests/e2e/test_chase_cam_smoothing.js
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const src = fs.readFileSync(path.join(__dirname, "../../webapp/cesium_view.js"), "utf8");
function extract(name) {
  const start = src.indexOf(`function ${name}(`);
  let i = src.indexOf(") {", start) + 2, depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(start, i + 1);
  }
  throw new Error(name);
}

class C3 { constructor(x = 0, y = 0, z = 0) { this.x = x; this.y = y; this.z = z; } }
const Cartesian3 = {
  dot: (a, b) => a.x * b.x + a.y * b.y + a.z * b.z,
  magnitude: (a) => Math.hypot(a.x, a.y, a.z),
  subtract: (a, b, r) => Object.assign(r, { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z }),
  multiplyByScalar: (a, s, r) => Object.assign(r, { x: a.x * s, y: a.y * s, z: a.z * s }),
  divideByScalar: (a, s, r) => Object.assign(r, { x: a.x / s, y: a.y / s, z: a.z / s }),
  cross: (a, b, r) => Object.assign(r, { x: a.y * b.z - a.z * b.y, y: a.z * b.x - a.x * b.z, z: a.x * b.y - a.y * b.x }),
  normalize: (a, r) => { const m = Math.hypot(a.x, a.y, a.z); return Object.assign(r, { x: a.x / m, y: a.y / m, z: a.z / m }); },
};
// Rotation about the z axis is all these tests need (up = +z).
const Quaternion = { fromAxisAngle: (_axis, angle) => ({ angle }) };
const Matrix3 = {
  fromQuaternion: (q) => q,
  multiplyByVector: (q, v, r) => Object.assign(r, {
    x: v.x * Math.cos(q.angle) - v.y * Math.sin(q.angle),
    y: v.x * Math.sin(q.angle) + v.y * Math.cos(q.angle), z: v.z }),
};

let clock = 0;
const smoothInput = { value: "0.6" };
const ctx = {
  Cesium: { Cartesian3: Object.assign(C3, Cartesian3), Quaternion, Matrix3 },
  $: (id) => (id === "cesium-chase-smooth-s" ? smoothInput : null),
  Date: { now: () => clock },
  Math,
};
vm.createContext(ctx);
vm.runInContext(`let droneCamForward = {}; let droneCamTime = {}; const DEFAULT_CHASE_SMOOTH_S = 0.6;\n`
  + extract("currentChaseSmoothSeconds") + "\n" + extract("smoothedCamForward")
  + "\nthis.reset = () => { droneCamForward = {}; droneCamTime = {}; };", ctx);

const up = new C3(0, 0, 1);
const heading = (v) => (Math.atan2(v.y, v.x) * 180) / Math.PI;
const east = new C3(1, 0, 0), north = new C3(0, 1, 0), west = new C3(-1, 0, 0);

function fly(target, seconds, fps = 60) {
  let out;
  for (let f = 0; f < seconds * fps; f++) { clock += 1000 / fps; out = ctx.smoothedCamForward("1", target, up); }
  return out;
}

let passed = 0;
const test = (name, fn) => { ctx.reset(); clock = 0; smoothInput.value = "0.6"; fn(); passed++; console.log(`  ✓ ${name}`); };

test("a 90° turn eases in instead of snapping", () => {
  fly(east, 1);
  const after1frame = fly(north, 1 / 60);
  assert(heading(after1frame) > 0 && heading(after1frame) < 5, `first frame: ${heading(after1frame)}`);
  const after0_6s = fly(north, 0.6);
  assert(heading(after0_6s) > 50 && heading(after0_6s) < 70, `after ~tau: ${heading(after0_6s)}`);
  assert(Math.abs(heading(fly(north, 3)) - 90) < 1);  // ~99.5% after 5 time constants
});

test("a U-turn swings through a real direction (no degenerate flip)", () => {
  fly(east, 1);
  const mid = fly(west, 0.6);
  const m = Math.hypot(mid.x, mid.y);
  assert(Math.abs(m - 1) < 1e-9, "heading stays a unit vector");
  assert(Math.abs(heading(mid)) > 60 && Math.abs(heading(mid)) < 150, `mid-turn: ${heading(mid)}`);
  assert(Math.abs(Math.abs(heading(fly(west, 4))) - 180) < 1);
});

test("Turn = 0 snaps instantly (old behaviour)", () => {
  smoothInput.value = "0";
  fly(east, 1);
  assert(Math.abs(heading(fly(north, 1 / 60)) - 90) < 1e-6);
});

console.log(`\n${passed} passed`);
