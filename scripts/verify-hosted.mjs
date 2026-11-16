import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { validateOrigin, verifyPageMetadata } from './verify-release.mjs';

export function verifyCache(headers, fingerprinted = false) {
  const policy = headers.get('cache-control') || '';
  if (fingerprinted) {
    assert.match(policy, /(?:^|,)\s*max-age=31536000(?:,|$)/i, 'Fingerprinted assets cache for one year');
    assert.match(policy, /\bimmutable\b/i, 'Fingerprinted assets are immutable');
    assert.doesNotMatch(policy, /\b(?:no-cache|no-store|max-age=0)\b/i, 'Fingerprint cache rule overrides the default');
  } else {
    assert.match(policy, /\b(?:no-cache|no-store|max-age=0)\b/i, 'Mutable files must revalidate');
    assert.doesNotMatch(policy, /\bimmutable\b/i, 'Mutable files cannot be immutable');
    assert(![...policy.matchAll(/\b(?:s-maxage|max-age)=(\d+)/gi)].some(([, age]) => Number(age) > 0), 'No positive freshness lifetime on mutable files');
  }
}

export async function verifyRedirect(start, expected, maxHops, fetchImpl = fetch) {
  let current = new URL(start);
  const seen = new Set();
  const hops = [];
  for (;;) {
    assert(!seen.has(current.href), `Redirect loop at ${current.href}`);
    seen.add(current.href);
    const response = await fetchImpl(current, { redirect: 'manual', signal: AbortSignal.timeout(30000) });
    if (![301, 302, 303, 307, 308].includes(response.status)) {
      await response.body?.cancel();
      assert.equal(response.status, 200, `Redirect destination: ${current.href}`);
      assert.equal(current.href, new URL(expected).href, 'Redirect preserves path/query and reaches the canonical origin');
      assert(hops.length > 0, 'A normalization redirect occurred');
      return { start: String(start), final: current.href, hops };
    }
    assert([301, 308].includes(response.status), `Normalization must be permanent, got ${response.status}`);
    const location = response.headers.get('location');
    assert(location, 'Redirect supplies Location');
    hops.push({ url: current.href, status: response.status, location });
    await response.body?.cancel();
    assert(hops.length <= maxHops, `Too many redirects from ${start}; limit ${maxHops}`);
    current = new URL(location, current);
  }
}

