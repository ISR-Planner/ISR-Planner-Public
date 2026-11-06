// Forward/inverse geometry of the actual shared spherical flight curve.
// Run: node tests/e2e/test_flight_projection.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {};
vm.createContext(context);
vm.runInContext(fs.readFileSync('webapp/latlng_utils.js', 'utf8'), context);
const {closestFlightPosition: closest, interpolateFlightPosition: interpolate,
  haversineKm: distance} = context;
const earthRadius = 6371.0088;
const close = (actual, expected, label, tolerance = 1e-9) =>
  assert(Math.abs(actual - expected) <= tolerance, `${label}: ${actual} vs ${expected}`);
const pointClose = (actual, expected, label) =>
  assert(distance(...actual, ...expected) < 1e-7, `${label}: ${actual} vs ${expected}`);

// Independent analytic nearest points: equatorial and meridional arcs.
const equator = closest([0, -10], [0, 10], [15, 0]);
close(equator.fraction, 0.5, 'Equator fraction');
pointClose(equator.point, [0, 0], 'Equator perpendicular foot');
close(equator.distanceKm, earthRadius * Math.PI / 12, 'Equator cross-track distance');
const meridian = closest([-20, 0], [30, 0], [0, 12]);
close(meridian.fraction, 0.4, 'Meridian fraction');
pointClose(meridian.point, [0, 0], 'Meridian perpendicular foot');
close(meridian.distanceKm, earthRadius * Math.PI / 15, 'Meridian cross-track distance');
for (const [query, fraction, expected] of [
  [[5, -35], 0, [0, -10]], [[5, 30], 1, [0, 10]],
]) {
  const result = closest([0, -10], [0, 10], query);
  assert.equal(result.fraction, fraction, 'An outside projection stops at the nearest endpoint');
  pointClose(result.point, expected, 'Finite-arc endpoint');
  close(result.distanceKm, distance(...query, ...expected), 'Endpoint distance');
}
const dateline = closest([0, 179], [0, -179], [10, 180]);
close(dateline.fraction, 0.5, 'Dateline fraction');
pointClose(dateline.point, [0, 180], 'Dateline perpendicular foot');
close(dateline.distanceKm, earthRadius * Math.PI / 18, 'Dateline cross-track distance');
const polar = closest([80, -90], [80, 90], [85, 0]);
close(polar.fraction, 0.5, 'Polar fraction');
pointClose(polar.point, [90, 0], 'Polar perpendicular foot');
close(polar.distanceKm, earthRadius * Math.PI / 36, 'Polar cross-track distance');
console.log('PASS analytic finite-arc nearest points, endpoints, dateline and polar geometry');

// Exact forward/inverse round trips include the reported DFW-DXB segment,
// its full long-range leg, high latitudes, seam crossings, and both directions.
const arcs = [
  [[60.10453860289056, -65.18062433043991], [60.45988446087058, -64.25361392871031]],
  [[32.89891536470137, -97.02979087829596], [25.25421234133669, 55.3619956970214]],
  [[66, -40], [66.2, -38]], [[10, 179], [11, -179]], [[80, -90], [80, 90]],
];
for (const original of arcs) for (const [from, to] of [original, [...original].reverse()]) {
  for (const fraction of [0, 0.001, 0.1, 0.25, 0.5, 0.75, 0.999, 1]) {
    const point = interpolate(from, to, fraction), result = closest(from, to, point);
    close(result.fraction, fraction, 'Forward/inverse fraction');
    pointClose(result.point, point, 'Forward/inverse point');
    assert(result.distanceKm < 1e-7, 'Forward point lies on the inverse curve');
  }
}
console.log('PASS forward/inverse round trips on actual route, long-range, reversed and seam-crossing arcs');

// An independent dense search bounds nearest-point error for off-track queries.
for (const [from, to, query] of [
  [...arcs[0], [60.3, -64.5]], [...arcs[1], [60, -20]], [...arcs[3], [13, -179.7]],
]) {
  const result = closest(from, to, query);
  let bestSample = Infinity;
  for (let i = 0; i <= 2000; i++) {
    bestSample = Math.min(bestSample, distance(...query, ...interpolate(from, to, i / 2000)));
  }
  assert(result.distanceKm <= bestSample + 1e-7, 'Spherical inverse finds at least the dense-search minimum');
  assert(result.fraction >= 0 && result.fraction <= 1);
  pointClose(result.point, interpolate(from, to, result.fraction), 'Inverse point uses the forward curve');
}
console.log('PASS off-track inverse agrees with independent dense search');

for (const [from, to] of [[[40, -100], [40, -100]], [[0, 180], [0, -180]]]) {
  const before = JSON.stringify([from, to]);
  const result = closest(from, to, [35, -110]);
  assert.equal(result.fraction, 0, 'Stationary duplicate keeps the start of its hold');
  assert.equal(JSON.stringify([from, to]), before, 'Inverse never mutates stored geometry');
  assert.notEqual(result.point, from, 'Returned point is an independent value');
  pointClose(result.point, from, 'Stationary point');
  assert(Number.isFinite(result.distanceKm));
}
const circlePole = closest([0, -20], [0, 20], [90, 0]);
assert(Number.isFinite(circlePole.fraction) && Number.isFinite(circlePole.distanceKm));
close(circlePole.distanceKm, earthRadius * Math.PI / 2, 'Great-circle pole has finite equal distance');
console.log('PASS stationary holds, equivalent seam points and perpendicular-pole degeneracy');
