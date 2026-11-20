export type Value = null | boolean | number | string | Value[] | { [key: string]: Value };
export type Row = { [key: string]: Value };
export function object(value: unknown): Row {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('The service returned an incompatible response.');
  for (const item of Object.values(value)) validateValue(item);
  return value as Row;
}
function validateValue(value: unknown): asserts value is Value {
  if (value === null || ['string', 'boolean', 'number'].includes(typeof value)) return;
  if (Array.isArray(value)) { value.forEach(validateValue); return; }
  object(value);
}
export function records(value: unknown): Row[] {
  if (!Array.isArray(value)) throw new Error('The service returned an incompatible collection.');
  return value.map(object);
}
export function str(value: unknown, fallback = ''): string { return typeof value === 'string' ? value : fallback; }
export function num(value: unknown, fallback = 0): number { return typeof value === 'number' && Number.isFinite(value) ? value : fallback; }
export function list(value: unknown): Value[] { return Array.isArray(value) ? value : []; }
export function optionalObject(value: unknown): Row { return value && typeof value === 'object' && !Array.isArray(value) ? object(value) : {}; }
export function text(value: unknown): string {
  if (value === null || value === undefined) return 'Unknown';
  if (value === '') return 'Empty text';
  return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
}
export function date(value: unknown): string {
  if (typeof value !== 'string' || !value) return 'Unknown';
  if (!value.includes('T')) return value + ' (source date)';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Hong_Kong' }).format(parsed) + ' · Asia/Hong_Kong';
}
export class Api {
  constructor(readonly base: string) {
    if (!base) throw new Error('The research service is not configured yet. Please try again after the service has been connected.');
    const url = new URL(base, location.origin);
    if (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname))) throw new Error('The research service requires a secure connection.');
  }
  url(path: string): string { return this.base.replace(/\/$/, '') + path; }
  async get(path: string, signal?: AbortSignal): Promise<Row> {
    return this.request(path, { signal });
  }
  async post(path: string, body: Row, token?: string): Promise<Row> {
    return this.request(path, { method: 'POST', headers: { 'Content-Type': 'application/json', ...(token ? { 'X-Session-Token': token } : {}) }, body: JSON.stringify(body) });
  }
  private async request(path: string, init: RequestInit): Promise<Row> {
    const response = await fetch(this.url(path), { ...init, signal: init.signal || AbortSignal.timeout(20000) });
    const payload: unknown = await response.json();
    const data = object(payload);
    if (!response.ok) throw new Error(str(data.detail) || str(data.error) || `Request failed (${response.status}). Your selected version and filters are preserved.`);
    return data;
  }
}
