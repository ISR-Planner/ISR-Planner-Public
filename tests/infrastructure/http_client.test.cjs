const assert = require('node:assert/strict');
const test = require('node:test');
const { create } = require('../../webapp/http_client.js');

test('construction performs no request; JSON requests preserve URL and options', async () => {
  const calls = [];
  const result = { success: true };
  const http = create(async (...args) => {
    calls.push(args);
    return { json: async () => result };
  });
  assert.equal(calls.length, 0);
  const options = {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: '{"example":true}', signal: new AbortController().signal,
  };
  assert.equal(await http.json('/example?host=a%3Ab', options), result);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], '/example?host=a%3Ab');
  assert.equal(calls[0][1], options);
});

test('GET leaves options unspecified and decodes HTTP error bodies', async () => {
  const payload = { success: false, message: 'Service unavailable' };
  const http = create(async (url, options) => {
    assert.equal(url, '/example');
    assert.equal(options, undefined);
    return { ok: false, status: 503, json: async () => payload };
  });
  assert.equal(await http.json('/example'), payload);
});

for (const failure of ['network', 'JSON decoding']) {
  test(`${failure} failures propagate unchanged without retry`, async () => {
    const error = failure === 'network' ? new TypeError('Offline') : new SyntaxError('Invalid JSON');
    let calls = 0;
    const http = create(async () => {
      calls++;
      if (failure === 'network') throw error;
      return { json: async () => { throw error; } };
    });
    await assert.rejects(http.json('/example', { method: 'POST' }), actual => actual === error);
    assert.equal(calls, 1);
  });
}

test('caller cancellation reaches the request and is not retried', async () => {
  const controller = new AbortController();
  let calls = 0;
  const http = create((url, { signal }) => {
    calls++;
    return new Promise((resolve, reject) => {
      signal.addEventListener('abort', () => reject(signal.reason), { once: true });
    });
  });
  const pending = http.json('/example', { signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, error => error === controller.signal.reason);
  assert.equal(calls, 1);
});
