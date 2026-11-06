const assert = require('node:assert/strict');
const test = require('node:test');
const { create } = require('../../webapp/fleet_drawers.js');

function markup() {
  class Element extends EventTarget {
    constructor() {
      super(); this.hidden = true; this.textContent = '▸'; this.attrs = { 'aria-expanded': 'false' };
      const classes = new Set(['existing']);
      this.classList = { toggle(name, on) { if (on) classes.add(name); else classes.delete(name); },
        contains: name => classes.has(name) };
    }
    getAttribute(key) { return this.attrs[key]; }
    setAttribute(key, value) { this.attrs[key] = value; }
    click() { this.dispatchEvent(new Event('click')); }
  }
  const buttons = Array.from({ length: 5 }, (_, i) => {
    const btn = new Element(); btn.dataset = { droneToggle: String(i + 1) }; return btn;
  });
  return { buttons, rows: buttons.map(() => new Element()), drawers: buttons.map(() => new Element()),
    collapse: new Element() };
}
function fixture() {
  let nodes = markup(), reads = 0;
  const document = {
    querySelectorAll(selector) { reads++; assert.equal(selector, '[data-drone-toggle]'); return nodes.buttons; },
    querySelector(selector) {
      reads++;
      if (selector === '.fleet-drawer:not([hidden])') return nodes.drawers.find(node => node && !node.hidden) || null;
      const id = Number(selector.match(/="(\d+)"/)?.[1]) - 1;
      return (selector.startsWith('.fleet-row') ? nodes.rows : nodes.buttons)[id] || null;
    },
  };
  const getById = id => {
    reads++;
    return id === 'btn-collapse-drones' ? nodes.collapse : nodes.drawers[Number(id.replace('drawer-d', '')) - 1] || null;
  };
  const controller = create({ document, getById });
  return { controller, get nodes() { return nodes; }, replace(value = markup()) { nodes = value; }, reads: () => reads };
}

test('construction/mount are inert to presentation; independent drawers keep labels, ARIA and collapse state', () => {
  const f = fixture(); assert.equal(f.reads(), 0);
  f.controller.attachFleetDrawers();
  assert(f.nodes.drawers.every(drawer => drawer.hidden));
  assert(f.nodes.collapse.hidden);
  f.nodes.buttons[0].click(); f.nodes.buttons[1].click();
  for (const i of [0, 1]) {
    assert.equal(f.nodes.drawers[i].hidden, false);
    assert.equal(f.nodes.buttons[i].getAttribute('aria-expanded'), 'true');
    assert.equal(f.nodes.buttons[i].textContent, '▾');
    assert(f.nodes.rows[i].classList.contains('open'));
    assert(f.nodes.rows[i].classList.contains('existing'));
  }
  assert.equal(f.nodes.collapse.hidden, false);
  f.nodes.buttons[0].click();
  assert.equal(f.nodes.buttons[0].textContent, '▸');
  assert.equal(f.nodes.collapse.hidden, false);
  f.nodes.collapse.click();
  assert(f.nodes.drawers.every(drawer => drawer.hidden));
  assert(f.nodes.buttons.every(btn => btn.getAttribute('aria-expanded') === 'false'));
  assert(f.nodes.collapse.hidden);
});

test('repeat mounting avoids duplicate toggles; disposal preserves display and unrelated listeners', () => {
  const f = fixture(); let other = 0;
  f.nodes.buttons[0].addEventListener('click', () => other++);
  f.controller.attachFleetDrawers(); f.controller.attachFleetDrawers();
  f.nodes.buttons[0].click(); assert.equal(f.nodes.drawers[0].hidden, false);
  f.controller.dispose(); f.controller.dispose();
  f.nodes.buttons[0].click(); f.nodes.collapse.click();
  assert.equal(other, 2); assert.equal(f.nodes.drawers[0].hidden, false);
  f.controller.attachFleetDrawers();
  f.nodes.buttons[0].click(); assert.equal(f.nodes.drawers[0].hidden, true);
});

test('dispose and remount capture replacement controls; direct setting resolves current nodes after disposal', () => {
  const f = fixture(); f.controller.attachFleetDrawers(); const old = f.nodes;
  f.controller.dispose(); f.replace();
  f.controller.attachFleetDrawers(); f.nodes.buttons[0].click();
  assert.equal(f.nodes.drawers[0].hidden, false);
  old.buttons[0].click(); old.collapse.click();
  assert.equal(f.nodes.drawers[0].hidden, false);
  assert.equal(old.drawers[0].hidden, true);
  f.controller.dispose(); f.replace();
  f.controller.setDroneDrawer('2', true);
  assert.equal(f.nodes.drawers[1].hidden, false);
  assert.equal(f.nodes.buttons[1].getAttribute('aria-expanded'), 'true');
});

test('absent controls and partial markup are safe and can be remounted after disposal', () => {
  const f = fixture();
  f.replace({ buttons: [], drawers: [], rows: [], collapse: null });
  f.controller.dispose(); f.controller.attachFleetDrawers();
  assert.equal(f.controller.setDroneDrawer('1', true), undefined);
  f.controller.dispose(); f.replace();
  f.nodes.rows[0] = null; f.nodes.collapse = null;
  f.controller.attachFleetDrawers(); f.nodes.buttons[0].click();
  assert.equal(f.nodes.drawers[0].hidden, false);
  f.nodes.drawers[1] = null;
  assert.equal(f.controller.setDroneDrawer('2', true), undefined);
});

test('DOM dependency failures propagate; construction does not resolve the document', () => {
  const error = new Error('neutral failure'); const fail = () => { throw error; };
  const controller = create({ document: { querySelectorAll: fail }, getById: fail });
  assert.throws(() => controller.attachFleetDrawers(), value => value === error);
  assert.throws(() => controller.setDroneDrawer('1', true), value => value === error);
  controller.dispose();
});
