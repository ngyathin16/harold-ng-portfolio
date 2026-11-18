import assert from 'node:assert/strict';
import { chromium, firefox, webkit } from 'playwright';

const base = process.env.BASE_URL || process.env.TEST_BASE_URL || 'http://127.0.0.1:4321';
const engine = process.env.BROWSER_ENGINE || 'chromium';
const browsers = { chromium, firefox, webkit };
assert(Object.hasOwn(browsers, engine), 'BROWSER_ENGINE must be chromium, firefox or webkit');
const browser = await browsers[engine].launch({
  headless: true,
  ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE }
    : engine === 'chromium' && process.platform === 'win32' ? { channel: 'msedge' } : {}),
});
const page = await browser.newPage({ reducedMotion: 'no-preference' });
const errors = [];
page.on('pageerror', error => errors.push(error.message));

async function transition(section, { retarget, reducedMotion = false } = {}) {
  const result = await page.evaluate(async ({ section, retarget }) => {
    const nav = document.querySelector('nav[aria-label="Main navigation"]');
    const pill = nav.querySelector('.tubelight-pill');
    const link = name => nav.querySelector(`a[href="/#${name}"]`);
    const geometry = element => {
      const rect = element.getBoundingClientRect();
      const origin = nav.getBoundingClientRect();
      return { center: rect.left + rect.width / 2 - origin.left, width: rect.width, height: rect.height };
    };
    const initial = geometry(pill);
    const firstTarget = geometry(link(section));
    const frames = [];
    let destination = section;
    let retargetAt = null;
    let stableFrames = 0;
    let previousScroll = scrollY;
    const start = performance.now();
    link(section).click();
    await new Promise(resolve => {
      const sample = now => {
        const position = geometry(pill);
        const active = nav.querySelector('[aria-current="location"]')?.getAttribute('href');
        frames.push({ ...position, active, elapsed: now - start });
        if (retarget && retargetAt === null && Math.abs(position.center - initial.center) > 1
          && Math.abs(position.center - firstTarget.center) > 1) {
          retargetAt = frames.length - 1;
          destination = retarget;
          link(retarget).click();
        }
        const target = geometry(link(destination));
        stableFrames = Math.abs(scrollY - previousScroll) < 0.1
          && Math.abs(position.center - target.center) < 0.5 ? stableFrames + 1 : 0;
        previousScroll = scrollY;
        if ((now - start > 500 && stableFrames >= 8) || now - start > 3000) resolve();
        else requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
    });
    return { initial, firstTarget, target: geometry(link(destination)), frames, retargetAt };
  }, { section, retarget });

  const label = `${page.viewportSize().width}px ${section}${retarget ? ` → ${retarget}` : ''}`;
  assert(result.frames.length > 2, `${label}: animation frames captured`);
  if (retarget) assert.notEqual(result.retargetAt, null, `${label}: retarget occurred during visible motion`);
  const groups = retarget ? [
    { initial: result.initial, target: result.firstTarget, frames: result.frames.slice(0, result.retargetAt + 1), section },
    { initial: result.frames[result.retargetAt], target: result.target, frames: result.frames.slice(result.retargetAt + 1), section: retarget },
  ] : [{ initial: result.initial, target: result.target, frames: result.frames, section }];
  for (const group of groups) {
    let previous = group.initial.center;
    const direction = Math.sign(group.target.center - previous);
    for (const frame of group.frames) {
      assert.equal(frame.active, `/#${group.section}`, `${label}: selected section changed during scrolling at ${Math.round(frame.elapsed)}ms`);
      assert(frame.width > 0 && frame.height > 0, `${label}: highlight remains visible`);
      assert(direction * (frame.center - previous) >= -0.75, `${label}: highlight reversed direction at ${Math.round(frame.elapsed)}ms`);
      assert(frame.center >= Math.min(group.initial.center, group.target.center) - 0.75
        && frame.center <= Math.max(group.initial.center, group.target.center) + 0.75,
      `${label}: highlight jumped outside its path at ${Math.round(frame.elapsed)}ms`);
      previous = frame.center;
    }
  }
  const final = result.frames.at(-1);
  assert(Math.abs(final.center - result.target.center) < 0.75 && Math.abs(final.width - result.target.width) < 0.75,
    `${label}: highlight finishes aligned with the selected link`);
  assert.equal(new URL(page.url()).hash, `#${retarget || section}`, `${label}: native anchor navigation remains intact`);
  if (!reducedMotion && !retarget) {
    assert(result.frames.some(frame => Math.abs(frame.center - result.initial.center) > 1
      && Math.abs(frame.center - result.target.center) > 1), `${label}: highlight travels between links instead of jumping`);
  }
  if (reducedMotion) assert(result.frames.every(frame => Math.abs(frame.center - result.target.center) < 0.75),
    `${label}: reduced motion reaches the selected link immediately`);
}

try {
  for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(base);
    await page.evaluate(() => document.fonts.ready);
    await page.locator('.tubelight-nav[data-ready="true"]').waitFor();
    for (const section of ['contact', 'about', 'work', 'about', 'contact', 'work']) await transition(section);
    await transition('contact', { retarget: 'work' });

    await transition('contact');
    await page.evaluate(() => scrollTo({ top: 0, behavior: 'instant' }));
    await page.waitForFunction(() => document.querySelector('nav a[href="/#work"]')?.getAttribute('aria-current') === 'location');
    await page.waitForTimeout(250);
    await transition('about');

    await page.emulateMedia({ reducedMotion: 'reduce' });
    await transition('contact', { reducedMotion: true });
    await transition('work', { reducedMotion: true });
    await page.emulateMedia({ reducedMotion: 'no-preference' });
  }
  assert.deepEqual(errors, [], 'Navigation has no browser errors');
  console.log(`Passed ${engine}: smooth navigation across all tab pairs at mobile/desktop widths, mid-animation retargeting, manual-scroll recovery and reduced motion.`);
} finally {
  await browser.close();
}
