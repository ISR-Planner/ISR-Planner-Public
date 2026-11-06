const assert = require('node:assert/strict');
const test = require('node:test');
const { create } = require('../../webapp/voice_controls.js');

function fixture() {
  const nodes = new Map(), calls = [];
  let textCallback, stateCallback;
  const voice = {
    stt: { supported: true, listening: false, toggle(text, state) {
      calls.push(['toggle']); textCallback = text; stateCallback = state;
      this.listening = !this.listening;
    } },
    tts: {
      isSpeaking: () => false, cancel: () => calls.push(['cancel']),
      speak: text => calls.push(['speak', text]), preload: () => calls.push(['preload']),
      setEngine: id => calls.push(['engine', id]),
    },
  };
  const node = (id) => {
    const element = new EventTarget();
    Object.assign(element, { style: {}, value: '', checked: false, title: '', options: [],
      innerHTML: '', classList: { toggle: (...args) => calls.push(['class', ...args]) },
      focus: () => calls.push(['focus']), appendChild(option) { this.options.push(option); } });
    nodes.set(id, element); return element;
  };
  ['btn-agent-mic', 'agent-input', 'speak-replies-toggle', 'tts-engine'].forEach(node);
  const dependencies = {
    document: { getElementById: id => nodes.get(id) ?? null, createElement: () => ({}) },
    getById: id => nodes.get(id) ?? null, getVoice: () => voice,
    fetchRequest: async url => { calls.push(['fetch', url]); return { json: async () => ({
      engines: [{ id: 'browser', label: 'Browser', configured: true },
        { id: 'cloud', label: 'Cloud', configured: false }], default: 'browser',
    }) }; },
  };
  const control = create(dependencies);
  return { control, dependencies, voice, nodes, calls, node,
    text: (...args) => textCallback(...args), state: (...args) => stateCallback(...args) };
}
const settle = () => new Promise(resolve => setImmediate(resolve));
const event = (node, type) => node.dispatchEvent(new Event(type));

test('construction is inert and missing voice safely disables or skips controls', () => {
  const fail = () => { throw Error('unexpected dependency call'); };
  create({ document: new Proxy({}, { get: fail }), getById: fail, getVoice: fail, fetchRequest: fail });
  const f = fixture(); f.dependencies.getVoice = () => null;
  // Dependencies are captured at construction, so instantiate with the absent engine.
  const control = create(f.dependencies);
  control.attachVoiceHandlers();
  assert.equal(f.nodes.get('btn-agent-mic').style.display, 'none');
  assert.match(f.nodes.get('btn-agent-mic').title, /Voice input not supported/);
  assert.deepEqual(f.calls, []);
});

test('dictation remains additive, cancels narration first, updates state and focuses final text', async () => {
  const f = fixture(); f.nodes.get('agent-input').value = ' existing ';
  f.control.attachVoiceHandlers(); await settle(); f.calls.length = 0;
  event(f.nodes.get('btn-agent-mic'), 'click');
  assert.deepEqual(f.calls, [['cancel'], ['toggle']]);
  f.text('interim', false);
  assert.equal(f.nodes.get('agent-input').value, 'existing interim');
  f.state('listening');
  assert.equal(f.nodes.get('btn-agent-mic').title, 'Listening… click to stop');
  f.text('final', true);
  assert.equal(f.nodes.get('agent-input').value, 'existing final');
  assert.deepEqual(f.calls.at(-1), ['focus']);
  f.state('idle');
  assert.equal(f.nodes.get('btn-agent-mic').title, 'Dictate with your voice');
  event(f.nodes.get('btn-agent-mic'), 'click'); // stop: preserve dictation base
  f.voice.stt.listening = false;
  event(f.nodes.get('btn-agent-mic'), 'click'); // next session snapshots current text
  f.text('next', true);
  assert.equal(f.nodes.get('agent-input').value, 'existing final next');
});

test('speak toggle preloads when enabled and cancels when disabled', async () => {
  const f = fixture(); f.control.attachVoiceHandlers(); await settle(); f.calls.length = 0;
  const toggle = f.nodes.get('speak-replies-toggle');
  toggle.checked = true; event(toggle, 'change');
  toggle.checked = false; event(toggle, 'change');
  assert.deepEqual(f.calls, [['preload'], ['cancel']]);
});

test('engine options preserve defaults, disabled labels, change order and repeatable mounting', async () => {
  const f = fixture(); f.control.attachVoiceHandlers(); f.control.attachVoiceHandlers();
  f.control.attachTtsEngineSelector(); await settle();
  assert.deepEqual(f.calls.filter(call => call[0] === 'fetch'), [['fetch', '/api/tts/engines']]);
  const select = f.nodes.get('tts-engine');
  assert.equal(select.value, 'browser');
  assert.deepEqual(select.options, [
    { value: 'browser', textContent: 'Browser', disabled: false },
    { value: 'cloud', textContent: 'Cloud — not configured', disabled: true },
  ]);
  f.calls.length = 0; select.value = 'browser'; event(select, 'change');
  assert.deepEqual(f.calls, [['engine', 'browser'], ['preload']]);
});

