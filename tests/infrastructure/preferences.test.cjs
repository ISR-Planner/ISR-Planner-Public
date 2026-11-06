const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const { create } = require('../../webapp/preferences.js');

function memoryStorage(initial = {}) {
  const data = new Map(Object.entries(initial));
  return {
    data,
    get length() { return data.size; },
    key: index => [...data.keys()][index] ?? null,
    getItem: key => data.get(key) ?? null,
    setItem: (key, value) => data.set(key, value),
    removeItem: key => data.delete(key),
  };
}

test('reads existing string preferences verbatim and supplies missing defaults', () => {
  const storage = memoryStorage({ language: '中文', empty: '' });
  const prefs = create(() => storage);
  assert.equal(prefs.read('language'), '中文');
  assert.equal(prefs.read('empty', 'fallback'), '');
  assert.equal(prefs.read('missing'), null);
  assert.equal(prefs.read('missing', 'fallback'), 'fallback');
  storage.setItem('language', 'en');
  assert.equal(prefs.read('language'), 'en');
});

test('treats non-string or unavailable storage values as missing', () => {
  const prefs = create(() => ({ getItem: () => ({ broken: true }) }));
  assert.equal(prefs.read('bad', 'default'), 'default');
  for (const getStorage of [() => null, () => { throw new Error('access denied'); }]) {
    const unavailable = create(getStorage);
    assert.equal(unavailable.read('key', 'default'), 'default');
    assert.equal(unavailable.write([['key', 'value']]), false);
    assert.equal(unavailable.removeMatching('old_'), false);
  }
});

test('writes only the requested string preferences and retains unrelated keys', () => {
  const storage = memoryStorage({ unrelated: 'keep', old: 'retain' });
  const prefs = create(() => storage);
  assert.equal(prefs.write([['provider', 'test'], ['model_test', 'example']]), true);
  assert.deepEqual(Object.fromEntries(storage.data), {
    unrelated: 'keep', old: 'retain', provider: 'test', model_test: 'example',
  });
});

test('rejects malformed writes before touching storage', () => {
  const storage = memoryStorage({ key: 'original' });
  const prefs = create(() => storage);
  for (const entries of [null, {}, [['key', null]], [['key', 2]], [['', 'bad']], [['key']], [['key', 'ok'], ['x', {}]]]) {
    assert.equal(prefs.write(entries), false);
    assert.deepEqual(Object.fromEntries(storage.data), { key: 'original' });
  }
});

test('restores earlier entries if a multi-preference write fails', () => {
  const storage = memoryStorage({ provider: 'original', unrelated: 'keep' });
  const setItem = storage.setItem;
  storage.setItem = (key, value) => {
    if (key === 'broken') throw new Error('quota');
    setItem(key, value);
  };
  const prefs = create(() => storage);
  assert.equal(prefs.write([['provider', 'new'], ['new_key', 'new'], ['broken', 'value']]), false);
  assert.deepEqual(Object.fromEntries(storage.data), { provider: 'original', unrelated: 'keep' });
});

test('reports a failed write even when rollback is unavailable', () => {
  const prefs = create(() => ({
    getItem: () => 'old',
    setItem() { throw new Error('denied'); },
    removeItem() { throw new Error('denied'); },
  }));
  assert.equal(prefs.write([['key', 'new']]), false);
});

test('removes all matching legacy keys without removing other preferences', () => {
  const storage = memoryStorage({ old_a: 'test', keep: 'yes', old_b: 'test', model: 'example' });
  const prefs = create(() => storage);
  assert.equal(prefs.removeMatching('old_'), true);
  assert.deepEqual(Object.fromEntries(storage.data), { keep: 'yes', model: 'example' });
  assert.equal(prefs.removeMatching(''), false);
});

test('handles read and enumeration failures without throwing', () => {
  const storage = memoryStorage();
  storage.getItem = () => { throw new Error('read denied'); };
  storage.key = () => { throw new Error('enumeration denied'); };
  storage.data.set('old_key', 'test');
  const prefs = create(() => storage);
  assert.equal(prefs.read('key'), null);
  assert.equal(prefs.write([['key', 'new']]), false);
  assert.equal(prefs.removeMatching('old_'), false);
});

