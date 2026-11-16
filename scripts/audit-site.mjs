import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { availableParallelism, cpus, platform } from 'node:os';
import lighthouse from 'lighthouse';
import { launch } from 'chrome-launcher';

// Audit a production preview after npm run build and npm run preview.
const base = new URL(process.env.TEST_BASE_URL || 'http://127.0.0.1:4321');
const runs = Number(process.env.AUDIT_RUNS || 5);
assert(Number.isSafeInteger(runs) && runs > 0 && runs <= 10, 'AUDIT_RUNS must be 1–10');
const artifacts = new URL('../artifacts/lighthouse/', import.meta.url);
await mkdir(artifacts, { recursive: true });
const browser = await launch({
  chromePath: process.env.BROWSER_EXECUTABLE || (process.platform === 'win32' ? 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' : undefined),
  chromeFlags: ['--headless=new', '--no-first-run', '--disable-extensions'],
  logLevel: 'silent',
});
const results = [];
try {
  for (let run = 1; run <= runs; run++) {
    const result = await lighthouse(base.href, {
      port: browser.port,
      output: ['json', 'html'],
      logLevel: 'error',
      onlyCategories: ['performance', 'accessibility'],
      formFactor: 'mobile',
      throttlingMethod: 'simulate',
    });
    assert(result, 'Lighthouse returned a report');
    const { lhr } = result;
    assert(!lhr.runtimeError, lhr.runtimeError?.message);
    const failures = lhr.audits['network-requests'].details.items.filter((request) => request.statusCode >= 400);
    assert.equal(failures.length, 0, 'No failed network requests');
    assert(lhr.audits['network-requests'].details.items.some((request) => request.resourceType === 'Document' && request.transferSize > 0 && request.statusCode === 200), 'A successful page document was transferred');
    await writeFile(new URL(`mobile-${run}.json`, artifacts), result.report[0]);
    await writeFile(new URL(`mobile-${run}.html`, artifacts), result.report[1]);
    const metrics = {
      run,
      performance: Math.round(lhr.categories.performance.score * 100),
      accessibility: Math.round(lhr.categories.accessibility.score * 100),
      firstContentfulPaintMs: lhr.audits['first-contentful-paint'].numericValue,
      largestContentfulPaintMs: lhr.audits['largest-contentful-paint'].numericValue,
      totalBlockingTimeMs: lhr.audits['total-blocking-time'].numericValue,
      cumulativeLayoutShift: lhr.audits['cumulative-layout-shift'].numericValue,
      failures,
    };
    results.push(metrics);
    process.stdout.write(`${JSON.stringify(metrics)}\n`);
    await writeFile(new URL('summary.json', artifacts), JSON.stringify({
      date: new Date().toISOString(),
      url: base.href,
      lighthouseVersion: lhr.lighthouseVersion,
      browser: lhr.environment,
      host: { platform: platform(), logicalProcessors: availableParallelism(), cpu: cpus()[0].model },
      settings: lhr.configSettings,
      results,
      note: 'Local production preview; simulated mobile throttling, cleared storage per navigation. No hosted-network or real-user performance claim.',
    }, null, 2));
  }
  assert(results.every(({ performance, accessibility }) => performance >= 90 && accessibility >= 90), 'One or more runs missed the 90/90 target; inspect artifacts/lighthouse');
} finally {
  browser.kill();
}
