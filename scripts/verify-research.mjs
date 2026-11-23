import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';
import AxeBuilder from '@axe-core/playwright';

const base = process.env.BASE_URL || 'http://127.0.0.1:4330';
const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });
const context = await browser.newContext({ reducedMotion: 'reduce' });
const page = await context.newPage();
const report = { checked_at: new Date().toISOString(), base, checks: [], errors: [], passed: false };
page.on('pageerror', error => report.errors.push(error.message));
const ready = () => page.locator('#workspace-content[aria-busy]').waitFor({ state: 'detached' });
await mkdir('artifacts/research', { recursive: true });
try {
  await page.goto(base + '/applications/');
  await page.getByRole('link', { name: /Research workspace/ }).click();
  await page.getByRole('link', { name: 'Open dataset' }).first().click(); await ready();
  assert(new URL(page.url()).searchParams.get('snapshot'));
  assert(new URL(page.url()).searchParams.get('publication'));
  await page.getByLabel('Search this snapshot').fill('Ahri');
  await page.getByLabel('Category', { exact: true }).selectOption('unit');
  await page.getByRole('button', { name: 'Search', exact: true }).click(); await ready();
  const catalogUrl = page.url();
  await page.getByRole('link', { name: 'Ahri', exact: true }).click(); await ready();
  assert(await page.getByRole('heading', { name: 'Evidence', exact: true }).isVisible());
  const recordUrl = page.url(); await page.reload(); await ready(); assert.equal(page.url(), recordUrl);
  await page.goBack(); await ready(); assert.equal(page.url(), catalogUrl);
  assert.equal(await page.getByLabel('Search this snapshot').inputValue(), 'Ahri');
  report.checks.push('catalog search, concrete version URL, unit evidence, reload and Back');
  await page.getByRole('link', { name: 'Tables', exact: true }).click(); await ready();
  await page.getByLabel('Table or research view').selectOption('field_gaps'); await ready();
  const gapsUrl = new URL(page.url()); gapsUrl.searchParams.set('page', '5');
  await page.goto(gapsUrl.href); await ready();
  assert.match(await page.locator('.pagination').innerText(), /201/);
  await page.getByText('Full row', { exact: true }).first().click();
  await page.locator('details[data-loaded="true"]').first().waitFor();
  report.checks.push('table rows beyond 200, lazy full row, stable pagination');
  await page.getByRole('link', { name: 'Coverage & evidence', exact: true }).click(); await ready();
  assert(await page.getByRole('heading', { name: 'Published validation' }).isVisible());
  assert.equal(await page.locator('#research-app').getByRole('link', { name: /download/i }).count(), 0);
  const oldDownloadUrl = new URL(page.url());
  oldDownloadUrl.searchParams.set('view', 'downloads');
  await page.goto(oldDownloadUrl.href); await ready();
  assert.equal(new URL(page.url()).searchParams.get('view'), 'catalog');
  assert(await page.getByLabel('Search this snapshot').isVisible());
  const retiredApi = await page.locator('#research-app').getAttribute('data-api');
  const retainedPublication = encodeURIComponent(oldDownloadUrl.searchParams.get('publication'));
  for (const method of ['GET', 'HEAD']) {
    for (const path of [`/v1/publications/${retainedPublication}/downloads`, `/v1/publications/${retainedPublication}/downloads/database.sqlite.gz`, '/v1/queries/retained/export?format=json', '/v1/queries/retained/export?format=csv']) {
      const response = await page.request.fetch(retiredApi + path, { method, maxRedirects: 0 });
      assert.equal(response.status(), 404, `${method} ${path}`);
      assert.equal(response.headers()['location'], undefined);
    }
  }
  report.checks.push('coverage and validation; old download view resolves to catalog; retired GET/HEAD routes return no artifacts or redirects');
  await page.getByRole('link', { name: 'Team queries', exact: true }).click(); await ready();
  await page.getByRole('button', { name: 'Use Jhin example' }).click();
  assert.equal(await page.getByLabel('Minimum distinct champions').inputValue(), '8');
  assert.equal(await page.getByLabel('Maximum per champion (optional)').inputValue(), '4');
  await page.getByRole('button', { name: 'Add a required trait' }).click();
  await page.getByLabel('Required trait', { exact: true }).selectOption({ label: 'Jazz' });
  await page.reload(); await ready();
  assert.equal(await page.getByLabel('Required trait', { exact: true }).inputValue(), 'tft19:trait:DA_Jazz19');
  await page.getByRole('button', { name: 'Remove', exact: true }).click();
  await page.reload(); await ready();
  assert.equal(await page.getByLabel('Required trait', { exact: true }).count(), 0);
  await page.getByLabel('Exclude champions and exclusive forms').selectOption({ label: 'Jhin' });
  assert(await page.getByRole('button', { name: 'Find matching teams' }).isDisabled());
  await page.getByRole('button', { name: 'Use Jhin example' }).click();
  report.checks.push('runtime Jhin preset, generic Jazz breakpoint, URL persistence, contradiction rejection');
  for (const width of [1280, 390, 640]) {
    await page.setViewportSize({ width, height: 900 });
    if (width === 640) await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
    assert.equal(await page.locator('#research-app').getByRole('link', { name: /download/i }).count(), 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `overflow ${width}`);
    const audit = await new AxeBuilder({ page }).include('#research-app').withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
    assert.deepEqual(audit.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) })), [], `accessibility ${width}`);
    await page.screenshot({ path: `artifacts/research/teams-${width}.png`, fullPage: true });
  }
  await page.evaluate(() => { document.documentElement.style.fontSize = ''; });
  await page.keyboard.press('Tab'); assert(await page.evaluate(() => document.activeElement !== document.body));
  report.checks.push('keyboard, 390px, 1280px, enlarged text and WCAG automated scan');
  if (process.env.TEST_COMPLETED_QUERY === '1') {
    await page.getByRole('button', { name: 'Find matching teams' }).click();
    await page.getByRole('heading', { name: 'Matching teams', exact: true }).waitFor({ timeout: 150000 });
    assert(new URL(page.url()).searchParams.get('query'));
    assert.equal(await page.locator('#research-app').getByRole('link', { name: /download/i }).count(), 0);
    assert(await page.locator('.result-entry').count() > 0);
    await page.getByLabel('Sort all results').selectOption('total_cost'); await ready();
    const finishedUrl = page.url(); await page.reload(); await ready();
    assert.equal(page.url(), finishedUrl);
    report.checks.push('submitted query resolves persisted complete results, global sort and reload');
  }
  const before = new URL(page.url());
  await context.setOffline(true);
  await page.getByRole('button', { name: 'Check for updates' }).click();
  await page.getByRole('alert').waitFor();
  assert.equal(new URL(page.url()).searchParams.get('publication'), before.searchParams.get('publication'));
  await context.setOffline(false);
  report.checks.push('network failure preserves version and query form');
  const emptyQueryUrl = new URL(page.url());
  emptyQueryUrl.searchParams.set('view', 'teams');
  emptyQueryUrl.searchParams.set('query', 'completed-empty-fixture');
  await page.route('**/v1/queries/completed-empty-fixture', route => route.fulfill({ json: {
    state: 'complete', complete: true, result_count: 0, champion_set_count: 0,
    query: { publication_id: emptyQueryUrl.searchParams.get('publication'), snapshot_id: emptyQueryUrl.searchParams.get('snapshot') },
    ruleset_version: 'fixture-rules', solver_version: 'fixture-solver', progress: {},
  } }));
  await page.goto(emptyQueryUrl.href); await ready();
  assert.match(await page.locator('#workspace-content').innerText(), /exhaustive search completed with no matches/);
  assert.equal(await page.locator('#research-app').getByRole('link', { name: /download/i }).count(), 0);
  assert.equal(await page.getByRole('button', { name: 'Refresh downloads', exact: true }).count(), 0);
  report.checks.push('completed empty query stays distinct from failure and has no download controls');
  await page.route('**/records/**', route => route.fulfill({ json: {
    record: { id: 'fixture', name: 'Value fixture', kind: 'mechanic', description: '<img src=x onerror="window.sourceExecuted=true">', lifecycle_status: 'unresolved' },
    sections: { values: [{ unknown: null, zero: 0, disabled: false, blank: '' }] },
    evidence: [{ source_id: 'fixture-source', status: 'archived', url: 'https://example.invalid/evidence', source_path: '$.value', value_json: '0' }], gaps: [], relationships: [],
  } }));
  await page.goto(recordUrl); await ready();
  assert.equal(await page.evaluate(() => window.sourceExecuted), undefined);
  assert.equal(await page.locator('#research-app img').count(), 0);
  assert(await page.getByRole('cell', { name: 'Unknown', exact: true }).isVisible());
  assert(await page.getByRole('cell', { name: '0', exact: true }).isVisible());
  assert(await page.getByRole('cell', { name: 'false', exact: true }).isVisible());
  assert(await page.getByRole('cell', { name: 'Empty text', exact: true }).isVisible());
  assert.match(await page.locator('.evidence-entry').innerText(), /archived/);
  report.checks.push('untrusted source text stays inert; null, zero, false, empty text and archived evidence stay distinct');
  const transferUrl = new URL(recordUrl);
  const apiBase = await page.locator('#research-app').getAttribute('data-api');
  const publicationPath = `/v1/datasets/${encodeURIComponent(transferUrl.searchParams.get('dataset'))}/publications`;
  const published = await (await page.request.get(apiBase + publicationPath)).json();
  const originalPublication = published.publications.find(row => row.id === transferUrl.searchParams.get('publication'));
  const nextPublication = { ...originalPublication, id: 'fixture-next-publication', snapshot_id: 'fixture-next-snapshot', published_at: '2099-01-01T00:00:00Z' };
  const fixtureUnit = id => ({ form_id: id, unit_id: id, champion_id: id, name: id === 'fixture-keep' ? 'Keep' : 'Same name', cost: 2, slots: 1, locked: false });
  const fixtureTrait = id => ({ trait_id: 'fixture-trait', name: 'Same trait', breakpoints: [{ breakpoint_id: id, status: 'supported', minimum: 2 }] });
  const rulesRequests = [];
  await page.route('**' + publicationPath, route => route.fulfill({ json: { publications: [originalPublication, nextPublication] } }));
  await page.route(`**/v1/publications/${encodeURIComponent(originalPublication.id)}/rules*`, route => {
    rulesRequests.push(new URL(route.request().url()).searchParams.get('version'));
    return route.fulfill({ json: { version: 'fixture-old-rules', units: [fixtureUnit('fixture-keep'), fixtureUnit('fixture-gone')], traits: [fixtureTrait('fixture-old-tier')], assumptions: [] } });
  });
  await page.route('**/v1/publications/fixture-next-publication/rules*', route => {
    assert.equal(new URL(route.request().url()).searchParams.has('version'), false);
    return route.fulfill({ json: { version: 'fixture-new-rules', units: [fixtureUnit('fixture-keep'), fixtureUnit('fixture-replacement')], traits: [fixtureTrait('fixture-new-tier')], assumptions: [] } });
  });
  const savedTransfer = { dataset_id: transferUrl.searchParams.get('dataset'), snapshot_id: originalPublication.snapshot_id, publication_id: originalPublication.id, ruleset_version: 'fixture-old-rules', team_size: { min: 2, max: 5 }, board_slots: 7, cost: { max: 4 }, total_cost: { max: 17 }, min_active_traits: 1, include: ['fixture-keep', 'fixture-gone'], required_traits: [{ trait_id: 'fixture-trait', breakpoint_id: 'fixture-old-tier' }] };
  transferUrl.searchParams.set('view', 'teams'); transferUrl.searchParams.delete('record'); transferUrl.searchParams.delete('query');
  transferUrl.searchParams.set('definition', JSON.stringify(savedTransfer));
  await page.goto(transferUrl.href); await ready();
  assert.equal(rulesRequests.at(-1), 'fixture-old-rules');
  await page.getByLabel('Maximum total cost (optional)').fill('18');
  await page.getByLabel('Snapshot', { exact: true }).selectOption(nextPublication.id); await ready();
  assert.equal(await page.getByLabel('Maximum total cost (optional)').inputValue(), '18');
  assert.equal(await page.getByLabel('Maximum distinct champions').inputValue(), '5');
  assert.equal(await page.getByLabel('Maximum occupied slots').inputValue(), '7');
  assert.deepEqual(await page.getByLabel('Include every selected form').evaluate(control => [...control.selectedOptions].map(option => option.value)), ['fixture-keep']);
  assert.equal(await page.getByLabel('Required trait', { exact: true }).count(), 0);
  assert.match(await page.locator('#workspace-content').innerText(), /Removed selections unavailable.*fixture-gone.*fixture-old-tier/);
  assert.equal(JSON.parse(new URL(page.url()).searchParams.get('definition')).ruleset_version, 'fixture-new-rules');
  await page.goto(transferUrl.href); await ready();
  assert.equal(rulesRequests.at(-1), 'fixture-old-rules');
  await page.getByRole('button', { name: 'Check for updates' }).click();
  await page.getByRole('button', { name: 'Open fixture-next-snapshot' }).click(); await ready();
  assert.equal(new URL(page.url()).searchParams.get('publication'), nextPublication.id);
  assert.match(await page.locator('#workspace-content').innerText(), /This query now uses ruleset fixture-new-rules/);
  const mismatchedUrl = new URL(transferUrl);
  mismatchedUrl.searchParams.set('definition', JSON.stringify({ ...savedTransfer, publication_id: nextPublication.id }));
  const requestsBeforeMismatch = rulesRequests.length;
  await page.goto(mismatchedUrl.href); await ready();
  assert.match(await page.locator('#workspace-content').innerText(), /saved query belongs to a different dataset, snapshot, or publication/);
  assert.equal(rulesRequests.length, requestsBeforeMismatch);
  assert.equal(page.url(), mismatchedUrl.href);
  await page.locator('#research-app').getByRole('link', { name: 'Home', exact: true }).click(); await ready();
  const datasetLink = new URL(await page.getByRole('link', { name: 'Open dataset' }).first().getAttribute('href'), base);
  assert.equal(datasetLink.searchParams.has('definition'), false);
  assert.equal(datasetLink.searchParams.has('query'), false);
  report.checks.push('snapshot transfer fixture retains numeric filters and available identities, reports removals, preserves historical rules, and clears form state when choosing a dataset');
  assert.deepEqual(report.errors, []);
  report.passed = true;
} finally {
  await writeFile('artifacts/research/browser-verification.json', JSON.stringify(report, null, 2));
  await browser.close();
}
process.stdout.write(JSON.stringify(report, null, 2) + '\n');
