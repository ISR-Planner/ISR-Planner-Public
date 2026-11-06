const assert = require('node:assert/strict');
const test = require('node:test');
const { create } = require('../../webapp/modal_control.js');

function fixture() {
  const modal = new EventTarget();
  modal.style = { display: 'none' };
  const open = new EventTarget();
  const close = new EventTarget();
  let opens = 0;
  const control = create({
    getModal: () => modal, getOpenButton: () => open, getCloseButton: () => close,
    onOpen: () => { assert.equal(modal.style.display, 'flex'); opens++; },
  });
  return { modal, open, close, control, opens: () => opens };
}
const click = element => element.dispatchEvent(new Event('click'));

test('construction and mount are inert; open, close, and backdrop preserve display behavior', () => {
  const f = fixture();
  click(f.open);
  assert.equal(f.opens(), 0);
  f.control.mount();
  assert.equal(f.modal.style.display, 'none');
  click(f.open);
  assert.equal(f.opens(), 1);
  click(f.close);
  assert.equal(f.modal.style.display, 'none');
  f.control.open();
  click(f.modal);
  assert.equal(f.modal.style.display, 'none');
});

test('mount and dispose are repeatable; cleanup leaves selection intact and remount works', () => {
  const f = fixture();
  f.control.mount();
  f.control.mount();
  click(f.open);
  assert.equal(f.opens(), 1);
  f.control.dispose();
  f.control.dispose();
  click(f.close);
  assert.equal(f.modal.style.display, 'flex');
  click(f.open);
  assert.equal(f.opens(), 1);
  f.control.close();
  f.control.mount();
  click(f.open);
  assert.equal(f.opens(), 2);
  click(f.close);
  assert.equal(f.modal.style.display, 'none');
});

test('missing elements are safe and remount captures replacement elements', () => {
  let modal = null;
  let open = null;
  const control = create({ getModal: () => modal, getOpenButton: () => open, getCloseButton: () => null });
  control.mount();
  control.open();
  control.close();
  control.dispose();
  modal = { style: {}, addEventListener() {}, removeEventListener() {} };
  open = new EventTarget();
  control.mount();
  click(open);
  assert.equal(modal.style.display, 'flex');
  control.dispose();
});

test('modal disposal removes only owned handlers and leaves direct operations and foreign handlers available', () => {
  const f = fixture(); let foreign = 0;
  f.open.addEventListener('click', () => foreign++);
  f.close.addEventListener('click', () => foreign++);
  f.control.mount(); click(f.open);
  assert.equal(foreign, 1); assert.equal(f.opens(), 1);
  f.control.dispose(); f.control.dispose();
  click(f.close); click(f.open);
  assert.equal(foreign, 3); assert.equal(f.opens(), 1);
  assert.equal(f.modal.style.display, 'flex');
  f.control.close(); assert.equal(f.modal.style.display, 'none');
  f.control.mount(); f.control.mount(); click(f.open);
  assert.equal(foreign, 4); assert.equal(f.opens(), 2);
});
