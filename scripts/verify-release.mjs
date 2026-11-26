import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { isIP } from 'node:net';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

const previewOrigin = 'http://localhost:4321';
const excludedHosts = ['localhost', 'local', 'internal', 'test', 'invalid', 'example', 'example.com', 'example.net', 'example.org', 'workers.dev', 'pages.dev', 'vercel.app', 'netlify.app', 'github.io', 'onrender.com'];

export function validateOrigin(value, name = 'SITE_URL') {
  assert(value && value === value.trim(), `${name} must be set to the approved HTTPS origin.`);
  const url = new URL(value);
  assert(url.protocol === 'https:' && (value === url.origin || value === `${url.origin}/`) && !url.port,
    `${name} must be an HTTPS origin without a path, query, credentials, or nonstandard port.`);
  assert(!isIP(url.hostname) && !url.hostname.startsWith('www.') && /^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z][a-z0-9-]*[a-z0-9]$/.test(url.hostname),
    `${name} must use the custom apex domain.`);
  assert(!excludedHosts.some((host) => url.hostname === host || url.hostname.endsWith(`.${host}`))
    && !/(^|[.-])(placeholder|chosen-domain|your-domain|your-actual-hostname)([.-]|$)/.test(url.hostname),
  `${name} cannot use a placeholder, local, test, or provider hostname.`);
  return url.origin;
}

export function validateResearchApiUrl(value) {
  const name = 'PUBLIC_RESEARCH_API_URL';
  assert(typeof value === 'string' && value && value === value.trim(), `${name} must explicitly configure the production HTTPS API origin.`);
  const url = new URL(value);
  assert(url.protocol === 'https:' && (value === url.origin || value === `${url.origin}/`) && !url.port,
    `${name} must be an HTTPS origin without a path, query, credentials, or nonstandard port.`);
  assert(!isIP(url.hostname) && /^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z][a-z0-9-]*[a-z0-9]$/.test(url.hostname)
    && !['localhost', 'local', 'internal', 'test', 'invalid', 'example', 'example.com', 'example.net', 'example.org'].some((host) => url.hostname === host || url.hostname.endsWith(`.${host}`)),
  `${name} must use a public API hostname, not a local address or fixture.`);
  return value;
}

