/* The state catalog (contracts/state-catalog.json, imported at build time) and the fr-CA formatting
   helpers ported from demo/src/a2ui.js (labelFor, fmtMoney, ageFrom). Customer text is French only. */
import catalog from '../../../contracts/state-catalog.json' with { type: 'json' };

export interface CatalogField {
  key: string;
  entity: string;
  type: string;
  label_fr?: string;
  help_fr?: string;
  enum_fr?: [string, string][];
  deterministic?: boolean;
  item_role_fr?: [string, string][];
}

export const FIELDS: Record<string, CatalogField> = Object.fromEntries(
  (catalog.fields as unknown as CatalogField[]).map((f) => [f.key, f]),
);
export const field = (key: string): CatalogField | undefined => FIELDS[key];
export const labelOf = (key: string): string => FIELDS[key]?.label_fr ?? '';
export const helpOf = (key: string): string => FIELDS[key]?.help_fr ?? '';

/** Tests pin the clock; the app uses the real one. */
export const clock = { now: (): Date => new Date() };

const MONTHS = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'];

export function fmtMoney(n: unknown, cents = false): string {
  if (n == null || n === '') return '';
  const digits = cents ? 2 : 0;
  return `${Number(n).toLocaleString('fr-CA', { minimumFractionDigits: digits, maximumFractionDigits: digits })} $`;
}

/** A YYYY-MM-DD date as a local day (new Date('2026-01-02') would be UTC midnight, the day before in Québec). */
export function parseDay(iso: unknown): Date {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso));
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(String(iso));
}

export function ageFrom(iso: unknown): number | null {
  if (!iso) return null;
  const d = parseDay(iso);
  if (Number.isNaN(d.getTime())) return null;
  const n = clock.now();
  let a = n.getFullYear() - d.getFullYear();
  const m = n.getMonth() - d.getMonth();
  if (m < 0 || (m === 0 && n.getDate() < d.getDate())) a--;
  return a;
}

/** A value as the customer reads it: enum labels, Oui/Non, long dates, money, kilometres. */
export function labelFor(key: string, value: unknown): string {
  const def = FIELDS[key];
  if (value === true) return 'Oui';
  if (value === false) return 'Non';
  if (value == null || value === '') return '';
  const en = def?.enum_fr;
  if (en) {
    const find = (v: unknown) => en.find((e) => e[0] === String(v))?.[1] ?? String(v);
    if (Array.isArray(value)) return value.map(find).join(', ');
    if (en.some((e) => e[0] === String(value))) return find(value);
  }
  if (def?.type === 'date' && /^\d{4}-\d{2}-\d{2}$/.test(String(value))) {
    const [y, m, d] = String(value).split('-');
    return `${Number(d)} ${MONTHS[Number(m) - 1]} ${y}`;
  }
  if (def?.type === 'currency') return fmtMoney(value);
  if (key === 'annual_kilometers') return `${Number(value).toLocaleString('fr-CA')} km`;
  if (Array.isArray(value)) return `${value.length} élément${value.length === 1 ? '' : 's'}`;
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}
