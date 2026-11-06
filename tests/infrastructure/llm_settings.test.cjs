const assert = require('node:assert/strict');
const test = require('node:test');
const { create } = require('../../webapp/llm_settings.js');

function fixture() {
  const nodes = new Map();
  const saved = new Map();
  const requests = [], logs = [];
  let writesSucceed = true, closes = 0, mounts = 0;
  const storage = {
    read: key => saved.get(key) ?? null,
    write(entries) {
      if (!writesSucceed) return false;
      for (const [key, value] of entries) saved.set(key, value);
      return true;
    },
    removeMatching(prefix) {
      for (const key of saved.keys()) if (key.startsWith(prefix)) saved.delete(key);
    },
  };
  const http = { async json(url, options) { requests.push({ url, options }); return { success: true }; } };
  const controller = create({
    document: { getElementById: id => nodes.get(id) ?? null }, storage, http,
    createModal: () => ({ open() {}, close() { closes++; }, mount() { mounts++; }, dispose() {} }),
    appendDebugLine: message => logs.push(message),
  });
  const node = (id, value = '') => {
    const item = { value, textContent: '', innerHTML: '', className: '', style: {} };
    nodes.set(id, item);
    return item;
  };
  return { controller, storage, http, saved, requests, logs, node,
    failWrites() { writesSucceed = false; }, closes: () => closes, mounts: () => mounts };
}

test('construction has no DOM, network, storage, or modal effects and owns independent state', () => {
  const unavailable = new Proxy({}, { get() { throw Error('unexpected dependency access'); } });
  const a = create({ document: unavailable, storage: unavailable, http: unavailable,
    createModal: unavailable, appendDebugLine: unavailable });
  const b = fixture().controller;
  assert.equal(a.modalControl, null);
  assert.equal(a.formListeners, null);
  a.providers.push({ id: 'neutral' });
  assert.deepEqual(b.providers, []);
  assert.equal(a.storage, unavailable);
});

test('init waits for provider loading before mounting and restoring preferences', async () => {
  const f = fixture();
  let resolveProviders;
  f.http.json = () => new Promise(resolve => { resolveProviders = resolve; });
  f.saved.set('isr_llm_key_retired', 'synthetic-never-send');
  f.saved.set('unrelated', 'keep');
  const pending = f.controller.init();
  assert.equal(f.mounts(), 0);
  assert.equal(f.saved.has('isr_llm_key_retired'), true);
  resolveProviders({ providers: [{ id: 'neutral' }] });
  await pending;
  assert.equal(f.mounts(), 1);
  assert.equal(f.saved.has('isr_llm_key_retired'), false);
  assert.equal(f.saved.get('unrelated'), 'keep');
  assert.deepEqual(f.controller.providers, [{ id: 'neutral' }]);
});

test('saved settings serialization is key-free and preserves incomplete/local/hosted results', () => {
  const f = fixture();
  assert.equal(f.controller.getCurrentSettings(), null);
  f.saved.set('isr_llm_provider', 'neutral');
  assert.equal(f.controller.getCurrentSettings(), null);
  f.saved.set('isr_llm_key_neutral', 'synthetic-never-send');
  f.saved.set('isr_llm_model_neutral', 'neutral-model');
  assert.deepEqual(f.controller.getCurrentSettings(), { provider: 'neutral', model: 'neutral-model' });
  f.saved.set('isr_llm_provider', 'ollama');
  assert.equal(f.controller.getCurrentSettings(), null);
  f.saved.set('isr_llm_model_ollama', 'local-model');
  assert.deepEqual(f.controller.getCurrentSettings(), {
    provider: 'ollama', model: 'local-model', base_url: 'http://localhost:11434',
  });
  f.saved.set('isr_ollama_host', 'http://local.invalid');
  assert.equal(f.controller.getCurrentSettings().base_url, 'http://local.invalid');
});

test('failed save leaves current selection and modal intact without success logging', () => {
  const f = fixture();
  f.node('llm-provider', 'neutral'); f.node('llm-model', 'neutral-model');
  const status = f.node('connection-status');
  f.failWrites();
  f.controller.saveSettings();
  assert.equal(f.controller.currentProvider, null);
  assert.equal(f.controller.currentModel, null);
  assert.equal(f.closes(), 0);
  assert.deepEqual(f.logs, []);
  assert.equal(status.className, 'connection-status error');
  assert.equal(status.textContent, 'Settings could not be saved. Browser storage is unavailable.');
});

test('successful local save retains the host, updates the same controller, closes and logs once', () => {
  const f = fixture();
  f.node('llm-provider', 'ollama'); f.node('llm-model', 'local-model');
  f.node('ollama-host', ' http://local.invalid ');
  f.controller.saveSettings();
  assert.equal(f.saved.get('isr_ollama_host'), 'http://local.invalid');
  assert.equal(f.controller.currentProvider, 'ollama');
  assert.equal(f.controller.currentModel, 'local-model');
  assert.equal(f.closes(), 1);
  assert.deepEqual(f.logs, ['AI Settings saved: ollama / local-model']);
});

test('connection validation skips requests for unconfigured hosted or unselected local models', async () => {
  const f = fixture();
  const provider = f.node('llm-provider', 'neutral');
  const model = f.node('llm-model', 'neutral-model');
  const status = f.node('connection-status');
  f.controller.providers = [{ id: 'neutral', requires_key: true, configured: false },
    { id: 'ollama', requires_key: false }];
  await f.controller.testConnection();
  assert.equal(status.textContent, 'Provider is not configured on the server');
  provider.value = 'ollama'; model.value = '';
  await f.controller.testConnection();
  assert.equal(status.textContent, 'Select a model (Refresh models if empty)');
  assert.deepEqual(f.requests, []);
});

