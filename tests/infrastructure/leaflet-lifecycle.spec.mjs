import { test, expect } from './shell-fixture.mjs';

for (const filename of ['index.html', 'weather_preview.html']) {
  test(`${filename}: Leaflet delayed resize belongs to the current map`, async ({ shell }) => {
    const { page } = shell;
    await shell.open(filename);
    await page.clock.install({ time: new Date('2026-10-24T12:00:00Z') });
    await page.clock.pauseAt(new Date('2026-10-24T12:00:01Z'));
    await page.evaluate(() => {
      destroyLeafletMap();
      window.resizeCalls = [];
      window.watchCurrentMap = label => {
        const map = mapState.leafletMap;
        const original = map.invalidateSize;
        map.invalidateSize = function (...args) {
          resizeCalls.push(label);
          return original.apply(this, args);
        };
      };
      initLeafletMap();
      watchCurrentMap('destroyed');
      destroyLeafletMap();
      destroyLeafletMap();
    });
    await page.clock.runFor(250);
    expect(await page.evaluate(() => resizeCalls)).toEqual([]);
    expect(await page.locator('#leaflet-map').count()).toBe(0);

    await page.evaluate(() => {
      initLeafletMap();
      watchCurrentMap('replaced');
      destroyLeafletMap();
      initLeafletMap();
      watchCurrentMap('current');
      initLeafletMap(); // Repeated initialization must not schedule another resize.
    });
    await page.clock.runFor(199);
    expect(await page.evaluate(() => resizeCalls)).toEqual([]);
    await page.clock.runFor(1);
    expect(await page.evaluate(() => resizeCalls)).toEqual(['current']);
    await page.clock.runFor(250);
    expect(await page.evaluate(() => resizeCalls)).toEqual(['current']);
    await page.evaluate(() => { destroyLeafletMap(); destroyLeafletMap(); });
    expect(await page.locator('#leaflet-map').count()).toBe(0);
  });
}
