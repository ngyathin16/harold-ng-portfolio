import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium, firefox, webkit } from 'playwright';

const base = new URL(process.env.BASE_URL || process.env.TEST_BASE_URL || 'http://127.0.0.1:4321');
const engine = process.env.BROWSER_ENGINE || 'chromium';
const browsers = { chromium, firefox, webkit };
assert(Object.hasOwn(browsers, engine), 'BROWSER_ENGINE must be chromium, firefox or webkit');
const browser = await browsers[engine].launch({
  headless: true,
  ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE }
    : engine === 'chromium' && process.platform === 'win32' ? { channel: 'msedge' } : {}),
});
const artifacts = new URL(`../artifacts/design/${engine}/`, import.meta.url);
await mkdir(artifacts, { recursive: true });
const projects = [
  { slug: 'queryproof', title: 'QueryProof', repository: 'queryproof' },
  { slug: 'releaseguard', title: 'ReleaseGuard', repository: 'releaseguard' },
  { slug: 'relay-desk', title: 'Relay Desk', repository: 'relay-desk' },
  { slug: 'ifc-gpt', title: 'IFC-GPT v2', repository: 'ifc-gpt2.0' },
];
const routes = ['/', ...projects.map(project => `/work/${project.slug}/`)];
const report = { date: new Date().toISOString(), base: base.href, engine, browser: browser.version(), viewports: [], noJavaScript: [], limitations: [], errors: [], passed: false };
const context = await browser.newContext({ reducedMotion: 'reduce' });
const page = await context.newPage();
page.on('pageerror', error => report.errors.push(error.message));
page.on('console', message => {
  if (message.type() === 'error') report.errors.push(message.text());
});

