import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import AxeBuilder from '@axe-core/playwright';

const base = new URL(process.env.TEST_BASE_URL || 'http://127.0.0.1:4321');
const artifacts = new URL('../artifacts/portfolio/', import.meta.url);
const sitemapResponse = await fetch(new URL('/sitemap.xml', base));
assert(sitemapResponse.ok, 'Production sitemap is available');
const sitemapXml = await sitemapResponse.text();
const projectRoutes = [...sitemapXml.matchAll(/<loc>([^<]+)<\/loc>/g)]
  .map((match) => new URL(match[1]).pathname)
  .filter((path) => path.startsWith('/work/'));
assert(projectRoutes.length > 0, 'Sitemap contains published projects');
const routes = ['/', ...new Set(projectRoutes), '/404.html'];
const widths = [360, 768, 1440];
await mkdir(artifacts, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  ...(process.env.BROWSER_EXECUTABLE
    ? { executablePath: process.env.BROWSER_EXECUTABLE }
    : process.platform === 'win32' ? { channel: 'msedge' } : {}),
});
const report = { date: new Date().toISOString(), base: base.href, browser: browser.version(), routes: [], links: [], errors: [] };
const context = await browser.newContext({ reducedMotion: 'reduce' });
const page = await context.newPage();
page.on('pageerror', (error) => report.errors.push(error.message));
page.on('console', (message) => {
  if (message.type() === 'error' && !message.location().url.endsWith('/404.html')) {
    report.errors.push(message.text());
  }
});
const links = new Set();

try {
  for (const route of routes) {
    for (const width of widths) {
      await page.setViewportSize({ width, height: 1000 });
      const response = await page.goto(new URL(route, base).href);
      assert(response, `No response for ${route}`);
      assert([200, ...(route === '/404.html' ? [404] : [])].includes(response.status()), `${route}: HTTP ${response.status()}`);
      await page.evaluate(() => document.fonts.ready);
      await page.locator('h1').waitFor();
      assert.equal(await page.locator('h1').count(), 1, `${route}: exactly one main heading`);
      assert(await page.title(), `${route}: page title`);
      assert(await page.locator('meta[name="description"]').getAttribute('content'), `${route}: description`);
      assert(await page.locator('link[rel="canonical"]').getAttribute('href'), `${route}: canonical`);
      assert.equal(await page.locator('html').getAttribute('lang'), 'en');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${route}: overflow at ${width}px`);
      for (const picture of await page.locator('img').all()) {
        await picture.scrollIntoViewIfNeeded();
        await picture.evaluate((element) => element.decode());
        assert(await picture.getAttribute('alt'), `${route}: image alternative text`);
        assert(await picture.getAttribute('width'), `${route}: reserved image width`);
        assert(await picture.getAttribute('height'), `${route}: reserved image height`);
      }
      const accessibility = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
      report.axeVersion = accessibility.testEngine.version;
      report.routes.push({ route, width, status: response.status(), violations: accessibility.violations.map(({ id, impact, nodes }) => ({ id, impact, elements: nodes.map(({ target, failureSummary }) => ({ target, failureSummary })) })) });
      for (const href of await page.locator('a[href]').evaluateAll((elements) => elements.map((element) => element.href))) {
        const url = new URL(href);
        if (url.origin === base.origin) links.add(url.href);
      }
      await page.evaluate(() => scrollTo(0, 0));
      const label = route === '/' ? 'home' : route.replaceAll('/', '-').replace(/^-|-$/g, '');
      await page.screenshot({ path: fileURLToPath(new URL(`${label}-${width}.png`, artifacts)), fullPage: true });
    }
  }

  for (const href of links) {
    const url = new URL(href);
    const response = await context.request.get(href);
    assert(response.ok(), `${url.pathname}: broken local link (${response.status()})`);
    if (url.hash) {
      await page.goto(url.href);
      assert(await page.evaluate((id) => Boolean(document.getElementById(id)), decodeURIComponent(url.hash.slice(1))), `${href}: missing anchor target`);
    }
    report.links.push({ path: url.pathname + url.hash, status: response.status() });
  }

  await page.goto(base.href);
  await page.keyboard.press('Tab');
  assert.equal(await page.evaluate(() => document.activeElement.textContent.trim()), 'Skip to content');
  assert(await page.locator('.skip-link').isVisible());
  const focus = await page.locator('.skip-link').evaluate((element) => ({ outline: getComputedStyle(element).outlineStyle, top: element.getBoundingClientRect().top }));
  assert.notEqual(focus.outline, 'none', 'Keyboard focus is visible');
  assert(focus.top >= 0, 'Skip link is on screen when focused');
  await page.keyboard.press('Enter');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'main', 'Skip link focuses main content');
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Work', exact: true }).click();
  assert.equal(new URL(page.url()).hash, '#work');
  await page.getByRole('link', { name: 'Read the Relay Desk case study' }).click();
  assert.equal(new URL(page.url()).pathname, '/work/relay-desk/');
  const sourceDownload = page.waitForEvent('download');
  await page.getByRole('link', { name: /Download runnable source/ }).click();
  assert.equal((await sourceDownload).suggestedFilename(), 'relay-desk.zip');

  await page.goto(base.href);
  assert.equal(await page.getByRole('link', { name: 'Email me', exact: false }).getAttribute('href'), 'mailto:haroldng2001@gmail.com');
  for (const link of await page.getByRole('link', { name: 'LinkedIn', exact: false }).all()) {
    assert.equal(await link.getAttribute('href'), 'https://www.linkedin.com/in/harold-ng-986a5b252/');
  }
  const pdf = await context.request.get(new URL('/resume.pdf', base).href);
  assert.equal((await pdf.body()).subarray(0, 5).toString(), '%PDF-');
  const archive = await context.request.get(new URL('/downloads/relay-desk.zip', base).href);
  assert.equal((await archive.body()).subarray(0, 4).toString('hex'), '504b0304');
  const robots = await context.request.get(new URL('/robots.txt', base).href);
  assert(robots.ok() && (await robots.text()).includes('User-agent:'), 'Robots file');
  const sitemap = await context.request.get(new URL('/sitemap.xml', base).href);
  assert(sitemap.ok() && (await sitemap.text()).includes('/work/relay-desk/'), 'Sitemap contains case studies');
  const social = await context.request.get(new URL('/images/social-preview.png', base).href);
  assert(social.ok() && (await social.body()).subarray(1, 4).toString() === 'PNG', 'Social preview is a real PNG');
  const unknown = await context.request.get(new URL('/this-page-does-not-exist/', base).href);
  assert.equal(unknown.status(), 404, 'Unknown paths return HTTP 404');
  assert((await unknown.text()).includes('Back to the homepage'), 'Unknown paths show a useful error page');
  assert.deepEqual(report.errors, [], 'No console or page errors');
  assert.deepEqual(report.routes.filter(({ violations }) => violations.length), [], 'Automated accessibility findings; see artifacts/portfolio/browser-check.json');
  report.passed = true;
  report.checks = [`${report.routes.length} page/viewport checks`, 'WCAG 2.2 AA automated scan', 'local links and anchors', 'keyboard skip and focus', 'work navigation', 'source download', 'PDF signature', 'contact URLs', 'sitemap', 'robots', 'social PNG', 'HTTP 404'];
  process.stdout.write(`Passed: ${report.routes.length} page/viewport checks, ${report.links.length} local links, keyboard and downloads.\n`);
} finally {
  await writeFile(new URL('browser-check.json', artifacts), JSON.stringify(report, null, 2));
  await browser.close();
}
