const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const root = path.resolve(__dirname, '../..');
const generator = import('../../scripts/build_page_shell.mjs');

async function inputs() {
  return Promise.all([
    fs.readFile(path.join(root, 'frontend/templates/planner.html'), 'utf8'),
    fs.readFile(path.join(root, 'frontend/templates/weather-panel.html'), 'utf8'),
    fs.readFile(path.join(root, 'frontend/page-variants.json'), 'utf8').then(JSON.parse),
  ]);
}

test('committed page outputs match their template inputs', async () => {
  const { buildPages } = await generator;
  assert.deepEqual(await buildPages({ check: true }), []);
});

test('generation is deterministic and retains variant IDs and script ordering', async () => {
  const { renderPages } = await generator;
  const args = await inputs();
  const pages = renderPages(...args);
  assert.deepEqual(renderPages(...args), pages);
  const scripts = pages.map(page => [...page.html.matchAll(/<script src="([^"]+)"/g)].map(match => match[1]).filter(src => !src.includes('/page_start.js')));
  assert.deepEqual(scripts[1].slice(0, scripts[0].length), scripts[0]);
  assert.deepEqual(scripts[2].slice(0, scripts[1].length), scripts[1]);
  assert.equal(scripts[1].length, scripts[0].length + 2);
  assert.match(scripts[1].at(-2), /weather_checkpoints\.js/);
  assert.match(scripts[1].at(-1), /weather_preview\.js/);
  assert.match(scripts[2].at(-1), /_weather_diagnostics\/runner\.js/);
  for (const [index, page] of pages.entries()) {
    const declaration = page.html.match(/<script id="page-capabilities" type="application\/json">([^<]+)<\/script>/);
    assert.deepEqual(JSON.parse(declaration[1]), { weatherPreview: index !== 0, weatherDiagnostics: index === 2 });
    assert.match(page.html, /page_start\.js[^<]+<\/script>\s*<\/body>/);
    const ids = [...page.html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
    assert.equal(ids.length, new Set(ids).size);
    assert.equal(ids.includes('weather-preview-box'), index !== 0);
    assert.equal(ids.includes('settings-modal'), true);
  }
  assert.match(pages[2].html, /<title>ISR Weather Speed Diagnostic<\/title>/);
});

test('missing, duplicate, or unknown template slots are rejected', async () => {
  const { renderPages } = await generator;
  const [template, panel, variants] = await inputs();
  for (const bad of [template.replace('{{PAGE_TITLE}}', ''), template + '{{PAGE_TITLE}}', template + '{{UNKNOWN}}']) {
    assert.throws(() => renderPages(bad, panel, variants), /slot/);
  }
});

test('manifest rejects invalid destinations and script URLs; titles are escaped', async () => {
  const { renderPages } = await generator;
  const [template, panel, variants] = await inputs();
  const copy = () => structuredClone(variants);
  let bad = copy(); bad[0].output = '../outside.html';
  assert.throws(() => renderPages(template, panel, bad), /output paths/);
  bad = copy(); bad[0].scripts = ['https://example.test/external.js'];
  assert.throws(() => renderPages(template, panel, bad), /Invalid variant/);
  bad = copy(); delete bad[0].capabilities;
  assert.throws(() => renderPages(template, panel, bad), /Invalid variant/);
  bad = copy(); bad[0].capabilities.weatherDiagnostics = true;
  assert.throws(() => renderPages(template, panel, bad), /Invalid variant/);
  const custom = copy(); custom[0].title = '<title & "text">';
  assert.match(renderPages(template, panel, custom)[0].html, /&lt;title &amp; &quot;text&quot;&gt;/);
});

test('freshness check detects changed and missing pages without writing; build repairs them', async t => {
  const { buildPages } = await generator;
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'page-shell-'));
  t.after(() => fs.rm(temp, { recursive: true, force: true }));
  await fs.cp(path.join(root, 'frontend'), path.join(temp, 'frontend'), { recursive: true });
  await fs.mkdir(path.join(temp, 'webapp/_weather_diagnostics'), { recursive: true });
  await fs.writeFile(path.join(temp, 'webapp/index.html'), 'stale');
  assert.equal((await buildPages({ root: temp, check: true })).length, 3);
  assert.equal(await fs.readFile(path.join(temp, 'webapp/index.html'), 'utf8'), 'stale');
  await assert.rejects(fs.stat(path.join(temp, 'webapp/weather_preview.html')), { code: 'ENOENT' });
  await buildPages({ root: temp });
  assert.deepEqual(await buildPages({ root: temp, check: true }), []);
});
