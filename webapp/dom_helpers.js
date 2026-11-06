(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ISRDom = factory();
})(globalThis, function () {
  'use strict';

  function create(document) {
    function getById(id) {
      return document.getElementById(id);
    }
    function setText(id, value) {
      const element = getById(id);
      if (!element) return;
      element.textContent = value == null ? '' : String(value);
    }
    function escapeHtml(text) {
      const div = document.createElement("div");
      div.textContent = text;
      return div.innerHTML;
    }
    return { getById, setText, escapeHtml };
  }

  return { create };
});
