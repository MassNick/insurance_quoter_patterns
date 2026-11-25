/* @checks evaluation: the same function names and semantics as demo/src/a2ui.js (runCheck plus the
   renderer-local ymmValid and onePrincipalPerVehicle checks). Arguments may be {"@path"} references,
   absolute or relative to a repeater item scope. */
import { ageFrom, clock, parseDay } from './catalog';
import { get } from './pointer';
import type { A2Component, CheckRule, DataModel, FunctionCall } from './types';

const VIN_MAP: Record<string, number> = {
  A: 1,
  B: 2,
  C: 3,
  D: 4,
  E: 5,
  F: 6,
  G: 7,
  H: 8,
  J: 1,
  K: 2,
  L: 3,
  M: 4,
  N: 5,
  P: 7,
  R: 9,
  S: 2,
  T: 3,
  U: 4,
  V: 5,
  W: 6,
  X: 7,
  Y: 8,
  Z: 9,
};
const VIN_W = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2];

export function vinValid(raw: unknown): boolean {
  const v = String(raw ?? '')
    .toUpperCase()
    .trim();
  if (!/^[A-HJ-NPR-Z0-9]{17}$/.test(v)) return false;
  let s = 0;
  for (let i = 0; i < 17; i++) s += (/\d/.test(v[i]) ? Number(v[i]) : VIN_MAP[v[i]]) * VIN_W[i];
  const r = s % 11;
  return v[8] === (r === 10 ? 'X' : String(r));
}

export const resolvePath = (p: string, scope?: string): string => (!p.startsWith('/') && scope ? `${scope}/${p}` : p);
export const ymmKey = (y: unknown, m: unknown, d: unknown): string => `${y ?? ''}|${m ?? ''}|${d ?? ''}`;

export interface CheckContext {
  data: DataModel;
  scope?: string;
  component?: A2Component;
  /** ymmValid answers from lookupMakeModelYear, keyed by ymmKey(year, make, model). */
  lookups?: Record<string, boolean>;
}

function arg(v: unknown, ctx: CheckContext): unknown {
  if (v && typeof v === 'object' && '@path' in v) return get(ctx.data, resolvePath((v as { '@path': string })['@path'], ctx.scope));
  return v;
}

const isEmpty = (v: unknown) => v == null || v === '' || (Array.isArray(v) && v.length === 0);

type Assignment = { driver_index: number; vehicle_index: number; role: string };

function onePrincipalPerVehicle(value: unknown, c?: A2Component): boolean {
  const arr = (Array.isArray(value) ? value : []) as Assignment[];
  const vehicles: number[] = c?.vehicles
    ? c.vehicles.map((v: { index: number }) => v.index)
    : (c?.cols ?? []).map((_: unknown, i: number) => i);
  return vehicles.every((vi) => arr.filter((a) => a.vehicle_index === vi && a.role === 'principal').length === 1);
}

export function evalCall(call: FunctionCall, ctx: CheckContext): boolean {
  const a = Object.fromEntries(Object.entries(call.args ?? {}).map(([k, v]) => [k, arg(v, ctx)])) as Record<string, unknown>;
  const v = a.value;
  switch (call['@call']) {
    case 'required':
      return !isEmpty(v);
    case 'equals':
      return a.left === a.right;
    case 'ymmValid': {
      if (isEmpty(v) && isEmpty(a.make) && isEmpty(a.model)) return !ctx.component?.required;
      return ctx.lookups?.[ymmKey(v, a.make, a.model)] === true;
    }
    case 'onePrincipalPerVehicle':
      return onePrincipalPerVehicle(v, ctx.component);
  }
  if (isEmpty(v)) return true;
  switch (call['@call']) {
    case 'email':
      return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(String(v));
    case 'regex':
      return new RegExp(String(a.pattern), 'i').test(String(v).trim());
    case 'vinCheckDigit':
      return vinValid(v);
    case 'numericRange': {
      const n = Number(v);
      return !Number.isNaN(n) && (a.min == null || n >= Number(a.min)) && (a.max == null || n <= Number(a.max));
    }
    case 'ageBetween': {
      const g = ageFrom(v);
      return g != null && g >= Number(a.min) && (a.max == null || g <= Number(a.max));
    }
    case 'dateWithin': {
      const t0 = clock.now();
      t0.setHours(0, 0, 0, 0);
      const days = Math.round((parseDay(v).getTime() - t0.getTime()) / 864e5);
      return days >= Number(a.from_days) && days <= Number(a.to_days);
    }
    default:
      return true; // unknown functions never block a submit; the BFF validates again
  }
}

/** The first failing check's message, or null. */
export function firstError(checks: CheckRule[] | undefined, ctx: CheckContext): string | null {
  return checks?.find((ch) => !evalCall(ch.condition, ctx))?.message ?? null;
}
