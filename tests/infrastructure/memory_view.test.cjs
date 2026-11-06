const assert = require('node:assert/strict');
const test = require('node:test');
const { create } = require('../../webapp/memory_view.js');
const escapeHtml = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');

function fixture() {
  let list = { innerHTML: 'existing' };
  const escaped = [];
  const view = create({ getList: () => list,
    escapeHtml(value) { escaped.push(value); return escapeHtml(value); } });
  return { view, escaped, get list() { return list; }, replace(value) { list = value; } };
}

test('memory view construction is inert and missing/replacement lists resolve lazily', () => {
  const fail = () => { throw Error('unexpected access'); };
  create({ getList: fail, escapeHtml: fail });
  const f = fixture(); const old = f.list;
  f.replace(null);
  assert.equal(f.view.render(new Proxy({}, { get: fail })), undefined);
  assert.deepEqual(f.escaped, []);
  f.replace({ innerHTML: 'replacement' });
  f.view.render([]);
  assert.match(f.list.innerHTML, /No memories yet\. Add instructions the agent should remember\./);
  assert.equal(old.innerHTML, 'existing');
});

test('absent and empty record collections preserve empty text/style without escaping', () => {
  const f = fixture();
  for (const records of [null, undefined, []]) {
    assert.equal(f.view.render(records), undefined);
    assert.equal(f.list.innerHTML, '<div style="color: #6b7280; font-size: 0.7rem; text-align: center; padding: 0.5rem;">No memories yet. Add instructions the agent should remember.</div>');
  }
  assert.deepEqual(f.escaped, []);
});

test('records retain order, IDs/categories, escaped content and inline delete dispatch without mutation', () => {
  const f = fixture();
  const records = Object.freeze([
    Object.freeze({ id: 7, category: 'general', content: '<b>neutral</b> & note' }),
    Object.freeze({ id: 9, category: 'preference', content: 'second neutral note' }),
  ]);
  const before = JSON.stringify(records);
  f.view.render(records);
  assert.deepEqual(f.escaped, records.map(record => record.content));
  assert(f.list.innerHTML.indexOf('data-id="7"') < f.list.innerHTML.indexOf('data-id="9"'));
  assert.match(f.list.innerHTML, /class="memory-category">general<\/span>/);
  assert.match(f.list.innerHTML, /&lt;b&gt;neutral&lt;\/b&gt; &amp; note/);
  assert.match(f.list.innerHTML, /onclick="deleteAgentMemory\(7\)">×/);
  assert.match(f.list.innerHTML, /onclick="deleteAgentMemory\(9\)">×/);
  assert.equal(JSON.stringify(records), before);
});

test('lookup, escaping, malformed input and assignment failures propagate without fallback rendering', () => {
  const f = fixture(); const error = new Error('neutral failure'); const fail = () => { throw error; };
  assert.throws(() => create({ getList: fail, escapeHtml }).render([]), value => value === error);
  assert.throws(() => create({ getList: () => f.list, escapeHtml: fail }).render([
    { id: 1, category: 'general', content: 'neutral' },
  ]), value => value === error);
  for (const records of [[null], { length: 1 }]) assert.throws(() => f.view.render(records), TypeError);
  assert.equal(f.list.innerHTML, 'existing');
  const refusing = { set innerHTML(_) { throw error; } };
  assert.throws(() => create({ getList: () => refusing, escapeHtml }).render([]), value => value === error);
});

test('borrowed memory records stay caller-owned and explicit rerender refreshes IDs and text', () => {
  const f = fixture();
  const record = { id: 1, category: 'general', content: 'first neutral' };
  const records = [record], before = JSON.stringify(records);
  f.view.render(records); const firstHTML = f.list.innerHTML;
  assert.equal(JSON.stringify(records), before);
  record.id = 2; record.category = 'preference'; record.content = 'updated <neutral>';
  assert.equal(f.list.innerHTML, firstHTML);
  assert.deepEqual(f.escaped, ['first neutral']);
  const changed = JSON.stringify(records);
  f.view.render(records);
  assert.match(f.list.innerHTML, /data-id="2"/);
  assert.match(f.list.innerHTML, /deleteAgentMemory\(2\)/);
  assert.match(f.list.innerHTML, /preference/);
  assert.match(f.list.innerHTML, /updated &lt;neutral&gt;/);
  assert.equal(JSON.stringify(records), changed);
  assert.deepEqual(f.escaped, ['first neutral', 'updated <neutral>']);
  f.view.render([]); record.content = 'old record changed';
  assert.match(f.list.innerHTML, /No memories yet/);
});