function decodeEntities(value) {
  const named = { amp: '&', quot: '"', apos: "'", lt: '<', gt: '>' };
  return value.replace(/&(#x[\da-f]+|#\d+|amp|quot|apos|lt|gt);/gi, (_, entity) => entity.startsWith('#')
    ? String.fromCodePoint(entity[1].toLowerCase() === 'x' ? parseInt(entity.slice(2), 16) : Number(entity.slice(1)))
    : named[entity.toLowerCase()]);
}

function verifyResearchApi(html, apiUrl) {
  const roots = [...html.matchAll(/<div\b[^>]*>/gi)]
    .map(([tag]) => [...tag.matchAll(/\s([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g)]
      .map(([, key, double, single, unquoted]) => [key.toLowerCase(), decodeEntities(double ?? single ?? unquoted ?? '')]))
    .filter((attributes) => attributes.some(([key, value]) => key === 'id' && value === 'research-app'));
  assert.equal(roots.length, 1, 'Research release must contain exactly one research-app root.');
  assert.equal(roots[0].filter(([key]) => key === 'id').length, 1, 'Research root must contain exactly one id.');
  const destinations = roots[0].filter(([key]) => key === 'data-api');
  assert.equal(destinations.length, 1, 'Research root must contain exactly one data-api destination.');
  assert.equal(destinations[0][1], apiUrl, 'Research data-api must exactly match PUBLIC_RESEARCH_API_URL; rebuild the artifact with the configured HTTPS API.');
}

function metadata(html) {
  return [...html.matchAll(/<(meta|link)\b[^>]*>/gi)].map(([tag, type]) => ({
    type: type.toLowerCase(),
    ...Object.fromEntries([...tag.matchAll(/\s([^\s=/>]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g)]
      .map(([, key, double, single]) => [key.toLowerCase(), decodeEntities(double ?? single)])),
  }));
}

export function verifyPageMetadata({ html, pathname, origin, noindex, label = pathname }) {
  const tags = metadata(html);
  const one = (predicate, name, attribute = 'content') => {
    const matches = tags.filter(predicate);
    assert.equal(matches.length, 1, `${label}: expected exactly one ${name}.`);
    assert(matches[0][attribute], `${label}: missing ${name} value.`);
    return matches[0][attribute];
  };
  const canonical = one((tag) => tag.type === 'link' && tag.rel === 'canonical', 'canonical', 'href');
  assert.equal(canonical, new URL(pathname, origin).href, `${label}: canonical must use the expected origin and route.`);
  assert.equal(one((tag) => tag.property === 'og:url', 'og:url'), canonical, `${label}: Open Graph URL must match canonical.`);
  for (const key of ['og:image', 'twitter:image']) {
    assert.equal(one((tag) => tag.property === key || tag.name === key, key), new URL('/images/social-preview.png', origin).href,
      `${label}: ${key} must use the release origin and social image.`);
  }
  for (const tag of tags.filter((tag) => /^(?:og:(?:url|image(?::(?:url|secure_url))?|video(?::(?:url|secure_url))?|audio(?::(?:url|secure_url))?)|twitter:(?:url|image(?::src)?))$/.test(tag.property || tag.name || ''))) {
    assert.equal(new URL(tag.content).origin, origin, `${label}: metadata URL must use the release origin.`);
  }
  const directives = one((tag) => tag.name === 'robots', 'robots').toLowerCase().split(/[\s,]+/);
  if (noindex) {
    assert(directives.includes('noindex') && !directives.includes('index'), `${label}: must remain noindex.`);
  } else {
    assert(directives.includes('index') && !directives.includes('noindex') && !directives.includes('none'), `${label}: production page must be indexable.`);
  }
}

async function listFiles(directory, prefix = '') {
  const entries = await readdir(directory, { withFileTypes: true });
  return (await Promise.all(entries.map(async (entry) => {
    assert(!entry.isSymbolicLink(), `Release assets must not contain symlinks: ${prefix}${entry.name}`);
    return entry.isDirectory()
      ? listFiles(resolve(directory, entry.name), `${prefix}${entry.name}/`)
      : `${prefix}${entry.name}`;
  }))).flat();
}

export async function verifyRelease({ mode, dist = 'dist', siteUrl = process.env.SITE_URL, approvedSiteUrl = process.env.APPROVED_SITE_URL, researchApiUrl = process.env.PUBLIC_RESEARCH_API_URL }) {
  assert(['production', 'preview', 'ci'].includes(mode), 'Choose --mode production, preview, or ci.');
  let origin = previewOrigin;
  if (mode === 'ci') {
    assert(!process.env.WORKERS_CI, 'CI fixture mode cannot run in Workers Builds.');
    origin = 'https://portfolio-ci.invalid';
    assert.equal(siteUrl, origin, 'CI mode requires the reserved fixture origin.');
    assert.equal(approvedSiteUrl, origin, 'CI mode requires the reserved fixture origin.');
  } else if (mode === 'production') {
    origin = validateOrigin(siteUrl);
    assert.equal(origin, validateOrigin(approvedSiteUrl, 'APPROVED_SITE_URL'), 'SITE_URL must match the independently approved production origin.');
    validateResearchApiUrl(researchApiUrl);
  } else {
    assert(!siteUrl, 'Preview builds must leave SITE_URL unset.');
  }

  const files = await listFiles(dist);
  for (const required of ['index.html', 'work/ifc-gpt/index.html', 'work/relay-desk/index.html', '404.html', 'resume.pdf', 'downloads/relay-desk.zip', 'images/social-preview.png', 'robots.txt', 'sitemap.xml', '_headers']) {
    assert(files.includes(required), `Missing release asset: ${required}`);
  }
  if (mode === 'production') assert(files.includes('applications/research/index.html'), 'Missing release asset: applications/research/index.html');
  assert(!files.some((file) => /(^|\/)(?:\.env(?:\..*)?|\.git|node_modules|src|docs|projects|artifacts)(\/|$)|\.(?:sqlite|sqlite3|db)(?:-|$)/i.test(file)), 'Release contains source, planning, credential, or database files.');

  const pages = files.filter((file) => file.endsWith('.html')).sort();
  const routes = [];
  for (const file of pages) {
    const route = `/${/(^|\/)index\.html$/.test(file) ? file.replace(/(^|\/)index\.html$/, '$1') : file.replace(/\.html$/, '')}`;
    const pathname = route.endsWith('/') ? route : `${route}/`;
    const html = await readFile(resolve(dist, file), 'utf8');
    verifyPageMetadata({
      html, pathname, origin,
      noindex: mode === 'preview' || file === '404.html', label: file,
    });
    if (mode === 'production' && file === 'applications/research/index.html') verifyResearchApi(html, researchApiUrl);
    if (file !== '404.html') routes.push(pathname);
  }

  const sitemap = await readFile(resolve(dist, 'sitemap.xml'), 'utf8');
  const locations = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)].map(([, location]) => decodeEntities(location));
  assert.deepEqual(locations.sort(), routes.map((route) => new URL(route, origin).href).sort(), 'Sitemap must cover every public HTML route exactly once, using the release origin.');
  const robots = await readFile(resolve(dist, 'robots.txt'), 'utf8');
  assert.equal(robots.replaceAll('\r\n', '\n'), mode !== 'preview'
    ? `User-agent: *\nAllow: /\nSitemap: ${origin}/sitemap.xml\n`
    : 'User-agent: *\nDisallow: /\n', 'Robots instructions must match the release mode and origin.');

  for (const [file, signature] of [
    ['resume.pdf', Buffer.from('%PDF-')],
    ['downloads/relay-desk.zip', Buffer.from('504b0304', 'hex')],
    ['images/social-preview.png', Buffer.from('89504e470d0a1a0a', 'hex')],
  ]) {
    assert((await readFile(resolve(dist, file))).subarray(0, signature.length).equals(signature), `${file}: invalid binary signature.`);
  }
  return { mode, origin, routes: routes.sort(), htmlFiles: pages.length };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const { values } = parseArgs({ options: { mode: { type: 'string' }, dist: { type: 'string', default: 'dist' } } });
    const result = await verifyRelease(values);
    process.stdout.write(`Release artifact verified: ${result.mode}, ${result.htmlFiles} HTML pages, ${result.origin}\n`);
  } catch (error) {
    process.stderr.write(`Release verification failed: ${error.message}\n`);
    process.exitCode = 1;
  }
}
