// Exercise the actual live/recorded weather adapters, including NOAA's valid
// three-position triangle representation. Rendering and solver are adapters.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const {harness} = require('./test_weather_accept');
const copy = value => JSON.parse(JSON.stringify(value));
const capture = JSON.parse(fs.readFileSync('webapp/_weather_diagnostics/noaa_20261012T012337Z.json'));

// Assert the preservation contract against the original input, independently of
// the normalizer: metadata and existing vertices are identical; only a missing
// repeated first position may be appended. Polygon holes count as rings too.
function assertOnlyRingClosure(actual, original) {
  const a = copy(actual), b = copy(original);
  assert.equal(a.cells.length, b.cells.length, 'no advisory was discarded');
  for (let i = 0; i < b.cells.length; i++) {
    const input = b.cells[i].geometry, output = a.cells[i].geometry;
    if (input.type === 'MultiPolygon') {
      assert.equal(output.coordinates.length, input.coordinates.length, 'polygon count is unchanged');
      assert.deepEqual(output.coordinates.map(p => p.length), input.coordinates.map(p => p.length),
        'each hole stays with its original polygon');
    }
    const rings = g => g.type === 'Polygon' ? g.coordinates : g.coordinates.flat();
    const before = rings(input), after = rings(output);
    assert.equal(after.length, before.length, 'all polygons and holes are retained');
    for (let j = 0; j < before.length; j++) {
      const source = before[j], normalized = after[j];
      const open = source[0][0] !== source.at(-1)[0] || source[0][1] !== source.at(-1)[1];
      assert.equal(normalized.length, source.length + Number(open));
      assert.deepEqual(normalized.slice(0, source.length), source, 'existing positions are untouched');
      assert.deepEqual(normalized.at(-1), source[0], 'last position closes the ring');
    }
    // Coordinates are checked above; compare every remaining metadata field.
    delete input.coordinates;
    delete output.coordinates;
  }
  assert.deepEqual(a, b, 'capture, advisory and geometry metadata are unchanged');
}

function triangleWeather() {
  const weather = copy(capture), template = weather.cells[0];
  weather.cells = [
    {...template, id: 'TRI', identity: 'TEST-TRI', rule: 'No fly',
      box: [-101, 39, -99, 41], validAt: null,
      validFrom: '2026-10-12T01:00:00Z', validTo: '2026-10-12T04:00:00Z',
      geometry: {type: 'Polygon', coordinates: [[[-101, 39], [-99, 39], [-100, 41]]]}},
    {...template, id: 'MULTI', identity: 'TEST-MULTI', rule: 'Other advisory',
      box: [-103, 36, -95, 43], validAt: null,
      validFrom: '2026-10-12T01:00:00Z', validTo: '2026-10-12T04:00:00Z',
      geometry: {type: 'MultiPolygon', sourceDetail: 'preserve me', coordinates: [
        [[[-103, 36], [-98, 36], [-98, 41], [-103, 41]],
          [[-102, 37], [-101, 39], [-100, 37]]],
        [[[-97, 41], [-95, 41], [-96, 43], [-97, 41]]],
      ]}},
  ];
  return weather;
}

function liveCapture(h, weather) {
  // Deliberately return the same object so mutation would be observable.
  h.context.fetch = async url => {
    assert.equal(url, '/api/weather/current');
    return {ok: true, json: async () => weather};
  };
}

