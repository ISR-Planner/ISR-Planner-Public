const assert = require('node:assert/strict');
const test = require('node:test');
const { create } = require('../../webapp/chat_view.js');

function fixture() {
  const calls = [];
  const nodes = new Map();
  const makeHistory = () => ({ children: [], scrollHeight: 700, scrollTop: 0,
    appendChild(node) { this.children.push(node); nodes.set(node.id, node); } });
  let history = makeHistory();
  const document = {
    createElement(tag) {
      calls.push(['element', tag]);
      return { remove() { calls.push(['remove', this.id]); nodes.delete(this.id); } };
    },
    getElementById(id) { calls.push(['lookup', id]); return nodes.get(id); },
  };
  const view = create({ document,
    getHistory() { calls.push(['history']); return history; },
    escapeHtml(text) { calls.push(['escape', text]); return '&lt;neutral&gt;'; },
    formatTime() { calls.push(['time']); return 'neutral time'; },
    createMessageId() { calls.push(['id']); return 'neutral-id'; },
  });
  return { view, calls, get history() { return history; }, replace(value = makeHistory()) { history = value; } };
}

test('chat construction is inert; cards are detached and use the injected clock and escaping', () => {
  const f = fixture();
  assert.deepEqual(f.calls, []);
  const card = f.view.createQABlock('<neutral>');
  assert.deepEqual(f.calls, [['time'], ['element', 'div'], ['escape', '<neutral>']]);
  assert.equal(card.className, 'qa-block');
  assert.match(card.innerHTML, /YOU \[neutral time\]/);
  assert.match(card.innerHTML, /&lt;neutral&gt;/);
  assert.match(card.innerHTML, /AGENT RESPONSE/);
  assert.match(card.innerHTML, /Thinking\.\.\./);
  assert.match(card.innerHTML, /qa-speak-btn.*style="display:none;"/);
  assert.deepEqual(f.history.children, []);
});

test('system messages preserve caller HTML, return IDs, append and scroll; history lookup is lazy', () => {
  const f = fixture(); const old = f.history;
  assert.equal(f.view.appendChatMessage('<strong>neutral</strong>'), 'neutral-id');
  assert.equal(old.children[0].innerHTML, '<strong>neutral</strong>');
  assert.equal(old.children[0].className, 'agent-message system-message');
  assert.equal(old.scrollTop, 700);
  assert.deepEqual(f.calls, [['history'], ['id'], ['element', 'div']]);
  f.replace();
  f.view.appendChatMessage('replacement');
  assert.equal(f.history.children[0].innerHTML, 'replacement');
  assert.equal(old.children.length, 1);
  f.calls.length = 0;
  f.replace(null);
  assert.equal(f.view.appendChatMessage('absent'), null);
  assert.deepEqual(f.calls, [['history']]);
});

test('removal ignores falsy IDs and missing elements and resolves each ID from the document', () => {
  const f = fixture();
  for (const id of [undefined, null, '', 0, false]) assert.equal(f.view.removeChatMessage(id), undefined);
  assert.deepEqual(f.calls, []);
  f.view.removeChatMessage('absent');
  f.view.appendChatMessage('neutral');
  f.view.removeChatMessage('neutral-id');
  f.view.removeChatMessage('neutral-id');
  assert.deepEqual(f.calls.filter(c => c[0] === 'remove'), [['remove', 'neutral-id']]);
  assert.deepEqual(f.calls.filter(c => c[0] === 'lookup'),
    [['lookup', 'absent'], ['lookup', 'neutral-id'], ['lookup', 'neutral-id']]);
});

test('presentation dependency failures propagate without fallback behavior', () => {
  const error = new Error('neutral failure');
  const fail = () => { throw error; };
  const options = { document: { createElement: () => ({}), getElementById: () => null },
    getHistory: () => ({ appendChild() {} }), escapeHtml: text => text,
    formatTime: () => 'time', createMessageId: () => 'id' };
  for (const [key, method, arg] of [
    ['formatTime', 'createQABlock', 'text'], ['escapeHtml', 'createQABlock', 'text'],
    ['getHistory', 'appendChatMessage', 'text'], ['createMessageId', 'appendChatMessage', 'text'],
  ]) assert.throws(() => create({ ...options, [key]: fail })[method](arg), value => value === error);
  assert.throws(() => create({ ...options, document: { createElement: fail } }).createQABlock('text'),
    value => value === error);
  assert.throws(() => create({ ...options, document: { getElementById: fail } }).removeChatMessage('id'),
    value => value === error);
});
