import { list, num, object, optionalObject, records, str, text, type Row } from './api';
import { button, el, field, input, notice, pretty, select } from './dom';
import type { Workspace } from './app';

function chosen(control: HTMLSelectElement): string[] { return [...control.selectedOptions].map(option => option.value); }
function integer(control: HTMLInputElement): number { return Number(control.value); }
function optionalInteger(control: HTMLInputElement): number | null { return control.value === '' ? null : Number(control.value); }
export async function teamBuilder(ctx: Workspace, signal: AbortSignal): Promise<HTMLElement> {
  let saved: Row = {};
  if (ctx.params.get('definition')) {
    try { saved = object(JSON.parse(ctx.params.get('definition') || '{}')); } catch { throw new Error('The saved query definition is invalid.'); }
  }
  for (const [key, expected] of Object.entries({ dataset_id: ctx.dataset.id, snapshot_id: ctx.publication.snapshot_id, publication_id: ctx.publication.id })) {
    if (saved[key] !== undefined && saved[key] !== expected) throw new Error('The saved query belongs to a different dataset, snapshot, or publication. Open its original version; the link has been preserved.');
  }
  const rules = await ctx.api.get(`/v1/publications/${encodeURIComponent(str(ctx.publication.id))}/rules` + (saved.ruleset_version ? '?version=' + encodeURIComponent(str(saved.ruleset_version)) : ''), signal);
  const units = records(rules.units), traits = records(rules.traits);
  const version = str(rules.version);
  if (saved.ruleset_version && saved.ruleset_version !== version) throw new Error('This historical ruleset is unavailable. The query has not been changed to a newer ruleset.');
  const previousPublication = str(saved.previous_publication_id);
  const removed: string[] = [];
  if (previousPublication) {
    if (saved.publication_id !== ctx.publication.id || saved.snapshot_id !== ctx.publication.snapshot_id) throw new Error('The transferred query does not belong to the selected snapshot.');
    const availableForms = new Set(units.map(unit => unit.form_id));
    const availableExclusions = new Set(units.flatMap(unit => [unit.form_id, unit.unit_id, unit.champion_id]));
    const availableTraits = new Set(traits.map(trait => trait.trait_id));
    for (const [key, allowed] of [['include', availableForms], ['exclude', availableExclusions], ['forbidden_traits', availableTraits]] satisfies [string, Set<unknown>][]) {
      saved[key] = list(saved[key]).filter(value => {
        if (allowed.has(value)) return true;
        removed.push(`${key.replaceAll('_', ' ')}: ${text(value)}`); return false;
      });
    }
    saved.required_traits = records(saved.required_traits || []).filter(requirement => {
      const trait = traits.find(candidate => candidate.trait_id === requirement.trait_id);
      if (records(trait?.breakpoints || []).some(point => point.breakpoint_id === requirement.breakpoint_id && point.status === 'supported')) return true;
      removed.push(`required trait: ${text(requirement.trait_id)} / ${text(requirement.breakpoint_id)}`); return false;
    });
  }
  const section = el('section', {}, el('div', { class: 'record-heading' }, el('div', {}, el('h2', {}, 'Find a board that fits'), el('p', { class: 'small muted' }, 'Set your conditions. Search every eligible configuration under a recorded ruleset.'))));
  if (previousPublication) section.append(notice(`Snapshot changed from publication ${previousPublication}. This query now uses ruleset ${version}; review the updated rules before running. Numeric filters and available selections were retained.`), ...(removed.length ? [notice('Removed selections unavailable in this snapshot: ' + removed.join('; '))] : []));
  const form = el('form', { class: 'team-form' });
  const sizes = optionalObject(saved.team_size), costs = optionalObject(saved.cost), totals = optionalObject(saved.total_cost);
  const sizeMin = input('size_min', String(sizes.min ?? 8), 'number'), sizeMax = input('size_max', String(sizes.max ?? 8), 'number');
  for (const control of [sizeMin, sizeMax]) { control.min = '1'; control.max = '8'; control.required = true; }
  const slots = input('slots', String(saved.board_slots ?? 8), 'number'); slots.min = '1'; slots.max = '16'; slots.required = true;
  const active = input('active', String(saved.min_active_traits ?? 0), 'number'); active.min = '0'; active.max = String(traits.length); active.required = true;
  form.append(el('fieldset', {}, el('legend', {}, 'Board size'), field('Minimum distinct champions', sizeMin), field('Maximum distinct champions', sizeMax), field('Maximum occupied slots', slots), field('Minimum proven active traits', active)));
  const include = select('include', units.map(unit => [str(unit.form_id), `${str(unit.name)} · ${text(unit.cost)} gold${unit.locked ? ' · locked' : ''}`])); include.multiple = true;
  const exclude = select('exclude', units.map(unit => [str(unit.form_id), str(unit.name)])); exclude.multiple = true;
  for (const option of include.options) option.selected = list(saved.include).includes(option.value);
  for (const option of exclude.options) {
    const unit = units.find(unit => unit.form_id === option.value);
    option.selected = [option.value, unit?.unit_id, unit?.champion_id].some(value => value !== undefined && list(saved.exclude).includes(value));
  }
  form.append(el('fieldset', {}, el('legend', {}, 'Champion choices'), field('Include every selected form', include), field('Exclude champions and exclusive forms', exclude), el('p', { class: 'small muted full' }, 'Use Ctrl / Command to select multiple choices. Each form belongs to one champion; exclusive forms cannot share a board.')));
  const minCost = input('cost_min', costs.min == null ? '' : String(costs.min), 'number'), maxCost = input('cost_max', costs.max == null ? '' : String(costs.max), 'number');
  const totalMin = input('total_min', totals.min == null ? '' : String(totals.min), 'number'), totalMax = input('total_max', totals.max == null ? '' : String(totals.max), 'number');
  for (const control of [minCost, maxCost, totalMin, totalMax]) control.min = '0';
  form.append(el('fieldset', {}, el('legend', {}, 'Listed purchase costs'), field('Minimum per champion (optional)', minCost), field('Maximum per champion (optional)', maxCost), field('Minimum total cost (optional)', totalMin), field('Maximum total cost (optional)', totalMax)));
  const locked = input('locked', '', 'checkbox'); locked.checked = saved.include_locked !== false;
  form.append(el('label', { class: 'checkbox full' }, locked, 'Include locked champions as hypothetical final-board choices'));
  const requirements = el('div', { class: 'full' });
  const requiredControls: { trait: HTMLSelectElement; point: HTMLSelectElement; row: HTMLElement }[] = [];
  function addRequirement(initial?: Row): void {
    const trait = select('required_trait', traits.filter(row => list(row.breakpoints).some(value => optionalObject(value).status === 'supported')).map(row => [str(row.trait_id), str(row.name)]), str(initial?.trait_id));
    if (!trait.value && trait.options.length) trait.selectedIndex = 0;
    const point = select('breakpoint', []);
    function updatePoints(): void {
      const current = traits.find(row => row.trait_id === trait.value);
      point.replaceChildren(...records(current?.breakpoints || []).filter(row => row.status === 'supported').map(row => el('option', { value: str(row.breakpoint_id) }, `${text(row.minimum)}-unit breakpoint${row.inferred ? ' (inferred)' : ''}`)));
    }
    updatePoints(); if (initial?.breakpoint_id) point.value = str(initial.breakpoint_id);
    trait.addEventListener('change', () => { updatePoints(); updateSummary(); });
    const row = el('div', { class: 'trait-requirement' }, field('Required trait', trait), field('At least this active breakpoint', point));
    row.append(button('Remove', () => { row.remove(); requiredControls.splice(requiredControls.findIndex(entry => entry.row === row), 1); updateSummary(); saveDefinition(); }));
    requiredControls.push({ trait, point, row }); requirements.append(row);
  }
  form.append(el('h3', { class: 'full' }, 'Trait activation'), requirements, button('Add a required trait', () => { addRequirement(); updateSummary(); saveDefinition(); }));
  const forbidden = select('forbidden', traits.map(trait => [str(trait.trait_id), str(trait.name)])); forbidden.multiple = true;
  for (const option of forbidden.options) option.selected = list(saved.forbidden_traits).includes(option.value);
  form.append(field('Forbidden active traits', forbidden), el('p', { class: 'small muted full' }, 'Members may remain below an activation threshold. Unknown activation satisfies neither a required nor a forbidden trait. Higher supported tiers satisfy a selected minimum.'));
  const summary = el('aside', { class: 'query-summary', 'aria-label': 'Query summary' });
  const errorBox = el('div', { class: 'full', 'aria-live': 'polite' });
  const run = el('button', { type: 'submit', class: 'primary' }, 'Find matching teams');
  form.append(errorBox, run);
  function definition(): Row {
    return { schema_version: 1, dataset_id: ctx.dataset.id, snapshot_id: ctx.publication.snapshot_id, publication_id: ctx.publication.id, ruleset_version: version, team_size: { min: integer(sizeMin), max: integer(sizeMax) }, board_slots: integer(slots), include: chosen(include), exclude: chosen(exclude), cost: { min: optionalInteger(minCost), max: optionalInteger(maxCost) }, total_cost: { min: optionalInteger(totalMin), max: optionalInteger(totalMax) }, required_traits: requiredControls.map(entry => ({ trait_id: entry.trait.value, breakpoint_id: entry.point.value })), forbidden_traits: chosen(forbidden), min_active_traits: integer(active), include_locked: locked.checked };
  }
  function validate(): string | null {
    if (integer(sizeMin) > integer(sizeMax)) return 'Minimum team size exceeds maximum.';
    const included = units.filter(unit => chosen(include).includes(str(unit.form_id)));
    const excluded = new Set(units.filter(unit => chosen(exclude).includes(str(unit.form_id))).map(unit => unit.champion_id));
    if (new Set(included.map(unit => unit.champion_id)).size !== included.length) return 'Included forms refer to the same or mutually exclusive champion.';
    if (included.some(unit => excluded.has(unit.champion_id))) return 'A champion is both included and excluded.';
    if (included.length > integer(sizeMax) || included.reduce((sum, unit) => sum + num(unit.slots), 0) > integer(slots)) return 'Included champions exceed team size or board capacity.';
    if (included.some(unit => (!locked.checked && unit.locked) || minCost.value !== '' && (unit.cost === null || num(unit.cost) < integer(minCost)) || maxCost.value !== '' && (unit.cost === null || num(unit.cost) > integer(maxCost)))) return 'An included champion conflicts with the cost or locked-champion filters.';
    if (minCost.value !== '' && maxCost.value !== '' && integer(minCost) > integer(maxCost) || totalMin.value !== '' && totalMax.value !== '' && integer(totalMin) > integer(totalMax)) return 'A minimum cost exceeds its maximum.';
    if (requiredControls.some(entry => chosen(forbidden).includes(entry.trait.value))) return 'A trait cannot be required and forbidden at the same time.';
    return null;
  }
  function updateSummary(): void {
    const names = units.filter(unit => chosen(include).includes(str(unit.form_id))).map(unit => str(unit.name));
    const description = [integer(sizeMin) === integer(sizeMax) ? `Exactly ${sizeMin.value} distinct champions` : `${sizeMin.value}–${sizeMax.value} distinct champions`, `At most ${slots.value} occupied slots`, `At least ${active.value} proven active traits`, names.length ? 'Include ' + names.join(', ') : 'No required champions', maxCost.value ? `Every champion costs at most ${maxCost.value} gold` : 'No maximum individual cost', locked.checked ? 'Locked champions included hypothetically' : 'Locked champions excluded', 'Native traits · One copy · No emblems or augments'];
    requiredControls.forEach(entry => description.push(`${entry.trait.selectedOptions[0]?.text || ''}: at least ${entry.point.selectedOptions[0]?.text || ''}`));
    const excludedNames = units.filter(unit => chosen(exclude).includes(str(unit.form_id))).map(unit => str(unit.name));
    if (excludedNames.length) description.push('Exclude ' + excludedNames.join(', ') + ' and their exclusive forms');
    const forbiddenNames = traits.filter(trait => chosen(forbidden).includes(str(trait.trait_id))).map(trait => str(trait.name));
    if (forbiddenNames.length) description.push('Proven inactive: ' + forbiddenNames.join(', '));
    if (minCost.value) description.push('Every champion costs at least ' + minCost.value + ' gold');
    if (totalMin.value) description.push('Total listed cost at least ' + totalMin.value + ' gold');
    if (totalMax.value) description.push('Total listed cost at most ' + totalMax.value + ' gold');
    summary.replaceChildren(el('p', { class: 'eyebrow' }, 'Your search'), el('h3', {}, 'Every condition must hold'), el('ul', {}, ...description.map(line => el('li', {}, line))), el('p', { class: 'small muted' }, 'Only proven active traits count. Results describe hypothetical final boards.'), el('details', {}, el('summary', {}, 'Query definition'), el('pre', {}, JSON.stringify(definition(), null, 2))));
    const problem = validate(); errorBox.replaceChildren(...(problem ? [notice(problem, true)] : [])); run.disabled = Boolean(problem);
  }
  function saveDefinition(): void { if (signal.aborted) return; const url = new URL(location.href); url.searchParams.set('definition', JSON.stringify(definition())); url.searchParams.delete('query'); history.replaceState({}, '', url); }
  form.addEventListener('input', () => { updateSummary(); saveDefinition(); });
  form.addEventListener('change', () => { updateSummary(); saveDefinition(); });
  let submitting = false;
  form.addEventListener('submit', async event => {
    event.preventDefault(); const problem = validate(); if (problem || submitting) return;
    const submitted = definition(); submitting = true;
    run.disabled = true; errorBox.replaceChildren(notice('Submitting your query…'));
    try {
      const token = localStorage.getItem('research-session') || crypto.randomUUID();
      localStorage.setItem('research-session', token);
      const job = await ctx.api.post('/v1/queries', { publication_id: ctx.publication.id, query: submitted }, token);
      if (job.session_token) localStorage.setItem('research-session', str(job.session_token));
      sessionStorage.setItem('research-token:' + str(job.query_id), str(job.session_token, token));
      sessionStorage.setItem('research-subscription:' + str(job.query_id), str(job.subscription_id));
      if (!signal.aborted) ctx.navigate({ query: str(job.query_id), definition: JSON.stringify(submitted), page: null });
    } catch (error) { if (!signal.aborted) { errorBox.replaceChildren(notice(error instanceof Error ? error.message : 'Submission failed. Retry with the same filters.', true)); run.disabled = false; } }
    finally { submitting = false; }
  });
  const preset = button('Use Jhin example', () => {
    const jhin = units.filter(unit => str(unit.name).toLocaleLowerCase() === 'jhin');
    if (jhin.length !== 1) { ctx.notify('Jhin cannot be resolved to one legal form in this snapshot.', true); return; }
    sizeMin.value = sizeMax.value = slots.value = active.value = '8'; maxCost.value = '4'; minCost.value = totalMin.value = totalMax.value = ''; locked.checked = true;
    for (const option of include.options) option.selected = option.value === jhin[0].form_id;
    for (const control of [exclude, forbidden]) for (const option of control.options) option.selected = false;
    requirements.replaceChildren(); requiredControls.length = 0; updateSummary(); saveDefinition();
  });
  records(saved.required_traits || []).forEach(addRequirement);
  section.append(el('div', { class: 'toolbar' }, preset), el('div', { class: 'team-layout' }, form, summary), el('h3', {}, 'Rules & assumptions'), el('code', {}, version), el('ul', { class: 'small muted' }, ...list(rules.assumptions).map(value => el('li', {}, text(value)))), el('details', {}, el('summary', {}, 'Inspect rule evidence and available forms'), pretty(rules)));
  updateSummary(); if (previousPublication) saveDefinition(); return section;
}

