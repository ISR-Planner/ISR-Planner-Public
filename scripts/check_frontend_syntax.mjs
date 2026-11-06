// Parse tracked frontend JavaScript without executing it or loading dependencies.
// Requires Node 22+. Inline HTML scripts and browser behavior are not checked.
import { execFileSync, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const files = execFileSync('git', ['ls-files', '-z', '--', 'webapp'], {
  cwd: root,
  encoding: 'utf8',
}).split('\0').filter(file => /\.(?:js|mjs|cjs)$/.test(file)).sort();

if (files.length === 0) {
  console.error('No tracked frontend JavaScript files found.');
  process.exit(1);
}

let failed = 0;
for (const file of files) {
  const result = spawnSync(process.execPath, ['--check', file], {
    cwd: root,
    encoding: 'utf8',
  });
  if (result.status !== 0) {
    failed += 1;
    console.error(`FAIL ${file}`);
    console.error(result.error?.message || result.stderr || result.stdout);
  }
}
console.log(`Frontend syntax: ${files.length - failed}/${files.length} files passed.`);
process.exitCode = failed ? 1 : 0;
