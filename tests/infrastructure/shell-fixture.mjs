// Real local HTML/JS/CSS, but no server, credentials, live data, or external CDN.
// All browser requests are intercepted. Unknown requests fail the test.
import { readFile } from 'node:fs/promises';
import { resolve, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test as base, expect } from '@playwright/test';

export const origin = 'https://shell.test';
const webapp = fileURLToPath(new URL('../../webapp/', import.meta.url));
const unused3dAssets = new Map([
  ['https://cesium.com/downloads/cesiumjs/releases/1.118/Build/Cesium/Widgets/widgets.css', 'text/css'],
  ['https://cesium.com/downloads/cesiumjs/releases/1.118/Build/Cesium/Cesium.js', 'application/javascript'],
]);
const types = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css' };
const blankTile = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1cAAAAASUVORK5CYII=', 'base64');

const providers = [{
  id: 'smoke-provider', name: 'Test service', configured: true, requires_key: true,
  models: [
    { id: 'smoke-a', name: 'Test model A', description: 'Synthetic test option' },
    { id: 'smoke-b', name: 'Test model B', description: 'Synthetic test option' },
  ],
}, {
  id: 'ollama', name: 'Mock local service', configured: true, requires_key: false,
  default_base_url: 'http://localhost:11434', models: [],
}];
const startup = {
  '/api/llm/providers': { providers },
  '/api/llm/ollama/models': { success: true, models: [
    { id: 'local-a', name: 'Local test A' }, { id: 'local-b', name: 'Local test B' },
  ] },
  '/api/environment': {
    success: true, filename: 'empty-ui-fixture.json',
    environment: { airports: [], targets: [], sams: [], tankers: [], polygons: [] },
  },
  '/api/asset_catalog': { platforms: [], sensors: [], target_categories: [] },
  '/api/agent/memory': { success: true, memories: [], count: 0 },
  '/api/gnn/models': { assignment: [], route: [] },
  '/api/tts/engines': {
    engines: [{ id: 'browser', label: 'Browser', configured: true }], default: 'browser',
  },
};

export const test = base.extend({
  shell: async ({ page, context }, use) => {
    const errors = [];
    const unexpected = [];
    const requests = [];
    const connectionCalls = [];
    let connectionResult = { success: true, message: 'Mock service connected' };
    page.on('pageerror', error => errors.push(error.message));
    await context.addInitScript(() => {
      // Avoid geolocation/IP lookup. Seed only absent views so reload tests preserve preferences.
      if (localStorage.getItem('isrLastView') === null) localStorage.setItem('isrLastView', JSON.stringify({
        centerLat: 0, centerLng: 0, zoomLevel: 4, timestamp: Date.now(),
      }));
    });
    await context.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      requests.push(`${request.method()} ${url.pathname}`);
      if (/^https:\/\/unpkg\.com\/leaflet@1\.9\.4\/dist\/leaflet\.(js|css)$/.test(url.href)) {
        const file = url.pathname.endsWith('.js') ? 'leaflet.js' : 'leaflet.css';
        return route.fulfill({ contentType: types[extname(file)],
          body: await readFile(new URL(`../../node_modules/leaflet/dist/${file}`, import.meta.url)) });
      }
      if (unused3dAssets.has(url.href)) {
        // No 3D view is opened; its external engine is outside shell coverage.
        return route.fulfill({ status: 200, contentType: unused3dAssets.get(url.href), body: '' });
      }
      if (/^https:\/\/[abc]\.tile\.openstreetmap\.org\/\d+\/\d+\/\d+\.png$/.test(url.href)) {
        return route.fulfill({ contentType: 'image/png', body: blankTile });
      }
      if (url.origin === origin) {
        if (request.method() === 'GET' && Object.hasOwn(startup, url.pathname)) {
          return route.fulfill({ json: startup[url.pathname] });
        }
        if (request.method() === 'POST' && url.pathname === '/api/llm/test') {
          connectionCalls.push(request.postDataJSON());
          if (connectionResult === 'abort') return route.abort('failed');
          return route.fulfill({ json: connectionResult });
        }
        if (request.method() === 'GET' && url.pathname.startsWith('/static/')) {
          const path = resolve(webapp, decodeURIComponent(url.pathname.slice('/static/'.length)));
          if (path.startsWith(webapp.endsWith(sep) ? webapp : webapp + sep) && types[extname(path)]) {
            try {
              return await route.fulfill({ contentType: types[extname(path)], body: await readFile(path) });
            } catch (error) {
              unexpected.push(`Missing local asset: ${url.pathname}: ${error.message}`);
              return route.abort();
            }
          }
        }
      }
      unexpected.push(`${request.method()} ${url.href}`);
      return route.abort('blockedbyclient');
    });
    await use({
      page, requests, connectionCalls,
      setConnectionResult(value) { connectionResult = value; },
      async open(filename) {
        const response = await page.goto(`${origin}/static/${filename}`, { waitUntil: 'load' });
        expect(response.status()).toBe(200);
        await expect(page.locator('#env-filename')).toHaveText(
          filename === 'weather_preview.html' ? 'Import your mission JSON' : 'empty-ui-fixture.json');
        await expect(page.locator('#llm-provider option')).toHaveCount(2);
        await expect(page.locator('#llm-model option')).toHaveCount(2);
      },
    });
    expect(unexpected, 'All requests must be local assets or explicit mocks').toEqual([]);
    expect(errors, 'No uncaught browser errors').toEqual([]);
  },
});
export { expect };
