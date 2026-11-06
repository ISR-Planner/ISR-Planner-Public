const assert = require('node:assert/strict');
const test = require('node:test');
const { create } = require('../../webapp/trace_view.js');
const escapeHtml = value => String(value).replace(/[&<>"']/g,
  char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
function fixture() {
  let container = { innerHTML: 'existing' };
  const times = [];
  const view = create({ getContainer: () => container, escapeHtml,
    formatTime: ts => { times.push(ts); return `time-${ts}`; } });
  return { view, times, get container() { return container; }, replace(value) { container = value; } };
}

test('construction is inert and missing/replacement containers are resolved on every render', () => {
  const fail = () => { throw Error('unexpected dependency access'); };
  create({ getContainer: fail, escapeHtml: fail, formatTime: fail });
  const f = fixture(); const old = f.container;
  f.replace(null);
  assert.equal(f.view.render(new Proxy({}, { get: fail })), undefined);
  assert.deepEqual(f.times, []);
  f.replace({ innerHTML: 'replacement' });
  assert.equal(f.view.render([]), undefined);
  assert.match(f.container.innerHTML, /No agent runs yet\. Send a message in the Agents tab\./);
  assert.equal(old.innerHTML, 'existing');
});

test('empty and absent record collections preserve the empty presentation without a clock read', () => {
  const f = fixture();
  for (const value of [undefined, null, []]) {
    f.view.render(value);
    assert.equal(f.container.innerHTML,
      '<div class="trace-empty">No agent runs yet. Send a message in the Agents tab.</div>');
  }
  assert.deepEqual(f.times, []);
});

test('rendering preserves newest-first display, truncation, escaping, event order, and input ownership', () => {
  const f = fixture();
  const older = Object.freeze({ ts: 1, user_message: 'older neutral', trace_events: Object.freeze([]) });
  const newer = Object.freeze({ ts: 2, user_message: '<b>' + 'x'.repeat(51), mission_id: 'neutral-id-extra',
    trace_events: Object.freeze([
      Object.freeze({ t: 'warn<label>', msg: '<em>neutral</em>', data: { note: '<safe>' } }),
      Object.freeze({ type: 'error', message: 'fallback text' }),
      Object.freeze({ t: 'info', msg: 'last', data: 0 }),
    ]) });
  const runs = Object.freeze([older, newer]);
  const before = JSON.stringify(runs);
  assert.equal(f.view.render(runs), undefined);
  const html = f.container.innerHTML;
  assert.deepEqual(f.times, [2, 1]);
  assert(html.indexOf('time-2') < html.indexOf('time-1'));
  assert(html.includes(escapeHtml(newer.user_message.substring(0, 50)) + '...'));
  assert(html.includes('ID: neutral-...'));
  assert(html.includes('trace-warn'));
  assert(html.includes('trace-error'));
  assert(html.includes('trace-info'));
  assert(html.includes('&lt;em&gt;neutral&lt;/em&gt;'));
  assert(html.includes(escapeHtml(JSON.stringify(newer.trace_events[0].data, null, 2))));
  assert.equal((html.match(/trace-data-details/g) || []).length, 1);
  assert(html.indexOf('fallback text') < html.indexOf('>last<'));
  assert(!html.includes('<label>'));
  assert.equal(JSON.stringify(runs), before);
});

test('dependency and malformed-data errors propagate without partial output replacement', () => {
  const f = fixture(); const error = new Error('neutral failure');
  const run = { ts: 1, user_message: 'neutral', trace_events: [] };
  for (const options of [
    { getContainer() { throw error; }, escapeHtml, formatTime: () => 'time' },
    { getContainer: () => f.container, escapeHtml, formatTime() { throw error; } },
    { getContainer: () => f.container, escapeHtml() { throw error; }, formatTime: () => 'time' },
  ]) assert.throws(() => create(options).render([run]), value => value === error);
  assert.equal(f.container.innerHTML, 'existing');
  assert.throws(() => f.view.render([{ ts: 1, user_message: 'neutral', trace_events: null }]), TypeError);
  const circular = {}; circular.self = circular;
  assert.throws(() => f.view.render([{ ...run, trace_events: [{ t: 'info', data: circular }] }]), TypeError);
  assert.equal(f.container.innerHTML, 'existing');
});

test('borrowed trace records refresh only on explicit render without becoming owned state', () => {
  const f = fixture();
  const run = { ts: 1, user_message: 'first neutral', trace_events: [{ t: 'info', msg: 'first event' }] };
  const records = [run], before = JSON.stringify(records);
  f.view.render(records); const firstHTML = f.container.innerHTML;
  assert.equal(JSON.stringify(records), before);
  run.ts = 2; run.user_message = 'second <neutral>'; run.trace_events[0].msg = 'updated <event>';
  assert.equal(f.container.innerHTML, firstHTML);
  assert.deepEqual(f.times, [1]);
  const changed = JSON.stringify(records);
  f.view.render(records);
  assert.match(f.container.innerHTML, /time-2/);
  assert.match(f.container.innerHTML, /second &lt;neutral&gt;/);
  assert.match(f.container.innerHTML, /updated &lt;event&gt;/);
  assert.equal(JSON.stringify(records), changed);
  assert.deepEqual(f.times, [1, 2]);
  f.view.render([]); run.user_message = 'old record changed';
  assert.match(f.container.innerHTML, /No agent runs yet/);
  assert.deepEqual(f.times, [1, 2]);
});
