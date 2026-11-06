// String-preference storage with explicit failure results. No DOM, network,
// application state, or event listeners. Storage is resolved lazily so denied
// browser storage access cannot break script initialization.
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ISRPreferences = factory();
})(globalThis, function () {
  'use strict';

  function create(getStorage) {
    return {
      read(key, fallback = null) {
        try {
          const value = getStorage().getItem(key);
          return typeof value === 'string' ? value : fallback;
        } catch (_) {
          return fallback;
        }
      },

      // Existing separate keys are preserved. Rollback after a failed write is
      // best effort: localStorage offers no transactions and can become denied.
      write(entries) {
        if (!Array.isArray(entries) || entries.some(entry =>
          !Array.isArray(entry) || entry.length !== 2 ||
          typeof entry[0] !== 'string' || !entry[0] || typeof entry[1] !== 'string')) {
          return false;
        }
        let storage;
        const previous = new Map();
        try {
          storage = getStorage();
          for (const [key] of entries) {
            const value = storage.getItem(key);
            if (value !== null && typeof value !== 'string') return false;
            previous.set(key, value);
          }
        } catch (_) {
          return false;
        }
        try {
          for (const [key, value] of entries) storage.setItem(key, value);
          return true;
        } catch (_) {
          for (const [key, value] of previous) {
            try {
              if (value === null) storage.removeItem(key);
              else storage.setItem(key, value);
            } catch (_) { /* Storage can remain unavailable during rollback. */ }
          }
          return false;
        }
      },

      removeMatching(prefix) {
        if (typeof prefix !== 'string' || !prefix) return false;
        try {
          const storage = getStorage();
          const keys = [];
          for (let i = 0; i < storage.length; i++) {
            const key = storage.key(i);
            if (typeof key === 'string' && key.startsWith(prefix)) keys.push(key);
          }
          for (const key of keys) storage.removeItem(key);
          return true;
        } catch (_) {
          return false;
        }
      },
    };
  }

  return { create };
});
