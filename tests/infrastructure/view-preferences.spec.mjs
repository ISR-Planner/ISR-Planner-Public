import { test, expect } from './shell-fixture.mjs';

for (const filename of ['index.html', 'weather_preview.html']) {
  test(`${filename}: viewport preferences preserve persistence, deduplication and reload`, async ({ shell }) => {
    const { page } = shell;
    await shell.open(filename);
    const result = await page.evaluate(() => {
      localStorage.setItem('neutral-unrelated', 'keep');
      const original = Storage.prototype.setItem;
      const writes = [];
      Storage.prototype.setItem = function (key, value) {
        if (key === VIEW_STORAGE_KEY) writes.push(JSON.parse(value));
        return original.call(this, key, value);
      };
      try {
        Object.assign(viewState, { centerLat: 1.123451, centerLng: 2.654321, zoomLevel: 6.01 });
        saveViewToLocalStorage();
        Object.assign(viewState, { centerLat: 1.123452, centerLng: 2.654322, zoomLevel: 6.02 });
        saveViewToLocalStorage();
        return { writes, read: readSavedView(), unrelated: localStorage.getItem('neutral-unrelated') };
      } finally { Storage.prototype.setItem = original; }
    });
    expect(result.writes).toHaveLength(1);
    expect(result.writes[0]).toEqual({ centerLat: 1.123451, centerLng: 2.654321,
      zoomLevel: 6.01, timestamp: expect.any(Number) });
    expect(result.read).toEqual({ centerLat: 1.123451, centerLng: 2.654321, zoomLevel: 6.01 });
    expect(result.unrelated).toBe('keep');

    expect(await page.evaluate(() => {
      const original = Storage.prototype.setItem; let attempts = 0;
      Storage.prototype.setItem = function (key, value) {
        if (key === VIEW_STORAGE_KEY) { attempts++; throw Error('neutral storage denial'); }
        return original.call(this, key, value);
      };
      try {
        Object.assign(viewState, { centerLat: 3, centerLng: 4, zoomLevel: 6 });
        saveViewToLocalStorage(); saveViewToLocalStorage();
      } finally { Storage.prototype.setItem = original; }
      // Recovery does not retry an unchanged rounded view.
      saveViewToLocalStorage();
      const retained = readSavedView();
      Object.assign(viewState, { centerLat: 4, centerLng: 5, zoomLevel: 6 });
      saveViewToLocalStorage();
      return { attempts, retained, saved: readSavedView() };
    })).toEqual({ attempts: 1,
      retained: { centerLat: 1.123451, centerLng: 2.654321, zoomLevel: 6.01 },
      saved: { centerLat: 4, centerLng: 5, zoomLevel: 6 } });
    await page.reload({ waitUntil: 'load' });
    await expect.poll(() => page.evaluate(() => viewState.zoomLevel)).toBe(6);
    const restored = await page.evaluate(() => ({ lat: viewState.centerLat, lng: viewState.centerLng,
      saved: readSavedView(), unrelated: localStorage.getItem('neutral-unrelated') }));
    expect(restored.lat).toBeCloseTo(4, 7); expect(restored.lng).toBeCloseTo(5, 7);
    expect(restored.saved.centerLat).toBeCloseTo(4, 7);
    expect(restored.saved.centerLng).toBeCloseTo(5, 7);
    expect(restored.saved.zoomLevel).toBe(6);
    expect(restored.unrelated).toBe('keep');
    expect(shell.requests.some(request => /^(POST|DELETE) /.test(request))).toBe(false);
  });
}
