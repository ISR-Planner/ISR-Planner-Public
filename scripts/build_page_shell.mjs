// Static shell generation: no application execution or external dependencies.
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const repository = fileURLToPath(new URL('../', import.meta.url));
const outputs = ['index.html', 'weather_preview.html', '_weather_diagnostics/index.html'];
const slots = ['PAGE_TITLE', 'HEADER_TITLE', 'ENV_EXTENSION', 'EXTENSION_SCRIPTS', 'PAGE_CAPABILITIES'];
const escapeHtml = value => value.replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[char]);

export function renderPages(template, panel, variants) {
  const found = [...template.matchAll(/\{\{([^{}]+)\}\}/g)].map(match => match[1]);
  if (found.length !== slots.length || slots.some(slot => found.filter(value => value === slot).length !== 1)) {
    throw new Error('Template must contain each known slot exactly once.');
  }
  if (!Array.isArray(variants) || variants.length !== outputs.length ||
      outputs.some(output => variants.filter(page => page.output === output).length !== 1)) {
    throw new Error('Manifest must contain each of the three allowed output paths exactly once.');
  }
  return variants.map(page => {
    if (typeof page.title !== 'string' || typeof page.header !== 'string' ||
        !page.capabilities || Object.keys(page.capabilities).sort().join(',') !== 'weatherDiagnostics,weatherPreview' ||
        typeof page.capabilities.weatherPreview !== 'boolean' ||
        typeof page.capabilities.weatherDiagnostics !== 'boolean' ||
        (page.capabilities.weatherDiagnostics && !page.capabilities.weatherPreview) || !Array.isArray(page.scripts) ||
        page.scripts.some(src => typeof src !== 'string' || !/^\/static\/[\w/.-]+\.js\?v=[\w.-]+$/.test(src))) {
      throw new Error(`Invalid variant: ${page.output}`);
    }
    const values = {
      PAGE_TITLE: escapeHtml(page.title), HEADER_TITLE: escapeHtml(page.header),
      ENV_EXTENSION: page.capabilities.weatherPreview ? panel : '',
      PAGE_CAPABILITIES: JSON.stringify(page.capabilities),
      EXTENSION_SCRIPTS: page.scripts.map(src => `<script src="${escapeHtml(src)}"></script>\n`).join(''),
    };
    return { output: page.output, html: template.replace(/\{\{([^{}]+)\}\}/g, (_, slot) => values[slot]) };
  });
}

export async function buildPages({ root = repository, check = false } = {}) {
  const [template, panel, manifest] = await Promise.all([
    readFile(resolve(root, 'frontend/templates/planner.html'), 'utf8'),
    readFile(resolve(root, 'frontend/templates/weather-panel.html'), 'utf8'),
    readFile(resolve(root, 'frontend/page-variants.json'), 'utf8'),
  ]);
  const pages = renderPages(template, panel, JSON.parse(manifest));
  const stale = [];
  for (const { output, html } of pages) {
    const filename = resolve(root, 'webapp', output);
    if (check) {
      let actual;
      try { actual = await readFile(filename, 'utf8'); }
      catch (error) { if (error.code !== 'ENOENT') throw error; }
      if (actual !== html) stale.push(output);
    } else {
      await writeFile(filename, html);
    }
  }
  return stale;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const args = process.argv.slice(2);
    if (args.length > 1 || (args.length === 1 && args[0] !== '--check')) {
      throw new Error('Usage: node scripts/build_page_shell.mjs [--check]');
    }
    const stale = await buildPages({ check: args[0] === '--check' });
    if (stale.length) {
      throw new Error(`Stale generated pages: ${stale.join(', ')}. Run make build-pages.`);
    }
    console.log(args[0] === '--check' ? 'Page shells are current (3/3).' : 'Generated 3 page shells.');
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
