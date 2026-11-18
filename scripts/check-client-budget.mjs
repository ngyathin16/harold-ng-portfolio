import assert from 'node:assert/strict';
import { mkdir, readdir, readFile, writeFile } from 'node:fs/promises';
import { gzipSync } from 'node:zlib';

const directory = new URL('../dist/', import.meta.url);
const output = await readdir(directory, { recursive: true });
const files = output.filter(file => file.endsWith('.js'));
const chunks = await Promise.all(files.map(async file => ({
  file: file.replaceAll('\\', '/'),
  gzipBytes: gzipSync(await readFile(new URL(file.replaceAll('\\', '/'), directory))).length,
})));
const routes = await Promise.all(output.filter(file => file.endsWith('.html')).map(async file => {
  const html = await readFile(new URL(file.replaceAll('\\', '/'), directory), 'utf8');
  const inline = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)]
    .filter(([, attributes, code]) => !/\bsrc\s*=|application\/ld\+json/i.test(attributes) && code.trim());
  return { file: file.replaceAll('\\', '/'), inlineGzipBytes: inline.reduce((sum, [, , code]) => sum + gzipSync(code).length, 0) };
}));
const externalGzipBytes = chunks.reduce((total, chunk) => total + chunk.gzipBytes, 0);
const maxInlineGzipBytes = Math.max(0, ...routes.map(route => route.inlineGzipBytes));
const report = {
  date: new Date().toISOString(),
  chunks,
  routes,
  externalGzipBytes,
  maxInlineGzipBytes,
  totalGzipBytes: externalGzipBytes + maxInlineGzipBytes,
  budgetBytes: 90_000,
  scope: 'All first-party JavaScript files in dist, including every loadable chunk, plus the largest sum of separately gzipped inline scripts on one HTML route. This conservative upper bound includes shared code and bootstrap scripts. Provider analytics is measured separately on the hosted site.',
};
await mkdir(new URL('../artifacts/design/', import.meta.url), { recursive: true });
await writeFile(new URL('../artifacts/design/bundle.json', import.meta.url), JSON.stringify(report, null, 2));
assert(report.totalGzipBytes <= report.budgetBytes, `First-party JavaScript exceeds 90 KB gzip: ${report.totalGzipBytes} bytes`);
process.stdout.write(`First-party JavaScript: ${report.totalGzipBytes} bytes gzip across ${chunks.length} chunks; 90,000-byte budget passed.\n`);
