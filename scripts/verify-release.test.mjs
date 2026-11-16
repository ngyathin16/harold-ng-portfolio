import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { validateOrigin, verifyPageMetadata, verifyRelease } from './verify-release.mjs';

const approved = 'https://haroldng.dev';
const paths = ['/', '/work/ifc-gpt/', '/work/relay-desk/'];

function html(pathname, origin, noindex = false) {
  return `<html><head>
    <meta name="robots" content="${noindex ? 'noindex, nofollow' : 'index, follow'}">
    <link rel="canonical" href="${origin}${pathname}">
    <meta property="og:url" content="${origin}${pathname}">
    <meta property="og:image" content="${origin}/images/social-preview.png">
    <meta name="twitter:image" content="${origin}/images/social-preview.png">
  </head><body>Fixture</body></html>`;
}

async function fixture(t, mode = 'production') {
  const dist = await mkdtemp(join(tmpdir(), 'portfolio-release-'));
  t.after(() => rm(dist, { recursive: true, force: true }));
  const origin = mode === 'preview' ? 'http://localhost:4321' : mode === 'ci' ? 'https://portfolio-ci.invalid' : approved;
  const put = async (file, body) => {
    const target = join(dist, file);
    await mkdir(dirname(target), { recursive: true });
    await writeFile(target, body);
  };
  for (const pathname of paths) await put(`${pathname.slice(1)}index.html`, html(pathname, origin, mode === 'preview'));
  await put('404.html', html('/404/', origin, true));
  await put('sitemap.xml', `<urlset>${paths.map((pathname) => `<url><loc>${origin}${pathname}</loc></url>`).join('')}</urlset>`);
  await put('robots.txt', mode === 'preview' ? 'User-agent: *\nDisallow: /\n' : `User-agent: *\nAllow: /\nSitemap: ${origin}/sitemap.xml\n`);
  await put('_headers', '/*\n  X-Content-Type-Options: nosniff\n');
  await put('resume.pdf', '%PDF-1.7 fixture');
  await put('downloads/relay-desk.zip', Buffer.from('504b0304000000', 'hex'));
  await put('images/social-preview.png', Buffer.from('89504e470d0a1a0a0000', 'hex'));
  const options = { mode, dist, siteUrl: mode === 'preview' ? '' : origin, approvedSiteUrl: origin };
  return { options, origin, put };
}

test('custom origins reject missing, local, placeholder, provider, and non-origin values', () => {
  for (const value of [undefined, '', 'http://haroldng.dev', 'https://www.haroldng.dev', 'https://localhost', 'https://127.0.0.1', 'https://[::1]', 'https://example.com', 'https://portfolio-ci.invalid', 'https://preview.pages.dev', 'https://preview.workers.dev', 'https://your-domain.com', 'https://haroldng.dev/path', 'https://haroldng.dev/?', 'https://haroldng.dev/#', 'https://user@haroldng.dev', 'https://haroldng.dev:8080', 'https://-invalid.dev']) {
    assert.throws(() => validateOrigin(value), undefined, String(value));
  }
  assert.equal(validateOrigin(`${approved}/`), approved);
});

test('production refuses missing approval or the wrong selected origin', async (t) => {
  const { options } = await fixture(t);
  await assert.rejects(verifyRelease({ ...options, approvedSiteUrl: '' }), /APPROVED_SITE_URL/);
  await assert.rejects(verifyRelease({ ...options, approvedSiteUrl: 'https://haroldng.work' }), /independently approved/);
  await assert.rejects(verifyRelease({ ...options, siteUrl: '' }), /SITE_URL/);
});

test('valid production and unindexed preview artifacts pass separately', async (t) => {
  for (const mode of ['production', 'preview']) {
    const { options, origin } = await fixture(t, mode);
    assert.deepEqual(await verifyRelease(options), { mode, origin, routes: paths, htmlFiles: 4 });
  }
});

test('preview rejects an inherited production SITE_URL or indexed HTML', async (t) => {
  const { options, origin, put } = await fixture(t, 'preview');
  await assert.rejects(verifyRelease({ ...options, siteUrl: approved }), /leave SITE_URL unset/);
  await put('index.html', html('/', origin, false));
  await assert.rejects(verifyRelease(options), /must remain noindex/);
});

