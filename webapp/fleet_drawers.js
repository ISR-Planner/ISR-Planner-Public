// DOM disclosure wiring only; configuration and disabled-row policy stay with callers.
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ISRFleetDrawers = factory();
})(globalThis, function () {
  'use strict';

  function create({ document, getById }) {
    let listeners = null;
    const $ = getById;

    function listen(element, handler) {
      element.addEventListener('click', handler);
      listeners.push([element, handler]);
    }

    function attachFleetDrawers() {
      if (listeners !== null) return;
      listeners = [];
      document.querySelectorAll("[data-drone-toggle]").forEach((btn) => {
        listen(btn, () => setDroneDrawer(btn.dataset.droneToggle, btn.getAttribute("aria-expanded") !== "true"));
      });
      const collapse = $("btn-collapse-drones");
      if (collapse) {
        listen(collapse, () => {
          for (let did = 1; did <= 5; did++) setDroneDrawer(String(did), false);
        });
      }
    }

    function setDroneDrawer(did, open) {
      const drawer = $(`drawer-d${did}`);
      const btn = document.querySelector(`[data-drone-toggle="${did}"]`);
      const row = document.querySelector(`.fleet-row[data-drone-id="${did}"]`);
      if (!drawer || !btn) return;
      drawer.hidden = !open;
      btn.setAttribute("aria-expanded", String(open));
      btn.textContent = open ? "▾" : "▸";
      if (row) row.classList.toggle("open", open);
      const collapse = $("btn-collapse-drones");
      if (collapse) collapse.hidden = !document.querySelector(".fleet-drawer:not([hidden])");
    }

    function dispose() {
      if (listeners === null) return;
      listeners.forEach(([element, handler]) => element.removeEventListener('click', handler));
      listeners = null;
    }

    return { attachFleetDrawers, setDroneDrawer, dispose };
  }

  return { create };
});