export async function queryResults(ctx: Workspace, signal: AbortSignal): Promise<{ element: HTMLElement; stop: () => void }> {
  const id = ctx.params.get('query') || '';
  const section = el('section');
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stopped = false;
  let sequence = 0;
  async function refresh(): Promise<void> {
    const request = ++sequence;
    if (timer) clearTimeout(timer);
    try {
      const job = await ctx.api.get('/v1/queries/' + encodeURIComponent(id), signal);
      if (stopped || signal.aborted || request !== sequence) return;
      const definition = optionalObject(job.query);
      if (definition.publication_id !== ctx.publication.id || definition.snapshot_id !== ctx.publication.snapshot_id) throw new Error('This query belongs to a different publication. Open its original snapshot to view the results.');
      const state = str(job.state), complete = job.complete === true && state === 'complete';
      const controls = el('div', { class: 'toolbar' }, button('Edit query', () => ctx.navigate({ query: null, definition: JSON.stringify(definition), page: null })), button('Copy query link', async () => { const url = new URL(ctx.link({ query: id, definition: JSON.stringify(definition), page: null }), 'https://haroldng.dev'); try { await navigator.clipboard.writeText(url.href); ctx.notify('Query link copied. It does not grant cancellation access.'); } catch { ctx.notify(url.href); } }));
      const subscription = sessionStorage.getItem('research-subscription:' + id), token = sessionStorage.getItem('research-token:' + id);
      if (['queued', 'running'].includes(state) && subscription && token) controls.append(button('Cancel my request', async () => { try { await ctx.api.post(`/v1/queries/${encodeURIComponent(id)}/cancel`, { subscription_id: subscription }, token); ctx.notify('Your request was cancelled. Shared work may continue for other visitors.'); await refresh(); } catch (error) { ctx.notify(error instanceof Error ? error.message : 'Cancellation failed.', true); } }));
      section.replaceChildren(el('h2', {}, complete ? 'Matching teams' : 'Team query'), el('p', { class: 'notice' }, complete ? `Complete · ${num(job.result_count).toLocaleString()} configurations. All matches under the recorded rules and assumptions.` : `${state.replaceAll('_', ' ')} · Incomplete. Results and counts remain provisional.`), controls);
      if (job.expired === true) {
        section.replaceChildren(el('h2', {}, 'Result cache expired'), notice('The saved definition and original ruleset are retained. Recompute to obtain results for this version.'), controls, button('Recompute saved query', () => ctx.navigate({ query: null, definition: JSON.stringify(definition), page: null })), pretty(definition));
        return;
      }
      if (job.error) section.append(notice(text(job.error), true));
      section.append(el('div', { class: 'publication-meta' }, el('span', {}, 'Ruleset: ' + text(job.ruleset_version)), el('span', {}, 'Solver: ' + text(job.solver_version)), el('span', {}, 'Distinct champion sets: ' + text(job.champion_set_count))), pretty(job.progress));
      if (complete) {
        const sort = select('result_sort', [['active_traits', 'Active trait count'], ['total_cost', 'Total cost'], ['champions', 'Champion names']], ctx.params.get('result_sort') || 'active_traits');
        sort.addEventListener('change', () => ctx.navigate({ result_sort: sort.value, page: null }));
        section.append(el('div', { class: 'toolbar' }, field('Sort all results', sort)));
      }
      if (num(job.result_count) > 0) {
        const result = await ctx.api.get(`/v1/queries/${encodeURIComponent(id)}/results?page=${ctx.params.get('page') || '1'}&page_size=25&sort=${encodeURIComponent(ctx.params.get('result_sort') || 'active_traits')}`, signal);
        if (stopped || signal.aborted || request !== sequence) return;
        for (const source of records(result.rows)) {
          const row = source.members ? source : optionalObject(source.result ?? source.result_json ?? source.configuration);
          const members = records(row.members || []), traits = records(row.traits || []);
          const article = el('article', { class: 'result-entry' }, el('h3', {}, `${text(row.active_trait_count)} active traits · ${text(row.total_cost)} gold`), el('p', { class: 'small muted' }, `${text(row.champion_count)} champions · ${text(row.board_slots)} occupied slots`));
          const names = el('div', { class: 'toolbar' });
          for (const member of members) names.append(el('a', { href: ctx.link({ view: 'record', record: str(member.unit_id), query: null }) }, `${str(member.name)} (${text(member.cost)}g${member.locked ? ', locked' : ''})`));
          article.append(names, el('div', { class: 'result-traits' }, ...traits.map(trait => el('a', { class: 'badge', href: ctx.link({ view: 'record', record: str(trait.trait_id), query: null }) }, `${str(trait.name)} ${text(trait.member_count)} · tier ${text(trait.minimum)}${trait.inferred ? ' · inferred' : ''}`))));
          if (list(row.unknown_traits).length) article.append(notice('Unknown activation: ' + records(row.unknown_traits).map(trait => str(trait.name)).join(', ')));
          article.append(el('details', {}, el('summary', {}, 'Configuration and rule evidence'), pretty(row))); section.append(article);
        }
        const page = num(result.page, 1), total = num(result.total), size = num(result.page_size, 25);
        const prev = button('← Previous', () => ctx.navigate({ page: String(page - 1) })); prev.disabled = page <= 1;
        const next = button('Next →', () => ctx.navigate({ page: String(page + 1) })); next.disabled = page * size >= total;
        section.append(el('div', { class: 'pagination' }, `Page ${page} · ${total.toLocaleString()} configurations`, el('div', {}, prev, next)));
      } else if (complete) section.append(notice('The exhaustive search completed with no matches. Edit the query to adjust the conditions.'));
      section.append(el('details', {}, el('summary', {}, 'Normalized query'), pretty(definition)));
      if (['queued', 'running'].includes(state) && !stopped) timer = setTimeout(() => { void refresh(); }, 2000);
    } catch (error) {
      if (signal.aborted || stopped || request !== sequence) return;
      section.append(notice(error instanceof Error ? error.message : 'The query service is unavailable. Your query is preserved.', true), button('Retry status', () => refresh()));
    }
  }
  await refresh();
  return { element: section, stop: () => { stopped = true; if (timer) clearTimeout(timer); } };
}
