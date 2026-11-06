const assert = require('node:assert/strict');
const test = require('node:test');
const { create } = require('../../webapp/view_preferences.js');
function fixture(overrides = {}) {
  const values = new Map(), writes = [], normalized = [];
  const options = { getStorage: () => ({ getItem: key => values.get(key) ?? null,
    setItem(key, value) { writes.push([key, value]); values.set(key, value); } }),
    now: () => 100000, storageKey: 'neutral-view', ttlMs: 1000, defaultZoom: 13,
    normalizeView(view) { normalized.push(view); return { ...view, normalized: true }; }, ...overrides };
  return { view: create(options), values, writes, normalized, options };
}
const record = Object.freeze({ centerLat: 1.123451, centerLng: 2.654321, zoomLevel: 6.01 });

test('construction is inert, storage resolves lazily and controllers have independent duplicate keys', () => {
  let calls = 0;
  const f = fixture({ getStorage() { calls++; return { setItem() {} }; } });
  assert.equal(calls, 0);
  f.view.save(record); f.view.save(record); assert.equal(calls, 1);
  create(f.options).save(record); assert.equal(calls, 2);
});

test('writes preserve unrounded fields/timestamp and suppress only matching rounded views', () => {
  const f = fixture(); f.view.save(record);
  assert.deepEqual(JSON.parse(f.writes[0][1]), { ...record, timestamp: 100000 });
  f.view.save({ ...record, centerLat: 1.123452, centerLng: 2.654322, zoomLevel: 6.02 });
  assert.equal(f.writes.length, 1);
  f.view.save({ ...record, zoomLevel: 6.2 }); assert.equal(f.writes.length, 2);
  assert.deepEqual(record, { centerLat: 1.123451, centerLng: 2.654321, zoomLevel: 6.01 });
});

test('failed writes retain duplicate suppression; malformed key formatting still throws', () => {
  let attempts = 0;
  const f = fixture({ getStorage() { attempts++; throw Error('denied'); } });
  assert.equal(f.view.save(record), undefined);
  f.view.save(record); assert.equal(attempts, 1);
  f.view.save({ ...record, zoomLevel: 7 }); assert.equal(attempts, 2);
  assert.throws(() => f.view.save({ ...record, centerLat: null }), TypeError);
  const clock = fixture({ now() { throw Error('clock'); } });
  assert.equal(clock.view.save(record), undefined); assert.deepEqual(clock.writes, []);
});

test('read preserves validation, exact expiry boundary, future timestamps and nullish zoom defaults', () => {
  const f = fixture();
  for (const raw of [undefined, '', '{', 'null', '[]', '{}',
    JSON.stringify({ centerLat: '1', centerLng: 2, timestamp: 100000 }),
    JSON.stringify({ centerLat: 1, centerLng: null, timestamp: 100000 }),
    JSON.stringify({ centerLat: 1, centerLng: 2, timestamp: 0 }),
    JSON.stringify({ centerLat: 1, centerLng: 2, timestamp: 98999 }),
  ]) { f.values.set('neutral-view', raw); assert.equal(f.view.read(), null); }
  assert.deepEqual(f.normalized, []);
  for (const [timestamp, zoom, expected] of [[99000, null, 13], [100001, '', ''], [100000, 0, 0]]) {
    f.values.set('neutral-view', JSON.stringify({ centerLat: 1, centerLng: 2, zoomLevel: zoom, timestamp }));
    assert.deepEqual(f.view.read(), { centerLat: 1, centerLng: 2, zoomLevel: expected, normalized: true });
  }
});

test('normalization receives only the existing view fields and read does not prime duplicate suppression', () => {
  const f = fixture();
  f.values.set('neutral-view', JSON.stringify({ ...record, timestamp: 100000, extra: 'ignored' }));
  assert.deepEqual(f.view.read(), { ...record, normalized: true });
  assert.deepEqual(f.normalized, [record]);
  f.view.save(record); assert.equal(f.writes.length, 1);
});

test('read catches storage, clock and normalization failures without changing stored data', () => {
  for (const override of [
    { getStorage() { throw Error('denied'); } },
    { now() { throw Error('clock'); } },
    { normalizeView() { throw Error('normalization'); } },
  ]) {
    const f = fixture(override), raw = JSON.stringify({ ...record, timestamp: 100000 });
    f.values.set('neutral-view', raw);
    assert.equal(f.view.read(), null); assert.equal(f.values.get('neutral-view'), raw);
    assert.deepEqual(f.writes, []);
  }
});
