import assert from 'node:assert/strict';
import test from 'node:test';
import { verifyCache, verifyHosted, verifyRedirect } from './verify-hosted.mjs';

test('mutable files revalidate and fingerprinted files have only the immutable policy', () => {
  verifyCache(new Headers({ 'cache-control': 'public, max-age=0, must-revalidate' }));
  verifyCache(new Headers({ 'cache-control': 'public, max-age=31536000, immutable' }), true);
  for (const policy of ['', 'public, max-age=3600', 'no-cache, immutable', 'max-age=0, s-maxage=60']) {
    assert.throws(() => verifyCache(new Headers({ 'cache-control': policy })));
  }
  assert.throws(() => verifyCache(new Headers({ 'cache-control': 'max-age=0, max-age=31536000, immutable' }), true));
});

function responses(entries) {
  return async (url) => {
    assert(Object.hasOwn(entries, url.href), `Unexpected request: ${url}`);
    const [status, location] = entries[url.href];
    return new Response('', { status, headers: location ? { location } : {} });
  };
}

test('canonical redirect preserves encoded query, and slash normalization allows a second hop', async () => {
  const from = 'http://www.harold.work/work/ifc-gpt?path=a%2Fb&x=1';
  const middle = 'https://harold.work/work/ifc-gpt?path=a%2Fb&x=1';
  const to = 'https://harold.work/work/ifc-gpt/?path=a%2Fb&x=1';
  const result = await verifyRedirect(from, to, 2, responses({ [from]: [301, middle], [middle]: [308, to], [to]: [200] }));
  assert.equal(result.final, to);
  assert.equal(result.hops.length, 2);
  await assert.rejects(verifyRedirect(from, to, 1, responses({ [from]: [301, middle], [middle]: [308, to] })), /Too many redirects/);
});

test('redirect check rejects temporary redirects, loops and dropped queries', async () => {
  const from = 'http://www.harold.work/?keep=1';
  const to = 'https://harold.work/?keep=1';
  await assert.rejects(verifyRedirect(from, to, 1, responses({ [from]: [302, to] })), /permanent/);
  await assert.rejects(verifyRedirect(from, to, 2, responses({ [from]: [301, from] })), /loop/);
  await assert.rejects(verifyRedirect(from, to, 1, responses({ [from]: [301, 'https://harold.work/'], 'https://harold.work/': [200] })), /preserves path\/query/);
});

test('hosted checker rejects missing approval, wrong origins and insecure URLs before fetching', async () => {
  const fetchImpl = () => assert.fail('Invalid configuration must not fetch');
  await assert.rejects(verifyHosted({ base: 'https://harold.work', mode: 'production', fetchImpl }));
  await assert.rejects(verifyHosted({ base: 'https://haroldng.work', approvedOrigin: 'https://harold.work', mode: 'production', fetchImpl }), /approved production origin/);
  await assert.rejects(verifyHosted({ base: 'http://harold.work', mode: 'preview', fetchImpl }), /HTTPS/);
});