test('stale preview HTML fails a production release', async (t) => {
  const { options, put } = await fixture(t);
  await put('work/relay-desk/index.html', html('/work/relay-desk/', 'http://localhost:4321', true));
  await assert.rejects(verifyRelease(options), /canonical must use/);
});

test('404 stays noindex and production pages stay indexable', async (t) => {
  const { options, origin, put } = await fixture(t);
  await put('404.html', html('/404/', origin, false));
  await assert.rejects(verifyRelease(options), /404.html: must remain noindex/);
  await put('404.html', html('/404/', origin, true));
  await put('index.html', html('/', origin, true));
  await assert.rejects(verifyRelease(options), /production page must be indexable/);
});

test('social URL leaks and duplicate canonical tags fail', () => {
  const options = { pathname: '/', origin: approved, noindex: false };
  assert.throws(() => verifyPageMetadata({ ...options, html: html('/', approved).replace('name="twitter:image" content="https://haroldng.dev', 'name="twitter:image" content="https://example.com') }), /twitter:image/);
  assert.throws(() => verifyPageMetadata({ ...options, html: html('/', approved).replace('</head>', `<link rel="canonical" href="${approved}/"></head>`) }), /exactly one canonical/);
  assert.throws(() => verifyPageMetadata({ ...options, html: html('/', approved).replace('</head>', '<meta property="og:image:secure_url" content="https://preview.workers.dev/social.png"></head>') }), /metadata URL/);
});

test('future case studies require sitemap coverage and valid metadata', async (t) => {
  const { options, origin, put } = await fixture(t);
  await put('work/new-project/index.html', html('/work/new-project/', origin));
  await assert.rejects(verifyRelease(options), /Sitemap must cover/);
  await put('sitemap.xml', `<urlset>${[...paths, '/work/new-project/'].map((pathname) => `<url><loc>${origin}${pathname}</loc></url>`).join('')}</urlset>`);
  assert.equal((await verifyRelease(options)).htmlFiles, 5);
  await put('work/new-project/index.html', html('/work/new-project/', 'https://preview.workers.dev'));
  await assert.rejects(verifyRelease(options), /canonical must use/);
});

test('robots and sitemap must use the approved origin', async (t) => {
  const { options, origin, put } = await fixture(t);
  const original = await readFile(join(options.dist, 'sitemap.xml'), 'utf8');
  await put('sitemap.xml', original.replace(origin, 'https://preview.workers.dev'));
  await assert.rejects(verifyRelease(options), /Sitemap must cover/);
  await put('sitemap.xml', original);
  await put('robots.txt', 'User-agent: *\nDisallow: /\n');
  await assert.rejects(verifyRelease(options), /Robots instructions/);
});

test('missing or non-binary downloads and leaked source files fail', async (t) => {
  const { options, put } = await fixture(t);
  await put('downloads/relay-desk.zip', '<html>404</html>');
  await assert.rejects(verifyRelease(options), /invalid binary signature/);
  await rm(join(options.dist, 'downloads/relay-desk.zip'));
  await assert.rejects(verifyRelease(options), /Missing release asset/);
  await put('downloads/relay-desk.zip', Buffer.from('504b03040000', 'hex'));
  await put('.env', 'PRIVATE_VALUE=fixture');
  await assert.rejects(verifyRelease(options), /Release contains/);
});

test('CI fixture verifies indexable output but cannot pass the production gate', async (t) => {
  const { options } = await fixture(t, 'ci');
  assert.equal((await verifyRelease(options)).mode, 'ci');
  await assert.rejects(verifyRelease({ ...options, mode: 'production' }), /provider hostname/);
  await assert.rejects(verifyRelease({ ...options, siteUrl: approved }), /reserved fixture origin/);
  const previous = process.env.WORKERS_CI;
  process.env.WORKERS_CI = '1';
  try {
    await assert.rejects(verifyRelease(options), /cannot run in Workers Builds/);
  } finally {
    if (previous === undefined) delete process.env.WORKERS_CI;
    else process.env.WORKERS_CI = previous;
  }
});
