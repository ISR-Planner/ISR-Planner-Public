// Exercise the shipped Agent Send handler and settings serialization together.
// node tests/e2e/test_agent_provider_request.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('webapp/isr.js', 'utf8');
const sendStart = source.indexOf('async function sendAgentMessage() {');
const sendEnd = source.indexOf('\n/**\n * AGENT-DRIVEN CONTINUE', sendStart);
const settingsStart = source.indexOf('const LLMSettings = ISRLLMSettings.create(');
const settingsEnd = source.indexOf('// Initialize LLM Settings when DOM is ready', settingsStart);
assert(sendStart >= 0 && sendEnd > sendStart && settingsStart >= 0 && settingsEnd > settingsStart);

function harness(checkpoint = false) {
  const storage = new Map();
  const requests = [], messages = [];
  const responseArea = {classList: {add() {}, remove() {}}, innerHTML: ''};
  const nodes = {
    'agent-input': {value: 'Explain this route'},
    'agent-chat-history': {appendChild() {}, scrollTop: 0, scrollHeight: 1},
    'btn-send-agent': {disabled: false},
  };
  let cuts = 0, clearedTimeouts = 0;
  const solveReq = {
    isCheckpointReplan: checkpoint,
    payload: {env: {targets: [{id: 'T1'}]}, drone_configs: {'1': {enabled: true}},
      visited_targets: checkpoint ? ['T0'] : [], repair_routes: null,
      time_sync: {enabled: false}},
  };
  const context = {
    state: {env: solveReq.payload.env, sequences: {}, missionId: 'example-mission',
      distanceMatrix: {labels: ['A1', 'T1'], matrix: [[0, 1], [1, 0]]}},
    missionState: {revision: 7},
    window: {},
    document: {},
    localStorage: {getItem: key => storage.get(key) ?? null},
    $: key => nodes[key] || null,
    appendDebugLine: message => messages.push(message),
    getUiPermissions: () => ({canSolve: true, canAgentContinue: checkpoint}),
    createQABlock: () => ({querySelector: () => responseArea}),
    buildSolveRequest: () => solveReq,
    currentRoutesForAgent: () => ({'1': ['A1', 'T1', 'A2']}),
    prepareAgentContinueCut: async () => { cuts++; },
    escapeHtml: text => text,
    wireReplyVoice() {},
    AbortController,
    setTimeout: () => 1,
    clearTimeout: () => { clearedTimeouts++; },
    fetch: async (url, options) => {
      requests.push({url, method: options.method, payload: JSON.parse(options.body)});
      return {json: async () => ({response: 'Route explanation.', routes_changed: false, success: true})};
    },
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync('webapp/preferences.js', 'utf8'), context);
  vm.runInContext(fs.readFileSync('webapp/http_client.js', 'utf8'), context);
  vm.runInContext(fs.readFileSync('webapp/modal_control.js', 'utf8'), context);
  vm.runInContext(fs.readFileSync('webapp/llm_settings.js', 'utf8'), context);
  vm.runInContext(source.slice(settingsStart, settingsEnd) + '\n' + source.slice(sendStart, sendEnd), context);
  return {context, storage, requests, messages, nodes, responseArea, solveReq,
    counters: () => ({cuts, clearedTimeouts})};
}

async function main() {
  for (const checkpoint of [false, true]) {
    const h = harness(checkpoint);
    // These legacy browser entries must never reintroduce client credentials
    // into a request: getCurrentSettings only serializes routing preferences.
    h.storage.set('isr_llm_key_openai', 'example-secret-not-to-send');
    h.storage.set('isr_llm_key_google', 'example-secret-not-to-send');
    for (const settings of [
      {provider: 'openai', model: 'gpt-4o'},
      {provider: 'google', model: 'gemini-2.0-flash'},
      {provider: 'ollama', model: 'local-example', base_url: 'http://localhost:11434'},
      null,
    ]) {
      h.storage.delete('isr_llm_provider');
      if (settings) {
        h.storage.set('isr_llm_provider', settings.provider);
        h.storage.set(`isr_llm_model_${settings.provider}`, settings.model);
        if (settings.base_url) h.storage.set('isr_ollama_host', settings.base_url);
      }
      h.nodes['agent-input'].value = 'Explain this route';
      await h.context.sendAgentMessage();
      const request = h.requests.at(-1);
      assert.equal(request.url, '/api/agents/reason');
      assert.equal(request.method, 'POST');
      assert.deepEqual(request.payload.llm_config, settings,
        'Agent Send must carry the currently saved selection, including after changing providers');
      assert.equal(JSON.stringify(request.payload).includes('example-secret-not-to-send'), false);
      assert.equal(request.payload.is_continue_from_cut, checkpoint);
      assert.deepEqual(request.payload.env, h.solveReq.payload.env);
      assert.deepEqual(request.payload.drone_configs, h.solveReq.payload.drone_configs);
      assert.deepEqual(request.payload.visited_targets, h.solveReq.payload.visited_targets);
      assert.equal(request.payload.mission_id, 'example-mission');
      assert.equal(request.payload.distance_matrix === null, checkpoint);
      const label = settings ? `${settings.provider} / ${settings.model}` : 'server default';
      assert.equal(h.messages.filter(message => message.includes('Using Reasoning Agent')).at(-1),
        `🧠 Using Reasoning Agent (${label})...`);
      assert.equal(h.nodes['btn-send-agent'].disabled, false);
      assert.equal(h.responseArea.innerHTML, 'Route explanation.');
    }
    assert.equal(h.requests.length, 4);
    assert.deepEqual(h.counters(), {cuts: checkpoint ? 4 : 0, clearedTimeouts: 4});
  }
  console.log('PASS: Agent Send forwards selected provider/model and keyless host, updates its label, preserves fresh/checkpoint requests, and sends no browser credentials');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
