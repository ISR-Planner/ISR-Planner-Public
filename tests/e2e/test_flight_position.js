// Shared geographic interpolation, independently checked against known arcs.
// Run: node tests/e2e/test_flight_position.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = vm.createContext({Math});
vm.runInContext(fs.readFileSync('webapp/latlng_utils.js', 'utf8'), context);
assert.equal(typeof context.interpolateFlightPosition, 'function');
const point = (from, to, fraction) => Array.from(context.interpolateFlightPosition(from, to, fraction));
const close = (actual, expected, message) => actual.forEach((value, axis) =>
  assert(Math.abs(value - expected[axis]) < 1e-8, `${message}: expected ${expected}, got ${actual}`));

close(point([0, 0], [0, 90], 0.5), [0, 45], 'Equatorial midpoint');
close(point([10, 20], [50, 20], 0.25), [20, 20], 'Meridian quarter point');
close(point([60, -45], [60, 45], 0.5), [67.7923457014035, 0],
  'High-latitude midpoint must bow north of the arithmetic latitude');

const segment = [[60.1045386, -65.1806243], [60.4598845, -64.2536139]];
const references = [
  [0.25, [60.19397860817895, -64.95075746516638]],
  [0.37, [60.23676798022576, -64.83997653051627]],
  [0.5, [60.28301868704448, -64.71963723534061]],
  [0.61, [60.32206868915331, -64.61754578994113]],
  [0.75, [60.37165520412122, -64.48725792009652]],
  [0.83, [60.3999330603981, -64.41262970053646]],
];
// Reference values use initial-bearing/destination equations, independently of
// the production interpolation. The reversed arc must describe the same point.
for (const [fraction, expected] of references) {
  close(point(...segment, fraction), expected, `DFW–DXB interior at ${fraction}`);
  close(point(segment[1], segment[0], 1 - fraction), expected, 'Reverse traversal');
}

for (const from of [[32.8998, -97.0403], [80, 179], [0, 0]]) {
  const to = [25.2532, 55.3657];
  assert.deepEqual(point(from, to, 0), from, 'Departure endpoint is exact');
  assert.deepEqual(point(from, to, 1), to, 'Arrival endpoint is exact');
  for (const fraction of [0, 0.25, 0.5, 0.75, 1]) {
    assert.deepEqual(point(from, from, fraction), from, 'Repeated-coordinate loiter stays stationary');
  }
}

const dateline = point([10, 179], [10, -179], 0.5);
assert(Math.abs(Math.abs(dateline[1]) - 180) < 1e-8, 'Short crossing stays at dateline');
const datelineLatitude = Math.atan(Math.tan(10 * Math.PI / 180) / Math.cos(Math.PI / 180)) * 180 / Math.PI;
close([dateline[0], Math.abs(dateline[1])], [datelineLatitude, 180], 'Dateline great-circle midpoint');
const original = JSON.stringify(segment);
point(...segment, 0.5);
assert.equal(JSON.stringify(segment), original, 'Interpolation leaves authoritative trajectory vertices intact');
console.log('PASS: independent great-circle interiors, reversed arcs, exact endpoints, stationary loiter and dateline');
