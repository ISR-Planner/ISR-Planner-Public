const assert = require('node:assert/strict');
const test = require('node:test');
const { create } = require('../../webapp/debug_output.js');

test('construction is inert; output lookup and time formatting occur in order on each write', () => {
  const calls = [];
  const output = { textContent: 'existing' };
  const writer = create({ getOutput() { calls.push('lookup'); return output; },
    formatTime() { calls.push('time'); return 'neutral-time'; } });
  assert.deepEqual(calls, []);
  assert.equal(writer.prepend('<b>literal</b>'), undefined);
  writer.prepend(0);
  assert.deepEqual(calls, ['lookup', 'time', 'lookup', 'time']);
  assert.equal(output.textContent, '[neutral-time] 0\n[neutral-time] <b>literal</b>\nexisting');
});

test('replacement nodes are resolved lazily; missing output avoids clock and message coercion', () => {
  let output = null, clocks = 0, conversions = 0;
  const message = { toString() { conversions++; return 'neutral message'; } };
  const writer = create({ getOutput: () => output, formatTime: () => { clocks++; return 'time'; } });
  assert.equal(writer.prepend(message), undefined);
  assert.equal(clocks, 0); assert.equal(conversions, 0);
  const old = output = { textContent: 'old' };
  writer.prepend(message);
  const replacement = output = { textContent: 'replacement' };
  writer.prepend(null);
  assert.equal(old.textContent, '[time] neutral message\nold');
  assert.equal(replacement.textContent, '[time] null\nreplacement');
  assert.equal(conversions, 1);
  assert.equal(clocks, 2);
});

test('lookup, clock, interpolation, and assignment failures propagate without fallback writes', () => {
  const error = new Error('neutral failure');
  const output = { textContent: 'unchanged' };
  const fail = () => { throw error; };
  for (const options of [
    { getOutput: fail, formatTime: fail },
    { getOutput: () => output, formatTime: fail },
  ]) assert.throws(() => create(options).prepend('message'), value => value === error);
  const writer = create({ getOutput: () => output, formatTime: () => 'time' });
  assert.throws(() => writer.prepend({ toString: fail }), value => value === error);
  assert.equal(output.textContent, 'unchanged');
  const refusing = { get textContent() { return 'existing'; }, set textContent(_) { throw error; } };
  assert.throws(() => create({ getOutput: () => refusing, formatTime: () => 'time' }).prepend('message'),
    value => value === error);
});
