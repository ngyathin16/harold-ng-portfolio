import { Api, date, list, num, object, optionalObject, records, str, text, type Row } from './api';
import { button, el, evidence, field, input, notice, pretty, select, table } from './dom';
import { teamBuilder, queryResults } from './teams';

export interface Workspace {
  api: Api;
  publication: Row;
  dataset: Row;
  params: URLSearchParams;
  navigate: (changes: Record<string, string | null>, replace?: boolean) => void;
  link: (changes: Record<string, string | null>) => string;
  notify: (message: string, error?: boolean) => void;
}
const basePath = '/applications/research/';
export function startWorkspace(): void {
  const root = document.getElementById('research-app');
  const content = document.getElementById('workspace-content');
  const status = document.getElementById('workspace-status');
  if (!(root instanceof HTMLElement) || !content || !status) return;
  let generation = 0;
  let controller = new AbortController();
  let stopPolling: (() => void) | undefined;
  const notify = (message: string, error = false) => status.replaceChildren(notice(message, error));
  function link(changes: Record<string, string | null>): string {
    const params = new URLSearchParams(location.search);
    for (const [key, value] of Object.entries(changes)) value === null ? params.delete(key) : params.set(key, value);
    return basePath + (params.size ? '?' + params.toString() : '');
  }
  function navigate(changes: Record<string, string | null>, replace = false): void {
    history[replace ? 'replaceState' : 'pushState']({}, '', link(changes));
    void render();
  }
  root.addEventListener('click', event => {
    if (!(event.target instanceof Element)) return;
    const anchor = event.target.closest('a');
    if (!anchor || event.defaultPrevented || event instanceof MouseEvent && (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey)) return;
    const url = new URL(anchor.href);
    if (url.origin === location.origin && url.pathname === basePath) { event.preventDefault(); history.pushState({}, '', url); void render(); }
  });
  window.addEventListener('popstate', () => { void render(); });
  async function render(): Promise<void> {
    if (!root || !content || !status) return;
    const run = ++generation;
    controller.abort(); controller = new AbortController(); stopPolling?.(); stopPolling = undefined;
    const signal = controller.signal;
    status.replaceChildren();
    content.setAttribute('aria-busy', 'true');
    if (!content.children.length) content.append(notice('Loading the selected publication…'));
    const params = new URLSearchParams(location.search);
    const requestedView = params.get('view');
    const view = requestedView === 'downloads' ? 'catalog' : requestedView || (params.has('dataset') ? 'catalog' : 'home');
    root.querySelectorAll<HTMLAnchorElement>('[data-view]').forEach(anchor => {
      const current = anchor.dataset.view;
      anchor.href = link({ view: current || 'home', record: null, query: null, page: null });
      if (current === view || current === 'catalog' && ['record', 'tables', 'coverage'].includes(view)) anchor.setAttribute('aria-current', 'page'); else anchor.removeAttribute('aria-current');
    });
    try {
      const api = new Api(root.dataset.api || '');
      const datasets = records((await api.get('/v1/datasets', signal)).datasets);
      if (run !== generation) return;
      if (view === 'home' || !datasets.length) {
        const home = el('section', { class: 'dataset-list' }, el('p', { class: 'eyebrow' }, 'Published research'), el('h2', {}, 'Choose a dataset'));
        if (!datasets.length) home.append(notice('No datasets have been published yet. Check again after the first publication.'));
        datasets.forEach(dataset => {
          const latest = optionalObject(dataset.latest_publication), manifest = optionalObject(latest.manifest);
          home.append(el('article', { class: 'dataset-entry' }, el('div', {}, el('h3', {}, str(dataset.name)), el('p', {}, str(dataset.description)), el('p', { class: 'small' }, str(manifest.channel, 'Channel unknown').toUpperCase() + ' · ' + str(manifest.completeness, 'Coverage unknown').replaceAll('_', ' ') + ' · Published ' + date(latest.published_at)), el('code', {}, str(latest.snapshot_id, text(dataset.latest_publication_id)))), el('a', { class: 'action', href: link({ dataset: str(dataset.id), view: 'catalog', snapshot: null, publication: null, record: null, page: null, query: null, definition: null }) }, 'Open dataset ↗')));
        });
        content.replaceChildren(home); return;
      }
      const dataset = datasets.find(row => row.id === params.get('dataset')) || (!params.has('dataset') ? datasets[0] : undefined);
      if (!dataset) throw new Error('This dataset is unavailable. Return Home to choose a published dataset.');
      const publications = records((await api.get(`/v1/datasets/${encodeURIComponent(str(dataset.id))}/publications`, signal)).publications);
      const publication = params.has('publication') ? publications.find(row => row.id === params.get('publication')) : params.has('snapshot') ? publications.find(row => row.snapshot_id === params.get('snapshot')) : publications.find(row => row.id === dataset.latest_publication_id) || publications[0];
      if (!publication) throw new Error('The selected snapshot is unavailable or has not been published. Your link has been preserved.');
      if (params.has('snapshot') && params.get('snapshot') !== publication.snapshot_id) throw new Error('The publication does not belong to the snapshot in this link.');
      for (const [key, value] of Object.entries({ dataset: str(dataset.id), snapshot: str(publication.snapshot_id), publication: str(publication.id), view })) params.set(key, value);
      history.replaceState({}, '', basePath + '?' + params.toString());
      const ctx: Workspace = { api, dataset, publication, params, navigate, link, notify };
      const wrapper = el('div');
      wrapper.append(publicationHeader(ctx, publications));
      const tabs = el('nav', { class: 'dataset-tabs', 'aria-label': 'Dataset' });
      for (const [id, label] of [['catalog', 'Catalog'], ['tables', 'Tables'], ['coverage', 'Coverage & evidence']]) tabs.append(el('a', { href: link({ view: id, record: null, page: null, sort: null }), ...((id === view || id === 'catalog' && view === 'record') ? { 'aria-current': 'page' } : {}) }, label));
      if (view !== 'teams') wrapper.append(tabs);
      let body: HTMLElement;
      if (view === 'record' || params.has('record') && view === 'catalog') body = await recordPage(ctx, signal);
      else if (view === 'tables') body = await tablesPage(ctx, signal);
      else if (view === 'coverage') body = await coveragePage(ctx, signal);
      else if (view === 'teams' && !list(dataset.capabilities).includes('team_queries')) body = el('section', { class: 'workspace-empty' }, el('h2', {}, 'Team queries are unavailable for this dataset'), el('p', {}, 'Its published records remain available in the explorer.'), el('a', { class: 'action', href: link({ view: 'home', dataset: null, publication: null, snapshot: null, query: null, definition: null }) }, 'Choose a dataset'));
      else if (view === 'teams' && params.has('query')) {
        const result = await queryResults(ctx, signal); body = result.element; stopPolling = result.stop;
      } else if (view === 'teams') body = await teamBuilder(ctx, signal);
      else body = await catalogPage(ctx, signal);
      if (run !== generation) { stopPolling?.(); return; }
      wrapper.append(body); content.replaceChildren(wrapper);
    } catch (error) {
      if (signal.aborted || run !== generation) return;
      const message = error instanceof Error ? error.message : 'The research service is unavailable.';
      const recovery = el('section', { class: 'workspace-empty' }, el('p', { class: 'eyebrow' }, 'Research service'), el('h2', {}, 'Unable to open this view'), notice(message, true), el('div', { class: 'toolbar-actions' }, button('Try again', () => render()), el('a', { href: basePath }, 'Workspace home')));
      if (content.querySelector('.publication-bar')) { notify(message + ' The previous view remains visible until this request succeeds.', true); status.append(button('Retry this view', () => render())); } else content.replaceChildren(recovery);
    } finally { if (run === generation) content.removeAttribute('aria-busy'); }
  }
  void render();
}
function scoped(ctx: Workspace): string { return `/v1/publications/${encodeURIComponent(str(ctx.publication.id))}`; }
function publicationHeader(ctx: Workspace, publications: Row[]): HTMLElement {
  function switchPublication(selected: Row): void {
    const params = new URLSearchParams(location.search);
    let definition = params.get('definition');
    if (definition || params.get('view') === 'teams') {
      try {
        const transferred = object(JSON.parse(definition || '{}'));
        transferred.dataset_id = ctx.dataset.id;
        transferred.snapshot_id = selected.snapshot_id;
        transferred.publication_id = selected.id;
        transferred.previous_publication_id = ctx.publication.id;
        delete transferred.ruleset_version;
        delete transferred.solver_version;
        definition = JSON.stringify(transferred);
      } catch { ctx.notify('The saved query definition is invalid. The selected snapshot has not changed.', true); return; }
    }
    ctx.navigate({ publication: str(selected.id), snapshot: str(selected.snapshot_id), page: null, query: null, definition });
  }
  const manifest = optionalObject(ctx.publication.manifest);
  const choice = select('publication', publications.map(row => [str(row.id), str(row.snapshot_id) + ' · ' + str(row.id).slice(-8)]), str(ctx.publication.id));
  choice.addEventListener('change', () => { const selected = publications.find(row => row.id === choice.value); if (selected) switchPublication(selected); });
  const scope = optionalObject(ctx.publication.scope);
  const metadata = el('div', {}, el('h2', {}, str(ctx.dataset.name)), el('code', {}, str(ctx.publication.snapshot_id)), el('div', { class: 'publication-meta' }, el('span', { class: 'badge' }, str(manifest.channel, 'Channel unknown').toUpperCase()), el('span', { class: 'badge' }, str(manifest.completeness, 'Status unknown').replaceAll('_', ' ')), el('span', {}, 'Build: ' + text(manifest.build)), el('span', {}, manifest.exact_client_build === true ? 'Exact client build' : 'Mixed or uncertain build identity')), el('p', { class: 'small muted' }, 'Published ' + date(ctx.publication.published_at)), el('p', { class: 'small muted' }, 'Publication ' + str(ctx.publication.id)), el('p', { class: 'small muted' }, 'Scope: ' + str(scope.description, text(ctx.publication.scope))));
  if (manifest.completeness === 'validated_with_gaps') metadata.append(el('p', { class: 'small muted' }, 'Validation checks passed; known information gaps remain.'));
  const check = button('Check for updates', async () => {
    try {
      const data = records((await ctx.api.get(`/v1/datasets/${encodeURIComponent(str(ctx.dataset.id))}/publications`)).publications);
      const latest = data.find(row => row.id !== ctx.publication.id && str(row.published_at) > str(ctx.publication.published_at));
      if (!latest) ctx.notify('You are viewing the newest available publication.');
      else { const status = document.getElementById('workspace-status'); status?.replaceChildren(notice('A newer publication is available. Your current view remains pinned.'), button('Open ' + str(latest.snapshot_id), () => switchPublication(latest))); }
    } catch (error) { ctx.notify(error instanceof Error ? error.message : 'Update check failed. Your snapshot is unchanged.', true); }
  });
  return el('div', { class: 'publication-bar' }, metadata, el('div', { class: 'publication-choice' }, field('Snapshot', choice), el('div', { class: 'toolbar' }, check)));
}
function pager(ctx: Workspace, result: Row): HTMLElement {
  const page = num(result.page, 1), size = num(result.page_size, 50), total = num(result.total);
  const previous = button('← Previous', () => ctx.navigate({ page: String(page - 1) })); previous.disabled = page <= 1;
  const next = button('Next →', () => ctx.navigate({ page: String(page + 1) })); next.disabled = page * size >= total;
  return el('div', { class: 'pagination' }, el('span', {}, total ? `${((page - 1) * size + 1).toLocaleString()}–${Math.min(total, page * size).toLocaleString()} of ${total.toLocaleString()}` : '0 results'), el('div', {}, previous, next));
}
async function catalogPage(ctx: Workspace, signal: AbortSignal): Promise<HTMLElement> {
  const params = new URLSearchParams();
  for (const key of ['q', 'kind', 'cost', 'trait', 'shop_only', 'locked', 'item_category', 'rarity', 'sort', 'page']) if (ctx.params.has(key)) params.set(key, ctx.params.get(key) || '');
  const result = await ctx.api.get(scoped(ctx) + '/catalog?' + params, signal);
  const form = el('form', { class: 'toolbar', role: 'search' });
  const search = input('q', ctx.params.get('q') || '', 'search'); search.placeholder = 'Name, alias, identifier, or description';
  form.append(el('label', { class: 'search-field' }, 'Search this snapshot', search));
  const kinds = list(result.kinds).map(value => str(value));
  form.append(field('Category', select('kind', [['', 'All categories'], ...kinds.map(value => [value, value.replaceAll('_', ' ')] satisfies [string, string])], ctx.params.get('kind') || '')));
  form.append(field('Sort', select('sort', [['name', 'Name'], ['cost', 'Cost'], ['kind', 'Category']], ctx.params.get('sort') || 'name')));
  const submit = el('button', { type: 'submit', class: 'primary' }, 'Search'); form.append(submit);
  const filters = el('details', {}, el('summary', {}, 'Focused filters'));
  const filterRow = el('div', { class: 'toolbar' });
  filterRow.append(field('Unit cost', input('cost', ctx.params.get('cost') || '', 'number')), field('Trait identifier', input('trait', ctx.params.get('trait') || '')), field('Shop eligibility', select('shop_only', [['', 'All records'], ['true', 'Shop units only']], ctx.params.get('shop_only') || '')), field('Unlock status', select('locked', [['', 'Any'], ['true', 'Locked'], ['false', 'Unlocked']], ctx.params.get('locked') || '')), field('Item category', input('item_category', ctx.params.get('item_category') || '')), field('Augment rarity', input('rarity', ctx.params.get('rarity') || '')));
  filters.append(filterRow); form.append(filters);
  form.addEventListener('submit', event => { event.preventDefault(); const changes: Record<string, string | null> = { page: null }; new FormData(form).forEach((value, key) => { changes[key] = String(value) || null; }); ctx.navigate(changes); });
  const rows = records(result.rows);
  const section = el('section', {}, form);
  if (!rows.length) section.append(notice('No records match these filters.'), button('Reset filters', () => ctx.navigate({ q: null, kind: null, cost: null, trait: null, shop_only: null, locked: null, item_category: null, rarity: null, page: null })));
  else section.append(table(rows, ['name', 'kind', 'description', 'lifecycle_status'], (row, key) => key === 'name' ? el('a', { href: ctx.link({ record: str(row.id ?? row.entity_id), view: 'record', page: null }) }, str(row.name)) : pretty(row[key])));
  section.append(pager(ctx, result)); return section;
}
async function recordPage(ctx: Workspace, signal: AbortSignal): Promise<HTMLElement> {
  const id = ctx.params.get('record') || '';
  const data = await ctx.api.get(scoped(ctx) + '/records/' + encodeURIComponent(id), signal);
  const record = object(data.record);
  const section = el('section', {}, el('a', { class: 'back-link', href: ctx.link({ view: 'catalog', record: null }) }, '← Catalog'));
  async function copy(citation = false): Promise<void> {
    const sources = records(data.evidence).map(row => str(row.url)).filter(Boolean);
    const value = citation ? `${str(ctx.dataset.name)}. Snapshot ${str(ctx.publication.snapshot_id)}. Publication ${str(ctx.publication.id)}. Record ${id}. ${[...new Set(sources)].join(' ')}` : new URL(ctx.link({ view: 'record', record: id }), 'https://haroldng.dev').href;
    try { await navigator.clipboard.writeText(value); ctx.notify(citation ? 'Citation copied.' : 'Record link copied.'); } catch { ctx.notify('Clipboard unavailable. Copy this text: ' + value); }
  }
  section.append(el('div', { class: 'record-heading' }, el('div', {}, el('p', { class: 'eyebrow' }, str(record.kind)), el('h2', {}, str(record.name)), el('code', {}, id)), el('div', { class: 'toolbar-actions' }, button('Copy record link', () => copy()), button('Copy citation', () => copy(true)))));
  section.append(el('p', { class: 'record-description' }, text(record.description)));
  const fields = el('dl', { class: 'record-fields' });
  for (const key of ['lifecycle_status', 'variant_key', 'channel', 'mode', 'evidence_origin', 'corroboration_status']) if (key in record) fields.append(el('div', {}, el('dt', {}, key.replaceAll('_', ' ')), el('dd', {}, text(record[key]))));
  section.append(fields);
  const sections = optionalObject(data.sections);
  for (const [name, value] of Object.entries(sections)) if (Array.isArray(value) && value.length) section.append(el('h3', {}, name.replaceAll('_', ' ')), table(records(value)));
  if (list(data.relationships).length) {
    section.append(el('h3', {}, 'Stored relationships'));
    for (const row of records(data.relationships)) {
      const target = str(row.target_entity_id ?? row.target_id);
      section.append(el('p', { class: 'small' }, text(row.relationship ?? row.relation) + ' → ', target ? el('a', { href: ctx.link({ record: target, view: 'record' }) }, str(row.target_name, target)) : el('span', {}, 'Unresolved reference')));
    }
  }
  section.append(el('h3', {}, 'Known gaps'), list(data.gaps).length ? table(records(data.gaps)) : el('p', { class: 'small muted' }, 'No field gaps recorded for this record. This does not establish completeness.'), el('h3', {}, 'Evidence'), evidence(records(data.evidence)), el('details', {}, el('summary', {}, 'Original structured record'), el('pre', {}, text(record))));
  if (data.source) section.append(el('h3', {}, 'Record citation'), evidence([object(data.source)]));
  if (num(data.evidence_total) > list(data.evidence).length) section.append(el('a', { class: 'action', href: ctx.link({ view: 'tables', table: 'v_evidence', column: 'entity_id', op: 'eq', value: id, record: null, page: null, sort: null }) }, `Browse all ${num(data.evidence_total)} evidence rows`));
  if (num(data.gaps_total) > list(data.gaps).length) section.append(el('a', { class: 'action', href: ctx.link({ view: 'tables', table: 'field_gaps', column: 'entity_id', op: 'eq', value: id, record: null, page: null, sort: null }) }, `Browse all ${num(data.gaps_total)} field gaps`));
  return section;
}
const explanations: [string, string][] = [
  ['loot', 'Loot rows form reward trees. Parent selection rules, stage conditions, and unknown probabilities affect outcomes; descendants are not independent probabilities.'],
  ['observation', 'Samples count observed boards. Composition groups, unclassified boards, and population totals are separate. Unreported dates, region, and build remain unknown. A requested window is not a verified collection interval.'],
  ['recommend', 'Recommendations preserve their own evidence and time window; they do not establish causal performance.'],
  ['curve', 'Only reported coordinates are shown. Missing values are not interpolated, and a coordinate is not necessarily a star level.'],
  ['change', 'These are dataset changes. Parser corrections and new evidence do not establish changes to the game.'],
];
async function tablesPage(ctx: Workspace, signal: AbortSignal): Promise<HTMLElement> {
  const tables = records((await ctx.api.get(scoped(ctx) + '/tables', signal)).tables);
  const chosen = ctx.params.get('table') || str(tables[0]?.name);
  const definition = tables.find(row => row.name === chosen);
  if (!definition) throw new Error('This table is missing or incompatible with the selected publication.');
  const choice = select('table', tables.map(row => [str(row.name), `${str(row.name)} · ${num(row.row_count).toLocaleString()} rows`]), chosen);
  choice.addEventListener('change', () => ctx.navigate({ table: choice.value, page: null, filters: null, column: null, sort: null }));
  const columns = list(definition.columns).map(value => optionalObject(value));
  const form = el('form', { class: 'toolbar' }, field('Table or research view', choice));
  const sort = select('sort', [['', 'Stable default order'], ...columns.map(row => [str(row.name), `${str(row.name)} (${str(row.type)})`] satisfies [string, string])], ctx.params.get('sort') || '');
  form.append(field('Sort column', sort), field('Direction', select('direction', [['asc', 'Ascending'], ['desc', 'Descending']], ctx.params.get('direction') || 'asc')));
  const col = select('column', [['', 'No filter'], ...columns.map(row => [str(row.name), str(row.name)] satisfies [string, string])], ctx.params.get('column') || '');
  form.append(field('Filter column', col), field('Comparison', select('op', [['eq', 'Equals'], ['contains', 'Contains text'], ['gte', 'At least'], ['lte', 'At most'], ['is_null', 'Is unknown (null)']], ctx.params.get('op') || 'eq')), field('Value', input('value', ctx.params.get('value') || '')), el('button', { type: 'submit', class: 'primary' }, 'Apply'));
  form.addEventListener('submit', event => { event.preventDefault(); const changes: Record<string, string | null> = { page: null }; new FormData(form).forEach((value, key) => { changes[key] = String(value) || null; }); ctx.navigate(changes); });
  const parameters = new URLSearchParams({ page: ctx.params.get('page') || '1', page_size: '50', direction: ctx.params.get('direction') || 'asc' });
  if (ctx.params.get('sort')) parameters.set('sort', ctx.params.get('sort') || '');
  if (ctx.params.get('column')) {
    let value: string | number | boolean = ctx.params.get('value') || '';
    const type = str(columns.find(row => row.name === ctx.params.get('column'))?.type).toLowerCase();
    if (/int|real|float|numeric|double/.test(type) && value !== '') value = Number(value);
    if (/bool/.test(type)) value = value === 'true';
    parameters.set('filters', JSON.stringify([{ column: ctx.params.get('column'), op: ctx.params.get('op') || 'eq', value }]));
  }
  const data = await ctx.api.get(scoped(ctx) + '/tables/' + encodeURIComponent(chosen) + '?' + parameters, signal);
  const section = el('section', {}, form, el('p', { class: 'small muted' }, 'Filters and sorting apply to the complete table before pagination. Open a value to inspect its full contents.'));
  for (const [pattern, explanation] of explanations) if (chosen.includes(pattern)) section.append(notice(explanation));
  const pageRows = records(data.rows);
  const displayRows = pageRows.map((row, index) => ({ inspect_row: index, ...row }));
  section.append(table(displayRows, undefined, (row, column) => {
    if (column !== 'inspect_row') return pretty(row[column]);
    const container = el('details', {}, el('summary', {}, 'Full row'));
    container.addEventListener('toggle', async () => {
      if (!container.open || container.dataset.loaded) return;
      container.dataset.loaded = 'loading';
      const full = new URLSearchParams(parameters);
      full.set('page_size', '1'); full.set('page', String((num(data.page, 1) - 1) * num(data.page_size, 50) + num(row.inspect_row) + 1)); full.set('full', 'true');
      try {
        const detail = await ctx.api.get(scoped(ctx) + '/tables/' + encodeURIComponent(chosen) + '?' + full);
        container.append(el('pre', {}, text(records(detail.rows)[0]))); container.dataset.loaded = 'true';
      } catch (error) { delete container.dataset.loaded; container.append(notice(error instanceof Error ? error.message : 'Row unavailable; close and reopen to retry.', true)); }
    });
    return container;
  }), pager(ctx, data), el('details', {}, el('summary', {}, 'Column types and keys'), table(columns), pretty(definition.primary_key)));
  return section;
}
async function coveragePage(ctx: Workspace, signal: AbortSignal): Promise<HTMLElement> {
  const data = await ctx.api.get(scoped(ctx) + '/coverage', signal);
  const section = el('section', {}, el('h2', {}, 'Coverage & evidence'), notice('Unknown expected inventory has no coverage percentage. Archived evidence is a retained historical source, not a fetch failure.'));
  for (const [key, label, tableName] of [['coverage', 'Category coverage', 'coverage_entries'], ['discrepancies', 'Conflicting claims', 'discrepancies'], ['source_health', 'Source health', 'source_documents']]) {
    section.append(el('h3', {}, label), table(records(data[key])), el('a', { class: 'text-link', href: ctx.link({ view: 'tables', table: tableName, page: null, column: null, sort: null }) }, 'Browse the complete table →'));
  }
  section.append(el('h3', {}, 'All field gaps'), pretty(data.issue_counts), el('a', { class: 'action', href: ctx.link({ view: 'tables', table: 'field_gaps', page: null, column: null, sort: null }) }, 'Browse every field gap'), el('h3', {}, 'Published validation'), pretty(data.validation)); return section;
}
