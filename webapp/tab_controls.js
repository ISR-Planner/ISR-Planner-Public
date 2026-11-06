(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ISRTabs = factory();
})(globalThis, function () {
  'use strict';

  // The caller supplies a Document; no application state or startup side effects.
  function create(document) {
    let listeners = null;
    return {
      mount() {
        if (listeners !== null) return;
        const buttons = document.querySelectorAll('.tab-btn');
        const panes = document.querySelectorAll('.tab-pane');
        listeners = [];
        buttons.forEach(button => {
          const onClick = () => {
            const tabId = button.getAttribute('data-tab');
            buttons.forEach(item => item.classList.remove('active'));
            panes.forEach(item => item.classList.remove('active'));
            button.classList.add('active');
            const pane = document.getElementById(`tab-${tabId}`);
            if (pane) pane.classList.add('active');
          };
          button.addEventListener('click', onClick);
          listeners.push([button, onClick]);
        });
      },
      dispose() {
        if (listeners === null) return;
        listeners.forEach(([button, onClick]) => button.removeEventListener('click', onClick));
        listeners = null;
      },
    };
  }

  return { create };
});
