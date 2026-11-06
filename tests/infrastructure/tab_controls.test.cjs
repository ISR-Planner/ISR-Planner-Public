const assert = require('node:assert/strict');
const test = require('node:test');
const { create } = require('../../webapp/tab_controls.js');

function fixture() {
  let changes = 0;
  class Element extends EventTarget {
    constructor(id, active = false) {
      super();
      this.id = id;
      const classes = new Set(active ? ['active', 'existing'] : ['existing']);
      this.classList = {
        add: value => { changes++; classes.add(value); },
        remove: value => { changes++; classes.delete(value); },
        contains: value => classes.has(value),
      };
    }
    getAttribute() { return this.id; }
    click() { this.dispatchEvent(new Event('click')); }
  }
  const buttons = [new Element('one', true), new Element('two')];
  const panes = [new Element('tab-one', true), new Element('tab-two')];
  const document = {
    querySelectorAll: selector => [...(selector === '.tab-btn' ? buttons : panes)],
    getElementById: id => panes.find(pane => pane.id === id) || null,
  };
  return { buttons, panes, document, changes: () => changes };
}

test('construction and mount preserve initial markup; click changes only active classes', () => {
  const f = fixture();
  const tabs = create(f.document);
  f.buttons[1].click();
  assert.equal(f.changes(), 0);
  tabs.mount();
  assert.equal(f.changes(), 0);
  f.buttons[1].click();
  assert.deepEqual(f.buttons.map(item => item.classList.contains('active')), [false, true]);
  assert.deepEqual(f.panes.map(item => item.classList.contains('active')), [false, true]);
  assert([...f.buttons, ...f.panes].every(item => item.classList.contains('existing')));
});

test('repeated mount adds no duplicate listeners; dispose is repeatable and remount works', () => {
  const f = fixture();
  const tabs = create(f.document);
  tabs.mount();
  f.buttons[1].click();
  const oneClick = f.changes();
  tabs.mount();
  f.buttons[0].click();
  assert.equal(f.changes(), oneClick * 2);
  tabs.dispose();
  tabs.dispose();
  f.buttons[1].click();
  assert.equal(f.changes(), oneClick * 2);
  assert(f.panes[0].classList.contains('active'));
  tabs.mount();
  f.buttons[1].click();
  assert.equal(f.changes(), oneClick * 3);
  assert(f.panes[1].classList.contains('active'));
});

test('missing pane preserves existing behavior: clicked button activates, all panes deactivate', () => {
  const f = fixture();
  f.buttons[1].id = 'missing';
  create(f.document).mount();
  f.buttons[1].click();
  assert(f.buttons[1].classList.contains('active'));
  assert(f.panes.every(item => !item.classList.contains('active')));
});

test('empty documents can mount, dispose, and remount', () => {
  const tabs = create({ querySelectorAll: () => [] });
  tabs.dispose();
  tabs.mount();
  tabs.dispose();
  tabs.mount();
  tabs.dispose();
});

test('tab disposal preserves unrelated handlers and remount binds replacements without reactivating old controls', () => {
  const f = fixture(); const old = f.buttons[1]; let foreign = 0;
  old.addEventListener('click', () => foreign++);
  const tabs = create(f.document); tabs.mount(); old.click();
  assert(f.panes[1].classList.contains('active'));
  tabs.dispose(); tabs.dispose(); const changes = f.changes();
  old.click(); assert.equal(foreign, 2); assert.equal(f.changes(), changes);
  const replacement = fixture().buttons[1]; f.buttons[1] = replacement;
  tabs.mount(); tabs.mount(); old.click();
  assert.equal(foreign, 3); assert.equal(f.changes(), changes);
  f.buttons[0].click(); assert(f.panes[0].classList.contains('active'));
  replacement.click(); assert(f.panes[1].classList.contains('active'));
  assert.equal(foreign, 3);
});
