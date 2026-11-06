// Supplied memory-list presentation; loading and actions remain with callers.
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ISRMemoryView = factory();
})(globalThis, function () {
  'use strict';

  function create({ getList, escapeHtml }) {
    function render(memories) {
      const listEl = getList();
      if (!listEl) return;

      if (!memories || memories.length === 0) {
        listEl.innerHTML = '<div style="color: #6b7280; font-size: 0.7rem; text-align: center; padding: 0.5rem;">No memories yet. Add instructions the agent should remember.</div>';
        return;
      }

      listEl.innerHTML = memories.map(m => `
    <div class="memory-item" data-id="${m.id}">
      <span class="memory-category">${m.category}</span>
      <span class="memory-content">${escapeHtml(m.content)}</span>
      <button class="btn-delete-memory" onclick="deleteAgentMemory(${m.id})">×</button>
    </div>
  `).join("");
    }

    return { render };
  }

  return { create };
});
