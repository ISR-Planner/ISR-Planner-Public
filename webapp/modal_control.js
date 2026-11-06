(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ISRModal = factory();
})(globalThis, function () {
  'use strict';

  function create({ getModal, getOpenButton, getCloseButton, onOpen = () => {} }) {
    let listeners = null;
    const control = {
      open() {
        const modal = getModal();
        if (modal) {
          modal.style.display = 'flex';
          onOpen();
        }
      },
      close() {
        const modal = getModal();
        if (modal) modal.style.display = 'none';
      },
      mount() {
        if (listeners !== null) return;
        listeners = [];
        const listen = (element, handler) => {
          if (!element) return;
          element.addEventListener('click', handler);
          listeners.push([element, handler]);
        };
        listen(getOpenButton(), () => control.open());
        listen(getCloseButton(), () => control.close());
        const modal = getModal();
        listen(modal, event => {
          if (event.target === modal) control.close();
        });
      },
      dispose() {
        if (listeners === null) return;
        listeners.forEach(([element, handler]) => element.removeEventListener('click', handler));
        listeners = null;
      },
    };
    return control;
  }

  return { create };
});
