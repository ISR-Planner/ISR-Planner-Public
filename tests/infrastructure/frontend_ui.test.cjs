const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const { create } = require('../../webapp/frontend_ui.js');
const names = ['dom', 'debugOutput', 'voiceControls', 'traceView', 'chatView', 'fleetDrawers', 'memoryView'];
const keys = ['domHelpers', ...names.slice(1)];
const fail = () => { throw Error('unexpected eager dependency access'); };
function fixture(overrides = {}) {
  const calls = [], instances = {};
  const factories = Object.fromEntries(names.map((name, index) => [name, { create(options) {
    calls.push(name); return instances[keys[index]] = { options, name };
  } }]));
  const options = { document: new Proxy({}, { get: fail }), factories,
    getById: fail, escapeHtml: fail, getVoice: fail, fetchRequest: fail,
    formatDebugTime: fail, formatTraceTime: fail, formatChatTime: fail, createMessageId: fail,
    ...overrides };
  return { calls, instances, options };
}

test('composition invokes the seven named factories once in order and returns their exact instances/adapters', () => {
  const f = fixture(); const result = create(f.options);
  assert.deepEqual(f.calls, names); assert.deepEqual(Object.keys(result), keys);
  for (const key of keys) assert.equal(result[key], f.instances[key]);
  assert.equal(result.domHelpers.options, f.options.document);
  for (const key of ['voiceControls', 'chatView', 'fleetDrawers'])
    assert.equal(result[key].options.document, f.options.document);
  assert.equal(result.voiceControls.options.getById, f.options.getById);
  assert.equal(result.voiceControls.options.getVoice, f.options.getVoice);
  assert.equal(result.voiceControls.options.fetchRequest, f.options.fetchRequest);
  assert.equal(result.fleetDrawers.options.getById, f.options.getById);
  assert.equal(result.debugOutput.options.formatTime, f.options.formatDebugTime);
  assert.equal(result.traceView.options.formatTime, f.options.formatTraceTime);
  assert.equal(result.chatView.options.formatTime, f.options.formatChatTime);
  assert.equal(result.chatView.options.createMessageId, f.options.createMessageId);
  for (const key of ['traceView', 'chatView', 'memoryView'])
    assert.equal(result[key].options.escapeHtml, f.options.escapeHtml);
  assert.equal(result.init, undefined); assert.equal(result.dispose, undefined);
});

test('fixed-ID adapters resolve current nodes lazily and preserve lookup errors', () => {
  const ids = []; let current = { neutral: 1 };
  const f = fixture({ getById(id) { ids.push(id); return current; } });
  const result = create(f.options); assert.deepEqual(ids, []);
  const lookups = [[result.debugOutput.options.getOutput, 'debug-output'],
    [result.traceView.options.getContainer, 'agents-trace-content'],
    [result.chatView.options.getHistory, 'agent-chat-history'],
    [result.memoryView.options.getList, 'memory-list']];
  for (const [lookup, id] of lookups) {
    assert.equal(lookup(), current); assert.equal(ids.at(-1), id);
  }
  current = null;
  for (const [lookup] of lookups) assert.equal(lookup(), null);
  const error = new Error('neutral lookup failure');
  const bad = create(fixture({ getById() { throw error; } }).options);
  assert.throws(() => bad.memoryView.options.getList(), value => value === error);
});

test('factory failures propagate and stop later construction without a second attempt', () => {
  const f = fixture(); const error = new Error('neutral construction failure');
  f.options.factories.voiceControls.create = () => { f.calls.push('voiceControls'); throw error; };
  assert.throws(() => create(f.options), value => value === error);
  assert.deepEqual(f.calls, names.slice(0, 3));
});

test('real component factories compose without touching DOM, browser services or clocks', () => {
  const f = fixture();
  f.options.factories = Object.fromEntries([
    ['dom', 'dom_helpers'], ['debugOutput', 'debug_output'], ['voiceControls', 'voice_controls'],
    ['traceView', 'trace_view'], ['chatView', 'chat_view'], ['fleetDrawers', 'fleet_drawers'], ['memoryView', 'memory_view'],
  ].map(([key, file]) => [key, require(`../../webapp/${file}.js`)]));
  const first = create(f.options), second = create(f.options);
  for (const key of keys) assert.notEqual(first[key], second[key]);
  assert.equal(typeof first.memoryView.render, 'function');
  assert.throws(() => first.domHelpers.getById('neutral'), /unexpected eager dependency access/);
});

for (const readyState of ['loading', 'complete']) {
  test(`actual composition preserves lexical bindings and lazy adapter behavior with document ${readyState}`, () => {
    const source = fs.readFileSync('webapp/isr.js', 'utf8');
    const start = source.indexOf('// DOM helper'); const end = source.indexOf('// Global state', start);
    assert(start >= 0 && end > start);
    const f = fixture(); let stateReads = 0, currentVoice = null;
    class Clock extends Date {
      constructor(...args) { super(args.length ? args[0] : 123); }
      static now() { return 123; }
      toLocaleTimeString(...args) { return JSON.stringify({ ms: this.getTime(), args }); }
    }
    const context = { ISRFrontendUI: { create },
      document: { get readyState() { stateReads++; return readyState; }, addEventListener: fail },
      window: { get ISRVoice() { return currentVoice; } }, Date: Clock, Math: { random: () => 0.5 },
      fetch: fail, escapeHtml: fail,
    };
    for (const [i, global] of ['ISRDom', 'ISRDebugOutput', 'ISRVoiceControls', 'ISRTraceView',
      'ISRChatView', 'ISRFleetDrawers', 'ISRMemoryView'].entries()) context[global] = f.options.factories[names[i]];
    vm.createContext(context); vm.runInContext(source.slice(start, end), context);
    assert.deepEqual(f.calls, names); assert.equal(stateReads, 0);
    const lexical = vm.runInContext('[domHelpers, debugOutput, voiceControls, traceView, chatView, fleetDrawers, memoryView]', context);
    keys.forEach((key, index) => { assert.equal(lexical[index], f.instances[key]); assert(!Object.hasOwn(context, key)); });
    context.$ = id => `late:${id}`;
    context.escapeHtml = value => `escaped:${value}`;
    context.fetch = (...args) => args;
    currentVoice = { neutral: true };
    assert.equal(f.instances.debugOutput.options.getOutput(), 'late:debug-output');
    assert.equal(f.instances.traceView.options.getContainer(), 'late:agents-trace-content');
    assert.equal(f.instances.chatView.options.getHistory(), 'late:agent-chat-history');
    assert.equal(f.instances.memoryView.options.getList(), 'late:memory-list');
    for (const key of ['traceView', 'chatView', 'memoryView']) assert.equal(f.instances[key].options.escapeHtml('neutral'), 'escaped:neutral');
    assert.equal(f.instances.voiceControls.options.getVoice(), currentVoice);
    assert.deepEqual(f.instances.voiceControls.options.fetchRequest('/neutral', { method: 'GET' }), ['/neutral', { method: 'GET' }]);
    assert.deepEqual(JSON.parse(f.instances.debugOutput.options.formatTime()), { ms: 123, args: [] });
    assert.deepEqual(JSON.parse(f.instances.traceView.options.formatTime(7)), { ms: 7, args: [] });
    assert.deepEqual(JSON.parse(f.instances.chatView.options.formatTime()), { ms: 123, args: [[], { hour: '2-digit', minute: '2-digit' }] });
    assert.equal(f.instances.chatView.options.createMessageId(), 'msg-123-i');
  });
}