test('every shipped page loads infrastructure modules before the main application', () => {
  for (const filename of ['index.html', 'weather_preview.html', '_weather_diagnostics/index.html']) {
    const html = fs.readFileSync(path.join(__dirname, '../../webapp', filename), 'utf8');
    const scripts = [...html.matchAll(/<script\s+src="([^"?]+)(?:\?[^"]*)?"/g)].map(match => match[1]);
    assert.equal(scripts.filter(src => src === '/static/preferences.js').length, 1, filename);
    assert(scripts.indexOf('/static/preferences.js') < scripts.indexOf('/static/isr.js'), filename);
    assert.equal(scripts.filter(src => src === '/static/http_client.js').length, 1, filename);
    assert(scripts.indexOf('/static/http_client.js') < scripts.indexOf('/static/isr.js'), filename);
    assert.equal(scripts.filter(src => src === '/static/tab_controls.js').length, 1, filename);
    assert(scripts.indexOf('/static/tab_controls.js') < scripts.indexOf('/static/isr.js'), filename);
    assert.equal(scripts.filter(src => src === '/static/modal_control.js').length, 1, filename);
    assert(scripts.indexOf('/static/modal_control.js') < scripts.indexOf('/static/isr.js'), filename);
    assert.equal(scripts.filter(src => src === '/static/frontend_ui.js').length, 1, filename);
    assert(scripts.indexOf('/static/frontend_ui.js') < scripts.indexOf('/static/isr.js'), filename);
    for (const dependency of ['dom_helpers', 'debug_output', 'voice_controls', 'trace_view', 'chat_view', 'fleet_drawers', 'memory_view']) {
      assert(scripts.indexOf(`/static/${dependency}.js`) < scripts.indexOf('/static/frontend_ui.js'), filename);
    }
    assert.equal(scripts.filter(src => src === '/static/view_preferences.js').length, 1, filename);
    assert(scripts.indexOf('/static/view_preferences.js') < scripts.indexOf('/static/isr.js'), filename);
    assert.equal(scripts.filter(src => src === '/static/memory_view.js').length, 1, filename);
    assert(scripts.indexOf('/static/memory_view.js') < scripts.indexOf('/static/isr.js'), filename);
    assert.equal(scripts.filter(src => src === '/static/fleet_drawers.js').length, 1, filename);
    assert(scripts.indexOf('/static/fleet_drawers.js') < scripts.indexOf('/static/isr.js'), filename);
    assert.equal(scripts.filter(src => src === '/static/chat_view.js').length, 1, filename);
    assert(scripts.indexOf('/static/chat_view.js') < scripts.indexOf('/static/isr.js'), filename);
    assert.equal(scripts.filter(src => src === '/static/trace_view.js').length, 1, filename);
    assert(scripts.indexOf('/static/trace_view.js') < scripts.indexOf('/static/isr.js'), filename);
    assert.equal(scripts.filter(src => src === '/static/voice_controls.js').length, 1, filename);
    assert(scripts.indexOf('/static/voice_controls.js') < scripts.indexOf('/static/isr.js'), filename);
    assert(scripts.indexOf('/static/voice.js') < scripts.indexOf('/static/voice_controls.js'), filename);
    assert.equal(scripts.filter(src => src === '/static/debug_output.js').length, 1, filename);
    assert(scripts.indexOf('/static/debug_output.js') < scripts.indexOf('/static/isr.js'), filename);
    assert.equal(scripts.filter(src => src === '/static/llm_settings.js').length, 1, filename);
    assert(scripts.indexOf('/static/llm_settings.js') < scripts.indexOf('/static/isr.js'), filename);
    for (const dependency of ['preferences', 'http_client', 'modal_control']) {
      assert(scripts.indexOf(`/static/${dependency}.js`) < scripts.indexOf('/static/llm_settings.js'), filename);
    }
    assert.equal(scripts.filter(src => src === '/static/dom_helpers.js').length, 1, filename);
    assert(scripts.indexOf('/static/dom_helpers.js') < scripts.indexOf('/static/isr.js'), filename);
  }
});