const visible = async (locator, message) => assert(await locator.isVisible(), message);
const noOverflow = async (target, label) => {
  assert(await target.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${label}: horizontal page overflow`);
};

try {
  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const route of routes) {
      const response = await page.goto(new URL(route, base).href, { waitUntil: 'domcontentloaded' });
      assert([200, 304].includes(response?.status()), `${route}: successful response or validated browser cache`);
      await page.waitForFunction(() => document.fonts.status === 'loaded', undefined, { timeout: 10000 });
      await noOverflow(page, `${route} at ${width}`);
      const nav = page.getByRole('navigation', { name: 'Main navigation', exact: true });
      for (const name of ['Work', 'About', 'Contact']) {
        const link = nav.getByRole('link', { name, exact: true });
        await visible(link, `${route}: ${name} remains visible at ${width}`);
        assert.equal(await link.getAttribute('href'), `/#${name.toLowerCase()}`);
      }
      assert.match(await page.locator('meta[property="og:image:alt"]').getAttribute('content'), /AI Solutions Engineer/);
      if (route === '/') {
        assert.equal(await page.title(), 'Harold Ng — AI Solutions Engineer in Hong Kong');
        for (const selector of ['meta[name="description"]', 'meta[property="og:title"]', 'meta[property="og:description"]', 'meta[name="twitter:title"]', 'meta[name="twitter:description"]']) {
          assert.match(await page.locator(selector).getAttribute('content'), /AI Solutions Engineer/, `${selector}: current title`);
        }
        const text = await page.locator('main').innerText();
        assert.match(text, /AI\s+Solutions\s+Engineer/);
        assert(!/\bA\.?I\.? [Ee]ngineer\b/.test(text), 'Homepage has no stale personal title');
        for (const project of projects) {
          await visible(page.locator(`#work a[href="/work/${project.slug}/"]`).first(), `${project.title}: discoverable case study`);
        }
      } else {
        const project = projects.find(item => route === `/work/${item.slug}/`);
        assert.equal(await page.locator('h1').innerText(), project.title);
        await visible(page.getByRole('complementary').filter({ has: page.getByText('What to look for', { exact: true }) }), 'Case study has an overview');
        const workflow = page.getByRole('region', { name: 'From input to a decision.' });
        await visible(workflow, 'Case workflow is visible');
        assert((await workflow.getByRole('listitem').count()) >= 3, 'Workflow explains meaningful stages');
        await visible(page.locator(`.study-actions a[href="https://github.com/ngyathin16/${project.repository}"]`), 'Case source is directly accessible');
        const cover = page.locator('.study-preview img');
        await page.waitForFunction(element => element.complete && element.naturalWidth > 0, await cover.elementHandle(), { timeout: 10000 });
        for (const video of await page.locator('video').all()) {
          await visible(video, 'Recorded evidence has a visible player');
          assert.equal(await video.getAttribute('preload'), 'none', 'Recorded evidence loads on demand');
          const poster = await video.getAttribute('poster');
          assert(poster, 'Recorded evidence has a poster');
          await page.evaluate(async source => {
            const image = new Image();
            image.src = source;
            await image.decode();
          }, new URL(poster, base).href);
        }
        if (width <= 390) {
          const summary = page.locator('details.study-mobile-contents > summary');
          await visible(summary, 'Mobile section index has a visible native control');
          await summary.focus();
          await page.keyboard.press('Enter');
          assert.equal(await page.locator('details.study-mobile-contents').getAttribute('open'), '', 'Keyboard opens mobile section index');
          const first = page.getByRole('navigation', { name: 'Case study sections' }).getByRole('link').first();
          const href = await first.getAttribute('href');
          await first.click();
          assert.equal(new URL(page.url()).hash, href, 'Section-index link navigates to its heading');
          assert(await page.locator(href).count(), 'Section-index target exists');
        }
      }
      report.viewports.push({ route, width });
    }
    await page.goto(base.href, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.fonts.status === 'loaded', undefined, { timeout: 10000 });
    for (const image of await page.locator('img').all()) {
      await image.scrollIntoViewIfNeeded();
      await page.waitForFunction(element => element.complete && element.naturalWidth > 0, await image.elementHandle(), { timeout: 10000 });
      await image.evaluate(element => element.decode());
    }
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: fileURLToPath(new URL(`homepage-${width}.png`, artifacts)), fullPage: true });
  }

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(base.href, { waitUntil: 'domcontentloaded' });
  const navigation = page.getByRole('navigation', { name: 'Main navigation', exact: true });
  await navigation.getByRole('link', { name: 'About', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('nav[aria-label="Main navigation"] a[href="/#about"]')?.getAttribute('aria-current') === 'location');
  assert.equal(await navigation.evaluate(element => element.getAnimations({ subtree: true }).filter(animation => animation.playState === 'running').length), 0, 'Reduced motion removes navigation travel');
  const aboutTop = await page.locator('#about').evaluate(element => element.getBoundingClientRect().top);
  const headerBottom = await navigation.evaluate(element => element.closest('header').getBoundingClientRect().bottom);
  assert(aboutTop >= headerBottom - 2, 'Sticky navigation does not cover the selected section heading');

  for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await navigation.getByRole('link', { name: 'Contact', exact: true }).click();
    assert.equal(new URL(page.url()).hash, '#contact', 'Contact navigation reaches its section');
    await page.waitForFunction(() => document.querySelector('nav[aria-label="Main navigation"] a[href="/#contact"]')?.getAttribute('aria-current') === 'location');
  }

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(base.href, { waitUntil: 'domcontentloaded' });
  let focusedWork = false;
  if (engine === 'webkit' && process.platform === 'win32') {
    report.limitations.push('Windows Playwright WebKit skips native links in its Tab order, reproduced with an independent plain-HTML fixture. Navigation focus appearance and Enter activation are checked after programmatic focus; native Tab reachability is covered by Chromium and Firefox. This engine run is not a Safari-device test.');
    await navigation.getByRole('link', { name: 'Work', exact: true }).focus();
    focusedWork = await page.evaluate(() => document.activeElement?.getAttribute('href') === '/#work');
  } else {
    for (let i = 0; i < 8; i++) {
      await page.keyboard.press('Tab');
      focusedWork = await page.evaluate(() => document.activeElement?.getAttribute('href') === '/#work');
      if (focusedWork) break;
    }
  }
  assert(focusedWork, 'Main navigation link receives focus');
  const focus = await page.evaluate(() => {
    const element = document.activeElement;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return { outline: style.outlineStyle, width: parseFloat(style.outlineWidth), top: rect.top, bottom: rect.bottom };
  });
  assert(focus.outline !== 'none' && focus.width > 0 && focus.top >= 0 && focus.bottom <= 844, 'Keyboard focus is visible and on screen');
  await page.keyboard.press('Enter');
  assert.equal(new URL(page.url()).hash, '#work', 'Keyboard activates the focused navigation link');

  for (const route of ['/', '/work/queryproof/']) {
    await page.goto(new URL(route, base).href, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.fonts.status === 'loaded', undefined, { timeout: 10000 });
    await page.evaluate(() => {
      const sizes = [...document.querySelectorAll('body, body *')]
        .filter(element => element instanceof HTMLElement)
        .map(element => [element, parseFloat(getComputedStyle(element).fontSize)]);
      for (const [element, size] of sizes) element.style.setProperty('font-size', `${size * 2}px`, 'important');
    });
    await noOverflow(page, `${route}: 200% text enlargement at 390px`);
  }

  const noScript = await browser.newContext({ javaScriptEnabled: false, reducedMotion: 'reduce', viewport: { width: 390, height: 844 } });
  const staticPage = await noScript.newPage();
  for (const route of routes) {
    await staticPage.goto(new URL(route, base).href, { waitUntil: 'domcontentloaded' });
    await visible(staticPage.locator('h1'), `${route}: heading visible without JavaScript`);
    await noOverflow(staticPage, `${route}: no-JavaScript reflow`);
    const links = staticPage.getByRole('navigation', { name: 'Main navigation', exact: true });
    for (const name of ['Work', 'About', 'Contact']) await visible(links.getByRole('link', { name, exact: true }), `${name}: readable without hydration`);
    if (route === '/') {
      assert.match(await staticPage.locator('main').innerText(), /AI\s+Solutions\s+Engineer/);
      await links.getByRole('link', { name: 'Contact', exact: true }).click();
      assert.equal(new URL(staticPage.url()).hash, '#contact', 'Contact anchor works without JavaScript');
    } else {
      await visible(staticPage.getByRole('region', { name: 'From input to a decision.' }), 'Workflow renders without JavaScript');
      await staticPage.locator('details.study-mobile-contents > summary').click();
      await visible(staticPage.getByRole('navigation', { name: 'Case study sections' }), 'Native contents disclosure works without JavaScript');
    }
    report.noJavaScript.push(route);
  }
  await staticPage.setViewportSize({ width: 320, height: 844 });
  await staticPage.goto(base.href, { waitUntil: 'domcontentloaded' });
  const staticNavigation = staticPage.getByRole('navigation', { name: 'Main navigation', exact: true });
  await staticNavigation.getByRole('link', { name: 'About', exact: true }).click();
  const staticAboutTop = await staticPage.locator('#about').evaluate(element => element.getBoundingClientRect().top);
  const staticHeaderBottom = await staticNavigation.evaluate(element => element.closest('header').getBoundingClientRect().bottom);
  assert(staticAboutTop >= staticHeaderBottom - 2, 'Wrapped navigation does not cover the selected section without JavaScript at 320px');
  await noScript.close();
  assert.deepEqual(report.errors, [], 'No hydration or browser console errors');
  report.passed = true;
  process.stdout.write(`Design checks passed: ${report.viewports.length} page/viewport combinations, ${report.noJavaScript.length} no-JavaScript journeys, title metadata, keyboard navigation, reduced motion, mobile contents and text reflow.\n`);
} catch (error) {
  report.failure = error.stack;
  await page.screenshot({ path: fileURLToPath(new URL('failure.png', artifacts)), fullPage: true }).catch(() => {});
  throw error;
} finally {
  await writeFile(new URL('checks.json', artifacts), JSON.stringify(report, null, 2));
  await browser.close();
}
