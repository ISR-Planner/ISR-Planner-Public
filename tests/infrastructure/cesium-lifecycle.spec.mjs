import { readFile } from 'node:fs/promises';
import { createCesiumFixture } from './cesium-fixture.cjs';
import { test, expect } from './shell-fixture.mjs';

for (const filename of ['index.html', 'weather_preview.html']) {
  test(`${filename}: 3D lifecycle orders overlays and supports cleanup/reopening`, async ({ shell }) => {
    const { page } = shell;
    await page.addInitScript({ content: `window.neutral3d = (${createCesiumFixture.toString()})(); window.Cesium = window.neutral3d.engine;` });
    await page.route('**/api/client_config', route => route.fulfill({ json: {} }));
    // Record public method identities immediately before page extensions initialize.
    await page.route('**/page_start.js*', async route => route.fulfill({
      contentType: 'application/javascript',
      body: 'window.originalViewMethods = {init: cesiumView.init, render: cesiumView.render};\n' +
        await readFile(new URL('../../webapp/page_start.js', import.meta.url), 'utf8'),
    }));
    await shell.open(filename);
    expect(await page.evaluate(() => originalViewMethods.init === cesiumView.init &&
      originalViewMethods.render === cesiumView.render)).toBe(true);
    await page.evaluate(() => {
      window.neutralCalls = [];
      window.removeNeutralOverlay = cesiumView.subscribeLifecycle({
        onInit(viewer) { neutralCalls.push(`init:${viewer.id}`); },
        onRender(viewer) {
          if (!viewer) return;
          neutralCalls.push(`render:${viewer.id}`);
          neutral3d.events.push(`overlay:${viewer.id}`);
          viewer.entities.add({ id: 'neutral-overlay' });
        },
      });
    });
    await page.locator('#btn-view-3d').click();
    await expect(page.locator('#cesium-container')).toBeVisible();
    await expect.poll(() => page.evaluate(() => neutralCalls.slice(0, 2))).toEqual(['init:0', 'render:0']);
    // Both camera branches clear the previous scene before notifying overlays.
    const rendered = await page.evaluate(() => {
      neutral3d.events.length = 0;
      const before = neutralCalls.length;
      const results = [cesiumView.render({ keepCamera: true }), cesiumView.render({ keepCamera: false })];
      return { calls: neutralCalls.length - before, events: neutral3d.events.slice(),
        undefinedReturns: results.every(value => value === undefined),
        entities: neutral3d.viewers[0].entities.values.map(value => value.id) };
    });
    expect(rendered.calls).toBe(2);
    expect(rendered.undefinedReturns).toBe(true);
    expect(rendered.entities).toEqual(['neutral-overlay']);
    expect(rendered.events).toEqual(filename === 'weather_preview.html'
      ? ['remove:0', 'guard:0', 'overlay:0', 'clear:0', 'guard:0', 'overlay:0']
      : ['remove:0', 'overlay:0', 'clear:0', 'overlay:0']);
    await page.locator('#btn-view-grid').click();
    expect(await page.evaluate(() => neutral3d.viewers[0].destroyed)).toBe(true);
    await page.locator('#btn-view-3d').click();
    await expect.poll(() => page.evaluate(() => neutralCalls.includes('render:1'))).toBe(true);
    expect(await page.evaluate(() => ({ count: neutral3d.viewers.length,
      guardsNewViewer: neutral3d.events.includes('guard:1') }))).toEqual({
      count: 2, guardsNewViewer: filename === 'weather_preview.html',
    });
    expect(await page.evaluate(() => {
      removeNeutralOverlay(); removeNeutralOverlay();
      const before = neutralCalls.length;
      cesiumView.render({ keepCamera: true });
      return { notified: neutralCalls.length - before,
        entities: neutral3d.viewers[1].entities.values.length };
    })).toEqual({ notified: 0, entities: 0 });
    await page.locator('#btn-view-grid').click();
  });
}
