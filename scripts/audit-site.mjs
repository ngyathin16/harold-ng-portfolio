import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { availableParallelism, cpus, platform } from 'node:os';
import lighthouse from 'lighthouse';
import { launch } from 'chrome-launcher';

const base = new URL(process.env.BASE_URL || process.env.TEST_BASE_URL || 'http://127.0.0.1:4321');
const runs = Number(process.env.AUDIT_RUNS || 3);
assert(Number.isSafeInteger(runs) && runs > 0 && runs <= 10, 'AUDIT_RUNS must be 1–10');
const artifacts = new URL('../artifacts/lighthouse/', import.meta.url);
await mkdir(artifacts, { recursive: true });
const browser = await launch({
  chromePath: process.env.BROWSER_EXECUTABLE || (process.platform === 'win32' ? 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' : undefined),
  chromeFlags: ['--headless=new', '--no-first-run', '--disable-extensions'],
  logLevel: 'silent',
});
const results = [];
const summary = {
  date: new Date().toISOString(),
  url: base.href,
  commit: process.env.GITHUB_SHA || null,
  deploymentVersion: process.env.DEPLOYMENT_VERSION || null,
  location: process.env.TEST_LOCATION || 'Not recorded',
  host: { platform: platform(), logicalProcessors: availableParallelism(), cpu: cpus()[0].model },
  requestedRuns: runs,
  results,
  passed: false,
  note: `${['localhost', '127.0.0.1', '[::1]'].includes(base.hostname) ? 'Local preview; no hosted-network claim.' : 'Hosted origin accessed from the recorded test runner; no real-user performance claim.'} Simulated mobile throttling; storage cleared per navigation.`,
};
const median = (values) => {
  const sorted = [...values].sort((a, b) => a - b);
  return (sorted[Math.floor((sorted.length - 1) / 2)] + sorted[Math.floor(sorted.length / 2)]) / 2;
};
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
    await writeFile(new URL(`mobile-${run}.json`, artifacts), result.report[0]);
    await writeFile(new URL(`mobile-${run}.html`, artifacts), result.report[1]);
    Object.assign(summary, { lighthouseVersion: lhr.lighthouseVersion, browser: lhr.environment, settings: lhr.configSettings });
    assert(!lhr.runtimeError, lhr.runtimeError?.message);
    const failures = lhr.audits['network-requests'].details.items.filter((request) => request.statusCode >= 400);
    assert.equal(failures.length, 0, 'No failed network requests');
    assert(lhr.audits['network-requests'].details.items.some((request) => request.resourceType === 'Document' && request.transferSize > 0 && request.statusCode === 200), 'A successful page document was transferred');
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
    await writeFile(new URL('summary.json', artifacts), JSON.stringify(summary, null, 2));
  }
  summary.median = Object.fromEntries(['performance', 'accessibility'].map((key) => [key, median(results.map((result) => result[key]))]));
  summary.range = Object.fromEntries(['performance', 'accessibility'].map((key) => [key, [Math.min(...results.map((result) => result[key])), Math.max(...results.map((result) => result[key]))]]));
  assert(summary.median.performance >= 90 && summary.median.accessibility >= 90, 'Median missed the 90/90 target; inspect artifacts/lighthouse');
  summary.passed = true;
  process.stdout.write(`Median: ${JSON.stringify(summary.median)}\n`);
} catch (error) {
  summary.failure = error.stack;
  throw error;
} finally {
  await writeFile(new URL('summary.json', artifacts), JSON.stringify(summary, null, 2));
  browser.kill();
}