function hostedFixture(mode = 'preview') {
  const paths = ['/', '/work/ifc-gpt/', '/work/relay-desk/', '/work/future-project/'];
  const origin = mode === 'preview' ? 'http://localhost:4321' : 'https://harold.work';
  const html = (path) => `<link rel="canonical" href="${origin}${path}"><meta property="og:url" content="${origin}${path}"><meta property="og:image" content="${origin}/images/social-preview.png"><meta name="twitter:image" content="${origin}/images/social-preview.png"><meta name="robots" content="${mode === 'preview' || path === '/404/' ? 'noindex, nofollow' : 'index, follow'}"><link rel="stylesheet" href="/_astro/site.abcd.css">Back to the homepage`;
  const state = { brokenPath: undefined, rawStatus: 404, rawCustom: mode === 'production', rawNoindex: true, navigationStatus: 404, navigationCalls: 0 };
  const response = (url, navigation) => {
    const path = url.pathname;
    if (path === '/__portfolio_missing__/' && !navigation && !state.rawCustom) {
      return new Response('Not found', { status: state.rawStatus, headers: state.rawNoindex ? { 'x-robots-tag': 'noindex' } : {} });
    }
    const bodies = {
      '/sitemap.xml': `<urlset>${paths.map((path) => `<url><loc>${origin}${path}</loc></url>`).join('')}</urlset>`,
      '/robots.txt': mode === 'preview' ? 'User-agent: *\nDisallow: /\n' : `User-agent: *\nAllow: /\nSitemap: ${origin}/sitemap.xml\n`,
      '/resume.pdf': '%PDF-fixture',
      '/downloads/relay-desk.zip': Buffer.from('504b0304', 'hex'),
      '/images/social-preview.png': Buffer.from('89504e470d0a1a0a', 'hex'),
      '/_astro/site.abcd.css': 'body {}',
      '/__portfolio_missing__/': html('/404/'),
      ...Object.fromEntries(paths.map((path) => [path, html(path)])),
    };
    assert(Object.hasOwn(bodies, path), `Unexpected path ${path}`);
    return new Response(path === state.brokenPath ? 'Wrong content' : bodies[path], {
      status: path === '/__portfolio_missing__/' ? (navigation ? state.navigationStatus : state.rawStatus) : 200,
      headers: {
        'content-type': path === '/resume.pdf' ? 'application/pdf' : 'text/html',
        'x-content-type-options': 'nosniff',
        'x-frame-options': 'DENY',
        'referrer-policy': 'strict-origin-when-cross-origin',
        'permissions-policy': 'camera=(), microphone=(), geolocation=()',
        ...(mode === 'preview' ? { 'x-robots-tag': 'noindex' } : {}),
        'cache-control': path.startsWith('/_astro/') ? 'public, max-age=31536000, immutable' : 'public, max-age=0, must-revalidate',
      },
    });
  };
  const config = {
    base: mode === 'preview' ? 'https://preview.harold.workers.dev' : origin,
    mode, approvedOrigin: origin,
    fetchImpl: async (url) => response(url, false),
    navigationFetch: async (url) => {
      assert.equal(url.pathname, '/__portfolio_missing__/');
      state.navigationCalls++;
      return response(url, true);
    },
  };
  return { config, state };
}

test('hosted preview checks rendered noindex, real 404, binary downloads and cache headers', async () => {
  const { config, state } = hostedFixture();
  const result = await verifyHosted(config);
  assert.equal(result.passed, true);
  assert(result.routes.some(({ path }) => path === '/work/future-project/'), 'New case studies join the hosted check');
  assert.equal(result.previewRaw404.body, 'Not found');
  assert.equal(result.previewRaw404.custom404, false);
  assert.equal(result.previewRaw404.status, 404);
  assert(result.routes.some(({ path, request }) => path === '/__portfolio_missing__/' && request === 'navigation'));
  assert.equal(state.navigationCalls, 1);
  state.brokenPath = '/work/future-project/';
  await assert.rejects(verifyHosted(config), /canonical/);
  state.brokenPath = '/resume.pdf';
  await assert.rejects(verifyHosted(config), /PDF bytes/);
  state.brokenPath = '/__portfolio_missing__/';
  await assert.rejects(verifyHosted(config), /useful custom 404/);
});

test('preview rejects SPA success for either missing request and requires provider noindex', async () => {
  const { config, state } = hostedFixture();
  state.rawStatus = 200;
  await assert.rejects(verifyHosted(config), /non-navigation misses return HTTP 404/);
  state.rawStatus = 404;
  state.rawNoindex = false;
  await assert.rejects(verifyHosted(config), /non-navigation 404 remains noindex/);
  state.rawNoindex = true;
  state.navigationStatus = 200;
  await assert.rejects(verifyHosted(config), /HTTP status/);
});

test('production still requires the custom page and security headers on raw HTTP 404', async () => {
  const { config, state } = hostedFixture('production');
  state.rawCustom = false;
  await assert.rejects(verifyHosted(config), /nosniff/);
  state.rawCustom = true;
  state.brokenPath = '/__portfolio_missing__/';
  await assert.rejects(verifyHosted(config), /useful custom 404/);
  state.brokenPath = undefined;
  state.rawStatus = 200;
  await assert.rejects(verifyHosted(config), /HTTP status/);
  assert.equal(state.navigationCalls, 0, 'Production never substitutes a navigation request for its raw HTTP check');
});
