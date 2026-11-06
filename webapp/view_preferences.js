// Last viewport preference only; map state and normalization remain with callers.
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ISRViewPreferences = factory();
})(globalThis, function () {
  'use strict';

  function create({ getStorage, now, storageKey, ttlMs, defaultZoom, normalizeView }) {
    // Debounce key — saveViewToLocalStorage() is called from the draw loop,
    // so this skips the write when nothing changed.
    let lastSavedView = '';
    function save(view) {
      const key = `${view.centerLat.toFixed(5)},${view.centerLng.toFixed(5)},${view.zoomLevel.toFixed(1)}`;
      if (key === lastSavedView) return;
      lastSavedView = key;
      try {
        getStorage().setItem(storageKey, JSON.stringify({
          centerLat: view.centerLat,
          centerLng: view.centerLng,
          zoomLevel: view.zoomLevel,
          timestamp: now(),
        }));
      } catch (_e) {
        // localStorage unavailable (private browsing, etc.) — ignore.
      }
    }

    // Read a saved view from localStorage, or null if absent/stale/malformed.
    function read() {
      try {
        const raw = getStorage().getItem(storageKey);
        if (!raw) return null;
        const v = JSON.parse(raw);
        if (typeof v?.centerLat !== 'number' || typeof v?.centerLng !== 'number') return null;
        if (!v.timestamp || (now() - v.timestamp) > ttlMs) return null;
        return normalizeView({ centerLat: v.centerLat, centerLng: v.centerLng, zoomLevel: v.zoomLevel ?? defaultZoom });
      } catch (_e) {
        return null;
      }
    }

    return { save, read };
  }

  return { create };
});