test('engine fetch failure keeps the static option and existing Kokoro fallback', async () => {
  const f = fixture(); f.dependencies.fetchRequest = async () => { throw Error('neutral failure'); };
  const control = create(f.dependencies);
  f.nodes.get('tts-engine').innerHTML = 'static option';
  control.attachTtsEngineSelector(); await settle();
  assert.equal(f.nodes.get('tts-engine').innerHTML, 'static option');
  assert.deepEqual(f.calls, [['engine', 'kokoro']]);
});

test('disposal detaches owned controls, leaves engines running and supports replacement remount', async () => {
  const f = fixture(); f.control.attachVoiceHandlers(); await settle(); f.calls.length = 0;
  f.control.dispose(); f.control.dispose();
  assert.deepEqual(f.calls, []);
  const old = f.nodes.get('btn-agent-mic'); event(old, 'click');
  event(f.nodes.get('tts-engine'), 'change');
  event(f.nodes.get('speak-replies-toggle'), 'change');
  assert.deepEqual(f.calls, []);
  const replacement = f.node('btn-agent-mic');
  f.control.attachVoiceHandlers(); await settle(); f.calls.length = 0;
  event(old, 'click'); assert.deepEqual(f.calls, []);
  event(replacement, 'click'); assert.deepEqual(f.calls, [['cancel'], ['toggle']]);
});

test('reply wiring preserves autoplay/replay/cancel and cleanup respects handler ownership', () => {
  const f = fixture(); const button = { style: {} };
  const block = { querySelector: selector => { assert.equal(selector, '.qa-speak-btn'); return button; } };
  f.nodes.get('speak-replies-toggle').checked = true;
  assert.equal(f.control.wireReplyVoice(block, 'neutral reply'), undefined);
  assert.equal(button.style.display, '');
  assert.deepEqual(f.calls, [['speak', 'neutral reply']]);
  button.onclick();
  f.voice.tts.isSpeaking = () => true; button.onclick();
  assert.deepEqual(f.calls.slice(1), [['speak', 'neutral reply'], ['cancel']]);
  f.control.disposeReplyVoice(block); f.control.disposeReplyVoice(block);
  assert.equal(button.onclick, null);
  f.control.wireReplyVoice(block, 'other reply');
  const otherHandler = () => {};
  button.onclick = otherHandler;
  f.control.disposeReplyVoice(block);
  assert.equal(button.onclick, otherHandler);
});

test('empty reply/missing engine skip element lookup; pending selector work is not cancelled by disposal', async () => {
  const f = fixture();
  f.control.wireReplyVoice({ querySelector() { throw Error('unexpected lookup'); } }, '');
  create({ ...f.dependencies, getVoice: () => null }).wireReplyVoice(
    { querySelector() { throw Error('unexpected lookup'); } }, 'neutral reply');
  let resolve;
  f.dependencies.fetchRequest = () => new Promise(done => { resolve = done; });
  const control = create(f.dependencies);
  control.attachTtsEngineSelector(); control.dispose();
  resolve({ json: async () => ({ engines: [{ id: 'browser', label: 'Browser', configured: true }] }) });
  await settle();
  assert.deepEqual(f.calls, [['engine', 'kokoro']]); // Missing server default retains the existing fallback.
});

test('control disposal preserves foreign listeners, active STT callbacks and separately owned reply handlers', async () => {
  const f = fixture(); let foreign = 0;
  const mic = f.nodes.get('btn-agent-mic'), input = f.nodes.get('agent-input');
  mic.addEventListener('click', () => foreign++);
  input.value = 'base';
  f.control.attachVoiceHandlers(); await settle();
  event(mic, 'click'); assert.equal(foreign, 1);
  const button = { style: {} }; const block = { querySelector: () => button };
  f.control.wireReplyVoice(block, 'neutral reply'); const replyHandler = button.onclick;
  f.calls.length = 0;
  f.control.dispose(); f.control.dispose(); event(mic, 'click');
  assert.equal(foreign, 2); assert.deepEqual(f.calls, []);
  f.text('late dictation', true); f.state('idle');
  assert.equal(input.value, 'base late dictation');
  assert.deepEqual(f.calls, [['focus'], ['class', 'listening', false]]);
  assert.equal(mic.title, 'Dictate with your voice');
  assert.equal(button.onclick, replyHandler);
  button.onclick(); assert.deepEqual(f.calls.at(-1), ['speak', 'neutral reply']);
  f.control.disposeReplyVoice(block); assert.equal(button.onclick, null);
});