export async function verifyHosted({ base, mode, approvedOrigin, fetchImpl = fetch, report = {} }) {
  assert(['production', 'preview'].includes(mode), 'Mode must be production or preview');
  base = new URL(base);
  assert.equal(base.protocol, 'https:', 'Hosted checks require HTTPS');
  assert.equal(base.href, `${base.origin}/`, 'BASE_URL must be an origin without path, query or credentials');
  const origin = mode === 'production' ? validateOrigin(approvedOrigin, 'APPROVED_SITE_URL') : 'http://localhost:4321';
  if (mode === 'production') assert.equal(base.origin, origin, 'BASE_URL must match the approved production origin');
  Object.assign(report, { date: new Date().toISOString(), base: base.href, mode, commit: process.env.GITHUB_SHA || null, deploymentVersion: process.env.DEPLOYMENT_VERSION || null, location: process.env.TEST_LOCATION || 'Not recorded', routes: [], redirects: [], passed: false });

  async function get(path, status = 200, fingerprinted = false) {
    const response = await fetchImpl(new URL(path, base), { redirect: 'manual', signal: AbortSignal.timeout(30000) });
    assert.equal(response.status, status, `${path}: HTTP status`);
    const headers = response.headers;
    assert.equal(headers.get('x-content-type-options'), 'nosniff', `${path}: nosniff`);
    assert.equal(headers.get('x-frame-options'), 'DENY', `${path}: framing denied`);
    assert.equal(headers.get('referrer-policy'), 'strict-origin-when-cross-origin', `${path}: referrer policy`);
    for (const permission of ['camera', 'microphone', 'geolocation']) assert(headers.get('permissions-policy')?.includes(`${permission}=()`), `${path}: ${permission} denied`);
    verifyCache(headers, fingerprinted);
    if (mode === 'production' && status !== 404) assert.doesNotMatch(headers.get('x-robots-tag') || '', /\bnoindex\b/i, `${path}: production response is indexable`);
    if (mode === 'preview') assert.match(headers.get('x-robots-tag') || '', /\bnoindex\b/i, `${path}: provider preview noindex header`);
    const body = Buffer.from(await response.arrayBuffer());
    report.routes.push({ path, status, bytes: body.length, headers: Object.fromEntries(headers) });
    return { body, headers, text: body.toString('utf8') };
  }

  const sitemap = await get('/sitemap.xml');
  const locations = [...sitemap.text.matchAll(/<loc>([^<]+)<\/loc>/g)].map(([, value]) => new URL(value));
  assert(locations.length > 0, 'Sitemap has pages');
  for (const url of locations) assert.equal(url.origin, origin, 'Sitemap origin matches build mode');
  const routes = [...new Set(locations.map((url) => url.pathname))];
  for (const path of ['/', '/work/ifc-gpt/', '/work/relay-desk/']) assert(routes.includes(path), `Sitemap contains ${path}`);
  const assets = new Set();
  for (const path of routes) {
    const page = await get(path);
    assert.match(page.headers.get('content-type') || '', /text\/html/i);
    verifyPageMetadata({ html: page.text, pathname: path, origin, noindex: mode === 'preview' });
    for (const [, asset] of page.text.matchAll(/(?:src|href)="(\/_astro\/[^"?#]+)"/g)) assets.add(asset);
  }
  assert(assets.size > 0, 'Generated pages reference fingerprinted assets');
  for (const asset of assets) await get(asset, 200, true);
  const missing = await get('/__portfolio_missing__/', 404);
  assert(missing.text.includes('Back to the homepage'), 'Unknown paths render the useful custom 404');
  verifyPageMetadata({ html: missing.text, pathname: '/404/', origin, noindex: true });
  const robots = await get('/robots.txt');
  assert.match(robots.text, /^User-agent:\s*\*$/m);
  if (mode === 'production') {
    assert.match(robots.text, /^Allow:\s*\/$/m);
    assert(!/^Disallow:\s*\//m.test(robots.text), 'Production robots permits crawling');
    assert(robots.text.includes(`Sitemap: ${origin}/sitemap.xml`), 'Robots sitemap uses the custom origin');
  } else assert.match(robots.text, /^Disallow:\s*\/$/m);
  const pdf = await get('/resume.pdf');
  assert.equal(pdf.body.subarray(0, 5).toString(), '%PDF-', 'Resume contains PDF bytes');
  assert.match(pdf.headers.get('content-type') || '', /application\/pdf/i);
  const zip = await get('/downloads/relay-desk.zip');
  assert.equal(zip.body.subarray(0, 4).toString('hex'), '504b0304', 'Source download contains ZIP bytes');
  const png = await get('/images/social-preview.png');
  assert.equal(png.body.subarray(0, 8).toString('hex'), '89504e470d0a1a0a', 'Social preview contains PNG bytes');

  if (mode === 'production') {
    const query = '?deployment-check=path%2Fquery&preserve=1';
    for (const path of ['/', '/work/ifc-gpt/', '/resume.pdf']) {
      for (const source of [`http://${base.host}`, `https://www.${base.host}`, `http://www.${base.host}`]) {
        report.redirects.push(await verifyRedirect(`${source}${path}${query}`, `${origin}${path}${query}`, 1, fetchImpl));
      }
    }
    for (const source of [origin, `http://${base.host}`, `https://www.${base.host}`, `http://www.${base.host}`]) {
      report.redirects.push(await verifyRedirect(`${source}/work/ifc-gpt${query}`, `${origin}/work/ifc-gpt/${query}`, 2, fetchImpl));
    }
  }
  report.passed = true;
  return report;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const report = { passed: false };
  const artifacts = new URL('../artifacts/hosted/', import.meta.url);
  try {
    const { values } = parseArgs({ options: { mode: { type: 'string' } } });
    assert(process.env.BASE_URL || process.env.TEST_BASE_URL, 'Set BASE_URL to the deployed HTTPS origin');
    await verifyHosted({ base: process.env.BASE_URL || process.env.TEST_BASE_URL, mode: values.mode, approvedOrigin: process.env.APPROVED_SITE_URL, report });
    process.stdout.write(`Passed: ${report.routes.length} hosted paths and ${report.redirects.length} redirect journeys at ${report.base}\n`);
  } catch (error) {
    report.failure = error.stack;
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  } finally {
    await mkdir(artifacts, { recursive: true });
    await writeFile(new URL('http-check.json', artifacts), JSON.stringify(report, null, 2));
  }
}
