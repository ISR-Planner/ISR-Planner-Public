const assert = require('node:assert/strict');
const test = require('node:test');
const { create } = require('../../webapp/dom_helpers.js');

test('construction is inert; lookup forwards IDs verbatim and returns the current element', () => {
  let current = { textContent: 'first' };
  const ids = [];
  const dom = create({ getElementById(id) { ids.push(id); return current; } });
  assert.deepEqual(ids, []);
  const first = current;
  assert.equal(dom.getById('a:b [c]'), first);
  current = { textContent: 'replacement' };
  assert.equal(dom.getById('a:b [c]'), current);
  dom.setText('a:b [c]', 'updated');
  assert.equal(current.textContent, 'updated');
  assert.equal(first.textContent, 'first');
  current = null;
  assert.equal(dom.getById('missing'), null);
  assert.deepEqual(ids, ['a:b [c]', 'a:b [c]', 'a:b [c]', 'missing']);
});

test('text writes preserve nullish clearing and explicit string conversion', () => {
  const element = { textContent: '' };
  const dom = create({ getElementById: () => element });
  for (const [value, expected] of [
    [null, ''], [undefined, ''], ['', ''], [0, '0'], [false, 'false'],
    ['<b>literal</b>', '<b>literal</b>'], ['é 🌦', 'é 🌦'],
    [{ toString: () => 'custom' }, 'custom'],
  ]) {
    assert.equal(dom.setText('example', value), undefined);
    assert.equal(element.textContent, expected);
  }
});

test('missing elements are a no-op and do not convert the supplied value', () => {
  const dom = create({ getElementById: () => null });
  assert.equal(dom.setText('missing', { toString() { throw new Error('must not run'); } }), undefined);
});

test('lookup and conversion errors propagate to the caller', () => {
  const error = new Error('test failure');
  const failedLookup = create({ getElementById() { throw error; } });
  assert.throws(() => failedLookup.getById('example'), actual => actual === error);
  assert.throws(() => failedLookup.setText('example', 'value'), actual => actual === error);
  const element = { textContent: 'original' };
  const dom = create({ getElementById: () => element });
  assert.throws(() => dom.setText('example', { toString() { throw error; } }), actual => actual === error);
  assert.equal(element.textContent, 'original');
});

test('HTML text escaping creates detached nodes lazily and delegates conversion to the supplied DOM', () => {
  const writes = [], created = [];
  const dom = create({ createElement(tag) {
    assert.equal(tag, 'div');
    const element = { set textContent(value) { writes.push(value); },
      get innerHTML() { return `serialized-${created.length}`; } };
    created.push(element); return element;
  } });
  assert.deepEqual(created, []);
  const noCoercion = { toString() { throw Error('conversion belongs to the DOM'); } };
  for (const value of [null, undefined, 0, false, '<neutral>', noCoercion]) {
    const result = dom.escapeHtml(value);
    assert.equal(writes.at(-1), value);
    assert.equal(result, `serialized-${created.length}`);
  }
  assert.equal(created.length, 6);
  assert.equal(new Set(created).size, 6);
});

test('HTML text escaping propagates element creation, assignment and serialization failures', () => {
  const error = new Error('neutral escaping failure');
  const fail = () => { throw error; };
  for (const document of [
    { createElement: fail },
    { createElement: () => ({ set textContent(_) { throw error; } }) },
    { createElement: () => ({ set textContent(_) {}, get innerHTML() { throw error; } }) },
  ]) {
    const dom = create(document);
    assert.throws(() => dom.escapeHtml('neutral'), value => value === error);
  }
});
