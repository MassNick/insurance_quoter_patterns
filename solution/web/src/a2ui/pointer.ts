/* RFC 6901 JSON Pointer helpers. set and remove return new objects (the reducer stays pure). */

export function parts(p: string): string[] {
  return p === '/' || p === ''
    ? []
    : p
        .split('/')
        .slice(1)
        .map((x) => x.replace(/~1/g, '/').replace(/~0/g, '~'));
}
export const escapeKey = (k: string | number): string => String(k).replace(/~/g, '~0').replace(/\//g, '~1');

/** Provenance of a bound value lives next to it: /_provenance/<escaped pointer> = { source, note }. */
export const provPath = (path: string): string => `/_provenance/${escapeKey(path)}`;
export const keyOf = (path: string): string => parts(path).at(-1) ?? '';

export function get(obj: unknown, path: string): unknown {
  let cur: unknown = obj;
  for (const k of parts(path)) {
    if (cur == null || typeof cur !== 'object') return undefined;
    cur = (cur as Record<string, unknown>)[k];
  }
  return cur;
}

function setIn(node: unknown, ks: string[], value: unknown, remove: boolean): unknown {
  const [k, ...rest] = ks;
  const isIndex = /^\d+$/.test(k);
  const base: Record<string, unknown> | unknown[] =
    node != null && typeof node === 'object' ? (Array.isArray(node) ? [...node] : { ...(node as object) }) : isIndex ? [] : {};
  const rec = base as Record<string, unknown>;
  if (!rest.length) {
    if (!remove) rec[k] = value;
    else if (Array.isArray(base)) rec[k] = undefined;
    else delete rec[k];
    return base;
  }
  if (remove && rec[k] == null) return base;
  rec[k] = setIn(rec[k], rest, value, remove);
  return base;
}

export function set<T>(obj: T, path: string, value: unknown): T {
  const ks = parts(path);
  return (ks.length ? setIn(obj, ks, value, false) : value) as T;
}

export function remove<T>(obj: T, path: string): T {
  const ks = parts(path);
  return (ks.length ? setIn(obj, ks, undefined, true) : {}) as T;
}