module.exports = {assertOnlyRingClosure};
if (require.main === module) (async () => {
  const h = harness(), weather = triangleWeather(), before = copy(weather);
  liveCapture(h, weather);
  await h.getWeather();
  assert.equal(h.element('wp-get').textContent, '✓ Forecast loaded');
  assert.match(h.element('wp-status').textContent, /^2 advisories/);
  const saved = h.context.weatherPreview.exportDemo();
  assertOnlyRingClosure(saved.weather, weather);
  assert.deepEqual(copy(saved.weather.cells[0].geometry.coordinates[0]),
    [[-101, 39], [-99, 39], [-100, 41], [-101, 39]]);
  assert.deepEqual(weather, before, 'live retrieval never mutates provider data');
  const request = h.context.buildSolveRequest();
  assert.equal(request.weatherApplied, true);
  assert.deepEqual(copy(request.payload.env.weather_snapshot.advisories.map(c => c.id)), ['TRI', 'MULTI']);
  console.log('PASS live three-position triangle loads, closes and reaches the solver without losing advisories');

  const savedBefore = copy(saved), replay = harness();
  replay.context.weatherPreview.loadDemo(saved, 'triangles.json');
  const exported = replay.context.weatherPreview.exportDemo();
  assert.deepEqual(copy(exported.weather), copy(saved.weather));
  replay.context.weatherPreview.loadDemo(exported, 'triangles-again.json');
  assert.deepEqual(copy(replay.context.weatherPreview.exportDemo().weather), copy(saved.weather));
  assert.deepEqual(copy(saved), savedBefore, 'demo import leaves its caller-owned input unchanged');
  assertOnlyRingClosure(exported.weather, before);
  console.log('PASS Save/Load is idempotent for triangles, MultiPolygons and interior holes');

  const legacy = JSON.parse(fs.readFileSync('webapp/weather_preview_noaa.json'));
  const legacyBefore = copy(legacy), d2 = legacy.cells.find(c => c.identity === 'D2');
  assert.equal(d2.geometry.coordinates[0].length, 3, 'real saved NOAA D2 has three positions');
  const old = harness();
  old.context.weatherPreview.useRecordedCapture(legacy);
  const normalizedLegacy = await old.context.weatherDataSource.current();
  assertOnlyRingClosure(normalizedLegacy, legacy);
  assert.deepEqual(legacy, legacyBefore);
  const oldDemo = copy(saved); oldDemo.weather = legacy;
  old.context.weatherPreview.loadDemo(oldDemo, 'older-capture.json');
  assertOnlyRingClosure(old.context.weatherPreview.exportDemo().weather, legacy);
  assert.deepEqual(oldDemo.weather, legacyBefore);
  console.log('PASS bundled historical NOAA D2 triangle loads through recorded capture and portable demo paths');

  const badRings = [
    [[-101, 39], [-99, 39], [-101, 39]], // Three entries but just two vertices.
    [[-101, 39], [-99, 39]],
    [[-101, 39], [-99, 39], [NaN, 41]],
    [[-101, 39], [-99, 39], [Infinity, 41]],
    [[-101, 39], [-99, 39], [-100, 91]],
    [[-101, 39], [-99, 39], [541, 41]],
  ];
  for (const ring of badRings) {
    const broken = triangleWeather();
    broken.cells[0].geometry.coordinates = [ring];
    const failed = harness(); liveCapture(failed, broken);
    await failed.getWeather();
    const message = failed.element('wp-status').textContent;
    assert.match(message, /Weather advisory TEST-TRI has invalid polygon coordinates/);
    assert.doesNotMatch(message, /demo/i, 'live failures must not be mislabeled as demo failures');
    assert.equal(failed.element('wp-get').textContent, 'Retry forecast');
    assert.throws(() => failed.context.weatherPreview.exportDemo(), /Load weather/);
    const envBefore = copy(replay.state.env), retained = copy(replay.context.weatherPreview.exportDemo());
    const brokenDemo = copy(saved); brokenDemo.weather = broken;
    assert.throws(() => replay.context.weatherPreview.loadDemo(brokenDemo), /Weather advisory TEST-TRI.*invalid polygon/);
    assert.deepEqual(copy(replay.state.env), envBefore, 'invalid demo cannot partially import a mission');
    assert.deepEqual(copy(replay.context.weatherPreview.exportDemo()), retained);
    assert.throws(() => replay.context.weatherPreview.useRecordedCapture(broken), /Weather advisory TEST-TRI.*invalid polygon/);
    assert.deepEqual(copy(await replay.context.weatherDataSource.current()), retained.weather,
      'invalid recorded capture cannot replace the last valid weather');
  }
  console.log('PASS malformed live and recorded rings report the advisory and leave valid demo state intact');
})().catch(error => {console.error(error); process.exitCode = 1;});
