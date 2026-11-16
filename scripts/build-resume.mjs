import { readFile, writeFile, mkdir, mkdtemp, rm } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';

const root = fileURLToPath(new URL('../', import.meta.url));
const source = await readFile(join(root, 'content/resume.md'), 'utf8');
const escape = (text) => text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const inline = (text) => escape(text).replace(/\[([^\]]+)\]\((https:\/\/[^\s)]+|mailto:[^\s)]+)\)/g, '<a href="$2">$1</a>');
const blocks = source.trim().split(/\r?\n\s*\r?\n/).map((block) => {
  const heading = /^(#{1,3}) (.+)$/.exec(block);
  if (heading) return `<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`;
  if (block.startsWith('- ')) return `<ul>${block.split(/\r?\n/).map((line) => `<li>${inline(line.slice(2))}</li>`).join('')}</ul>`;
  return `<p>${inline(block).replace(/ {2}\r?\n/g, '<br>')}</p>`;
});
const html = `<!doctype html><html lang="en"><meta charset="utf-8"><title>Harold Ng — Résumé</title><style>
@page{size:A4;margin:14mm 16mm}body{font:9.5pt/1.42 Arial,sans-serif;color:#202823;margin:0}h1{font-size:24pt;line-height:1.1;margin:0 0 6pt;font-weight:650}h2{font-size:10pt;text-transform:uppercase;letter-spacing:.1em;border-bottom:1px solid #c6d0ca;padding-bottom:3pt;margin:13pt 0 7pt}h3{font-size:10pt;margin:7pt 0 2pt}p{margin:3pt 0 6pt}ul{padding-left:14pt;margin:5pt 0 8pt}li{margin-bottom:4pt}a{color:#235c50;text-decoration:none}h2,h3{break-after:avoid}li{break-inside:avoid}
</style><body>${blocks.join('\n')}</body></html>`;
assert(html.includes('mailto:haroldng2001@gmail.com'));
assert(!html.includes('[LinkedIn]'));
const temporary = await mkdtemp(join(tmpdir(), 'harold-resume-'));
const output = resolve(root, 'public/resume.pdf');
await mkdir(join(root, 'public'), { recursive: true });
await writeFile(join(temporary, 'resume.html'), html);
const browser = process.env.BROWSER_EXECUTABLE || 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
try {
  await promisify(execFile)(browser, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-pdf-header-footer',
    `--user-data-dir=${join(temporary, 'browser')}`, `--print-to-pdf=${output}`,
    pathToFileURL(join(temporary, 'resume.html')).href,
  ], { windowsHide: true, timeout: 45000 });
  const pdf = await readFile(output);
  assert.equal(pdf.subarray(0, 5).toString(), '%PDF-');
  assert(pdf.length > 10000, 'PDF is unexpectedly small');
  process.stdout.write(`Created ${output} (${pdf.length} bytes)\n`);
} finally {
  // This directory was created by this process under the operating system temp directory.
  assert(resolve(temporary).startsWith(resolve(tmpdir()) + '\\') || resolve(temporary).startsWith(resolve(tmpdir()) + '/'));
  await rm(temporary, { recursive: true, force: true, maxRetries: 4, retryDelay: 250 });
}
