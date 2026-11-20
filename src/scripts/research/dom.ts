import { date, str, text, type Row } from './api';
type Child = Node | string;
export function el<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Record<string, string> = {}, ...children: Child[]): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  node.append(...children);
  return node;
}
export function button(label: string, run: () => void | Promise<void>, cls = ''): HTMLButtonElement {
  const node = el('button', { type: 'button', class: cls }, label);
  node.addEventListener('click', () => { void run(); });
  return node;
}
export function field(label: string, control: HTMLElement): HTMLLabelElement { control.setAttribute('aria-label', label); return el('label', {}, label, control); }
export function input(name: string, value = '', type = 'text'): HTMLInputElement { return el('input', { name, value, type }); }
export function select(name: string, choices: [string, string][], current = ''): HTMLSelectElement {
  const node = el('select', { name });
  choices.forEach(([value, label]) => node.append(el('option', { value }, label)));
  node.value = current;
  return node;
}
export function notice(message: string, error = false): HTMLElement { return el('p', { class: 'notice' + (error ? ' error' : ''), ...(error ? { role: 'alert' } : {}) }, message); }
export function pretty(value: unknown): HTMLElement {
  if (value && typeof value === 'object' || typeof value === 'string' && value.length > 180) return el('details', {}, el('summary', {}, 'Inspect value'), el('pre', {}, text(value)));
  return el('span', {}, text(value));
}
export function table(rows: Row[], columns?: string[], render?: (row: Row, column: string) => Node): HTMLElement {
  if (!rows.length) return el('p', { class: 'notice' }, 'No rows in this selection.');
  const keys = columns || Object.keys(rows[0]);
  const body = el('tbody');
  for (const row of rows) body.append(el('tr', {}, ...keys.map(key => el('td', {}, render ? render(row, key) : pretty(row[key])))));
  return el('div', { class: 'table-region', tabindex: '0', role: 'region', 'aria-label': 'Scrollable data table' }, el('table', {}, el('thead', {}, el('tr', {}, ...keys.map(key => el('th', { scope: 'col' }, key.replaceAll('_', ' '))))), body));
}
export function safeLink(label: string, url: unknown): HTMLElement {
  if (typeof url !== 'string') return el('span', {}, label + ' · URL unavailable');
  try {
    const parsed = new URL(url, location.origin);
    if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error();
    return el('a', { href: parsed.href, target: '_blank', rel: 'noopener noreferrer' }, label + ' ↗');
  } catch { return el('span', {}, label + ' · invalid source URL'); }
}
export function evidence(rows: Row[]): HTMLElement {
  const section = el('section');
  if (!rows.length) section.append(notice('Field-level evidence is unavailable. Use the record citation and source context below.'));
  for (const row of rows) section.append(el('article', { class: 'evidence-entry' }, safeLink(str(row.title) || str(row.source_id, 'Source'), row.url), el('p', {}, 'Status: ' + text(row.status) + (row.stale ? ' · Stale' : '') + ' · Origin: ' + text(row.evidence_origin)), el('p', {}, 'Field: ' + text(row.field_path) + ' · Locator: ' + text(row.source_path ?? row.source_locator)), pretty(row.value_json ?? row.value), el('p', {}, 'Retrieved: ' + date(row.retrieved_at) + ' · Published: ' + date(row.published_at)), el('code', {}, 'SHA-256: ' + text(row.sha256))));
  return section;
}