test('local discovery restores existing model selection and preserves failure presentation', async () => {
  const f = fixture();
  const model = f.node('llm-model');
  const status = f.node('connection-status');
  f.node('ollama-host', 'http://local.invalid');
  f.saved.set('isr_llm_model_ollama', 'local-model');
  let answer = { success: true, models: [{ id: 'local-model', name: 'Local model' }] };
  f.http.json = async url => { f.requests.push(url); return answer; };
  await f.controller.loadOllamaModels();
  assert.equal(model.value, 'local-model');
  assert.match(model.innerHTML, /Local model/);
  assert.deepEqual(f.requests, ['/api/llm/ollama/models?base_url=http%3A%2F%2Flocal.invalid']);
  answer = { success: false, message: 'Neutral service failure' };
  await f.controller.loadOllamaModels();
  assert.match(model.innerHTML, /No models found/);
  assert.equal(status.textContent, 'Neutral service failure');
  f.http.json = async () => { throw Error('neutral transport failure'); };
  await f.controller.loadOllamaModels();
  assert.match(model.innerHTML, /Could not reach Ollama/);
  assert.equal(status.textContent, 'Could not reach Ollama at http://local.invalid. Is it running?');
});

// Exercise the real composition/startup block without loading application workflows.
for (const readyState of ['loading', 'complete']) {
  test(`application composition preserves ${readyState} document startup`, async () => {
    const fs = require('node:fs');
    const path = require('node:path');
    const vm = require('node:vm');
    const source = fs.readFileSync(path.join(__dirname, '../../webapp/isr.js'), 'utf8');
    const start = source.indexOf('const LLMSettings = ISRLLMSettings.create(');
    const end = source.indexOf('// Agent Comparison UI', start);
    assert(start >= 0 && end > start);
    let requests = 0, storageReads = 0, modalMounts = 0;
    const callbacks = [];
    const context = vm.createContext({
      document: { readyState, getElementById: () => null,
        addEventListener(name, callback) { assert.equal(name, 'DOMContentLoaded'); callbacks.push(callback); } },
      ISRPreferences: { create(getStorage) { return {
        read() { storageReads++; return getStorage().getItem(); }, removeMatching() {},
      }; } },
      ISRHttp: { create(fetchRequest) { return { json: async url => (await fetchRequest(url)).json() }; } },
      ISRModal: { create: () => ({ mount() { modalMounts++; } }) },
      fetch: async () => { requests++; return { json: async () => ({ providers: [] }) }; },
      localStorage: { getItem: () => null },
      appendDebugLine() {},
    });
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../webapp/llm_settings.js'), 'utf8'), context);
    vm.runInContext(source.slice(start, end), context);
    if (readyState === 'loading') {
      assert.equal(requests, 0);
      assert.equal(storageReads, 0);
      assert.equal(modalMounts, 0);
      assert.equal(callbacks.length, 1);
      await callbacks[0]();
    } else {
      assert.equal(callbacks.length, 0);
    }
    // init deliberately isn't awaited by the existing DOM-ready entrypoint.
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(requests, 1);
    assert.equal(modalMounts, 1);
    assert.equal(storageReads, 1);
  });
}

test('missing UI/provider selection remains guarded without requests', async () => {
  const f = fixture();
  await f.controller.testConnection();
  await f.controller.loadOllamaModels();
  const model = f.node('llm-model');
  f.node('llm-provider', 'missing-provider');
  f.controller.onProviderChange();
  assert.equal(model.innerHTML, '<option value="">Select a provider first</option>');
  assert.deepEqual(f.requests, []);
});

test('listener disposal during pending init does not cancel its later mount and preference restoration', async () => {
  const f = fixture(); let resolveProviders;
  f.http.json = () => new Promise(resolve => { resolveProviders = resolve; });
  f.saved.set('isr_llm_key_retired', 'synthetic-never-send');
  const pending = f.controller.init();
  f.controller.disposeEventListeners(); f.controller.disposeEventListeners();
  assert.equal(f.mounts(), 0); assert.equal(f.controller.formListeners, null);
  assert.equal(f.saved.has('isr_llm_key_retired'), true);
  resolveProviders({ providers: [] }); await pending;
  assert.equal(f.mounts(), 1); assert.deepEqual(f.controller.formListeners, []);
  assert.equal(f.saved.has('isr_llm_key_retired'), false);
  f.controller.disposeEventListeners(); assert.equal(f.controller.formListeners, null);
});

test('pending model discovery retains its captured selector after listener disposal and node replacement', async () => {
  const f = fixture(); const old = f.node('llm-model'); let resolveModels;
  f.saved.set('isr_llm_model_ollama', 'neutral-model');
  f.http.json = () => new Promise(resolve => { resolveModels = resolve; });
  const pending = f.controller.loadOllamaModels();
  const replacement = f.node('llm-model'); replacement.innerHTML = 'replacement untouched';
  f.controller.disposeEventListeners();
  resolveModels({ success: true, models: [{ id: 'neutral-model', name: 'Neutral model' }] });
  await pending;
  assert.match(old.innerHTML, /Neutral model/); assert.equal(old.value, 'neutral-model');
  assert.equal(replacement.innerHTML, 'replacement untouched');
  assert.equal(replacement.value, ''); assert.equal(f.controller.formListeners, null);
});
