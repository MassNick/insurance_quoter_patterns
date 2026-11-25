/* The app's one event reducer. Every outbound envelope of contracts/events.md and every local UI
   action goes through `reduce`, so the whole customer view is a pure function of what the BFF sent and
   what the customer did. The A2UI part is delegated to the a2ui reducer. */

import { remove, set } from '../a2ui/pointer';
import { emptySurfaces, type LocalAction, type SurfacesState, surfacesReducer } from '../a2ui/reducer';
import { surfaceTitle } from '../a2ui/Surface';
import type { A2uiMessage } from '../a2ui/types';
import type { Envelope, Inbound } from './api';

export interface Suggestion {
  label: string;
  kind?: string;
  action_id?: string;
}
/** A knowledge-base passage the reply cites (text.done `citations`, contracts/events.md). */
export interface Citation {
  title: string;
  url?: string;
  id?: string;
  ref_id?: string;
}
export interface Msg {
  id: string;
  role: 'bot' | 'user' | 'system';
  text: string;
  typing?: boolean;
  streaming?: boolean;
  citations?: Citation[];
  retry?: Inbound;
  chips: string[];
}
export type ChipStatus = 'open' | 'submitted' | 'closed' | 'sent';
export interface Chip {
  surfaceId: string;
  label: string;
  status: ChipStatus;
  /** Status before a reopen, restored if the customer closes without saving. */
  prev: ChipStatus;
  edit: boolean;
  locked: boolean;
  messageId: string;
}
export interface Signal {
  code: string;
  message: string;
  flag?: boolean;
}
export interface Change {
  path: string;
  old_value: unknown;
  new_value: unknown;
}
/** confirm.required; the BFF groups the deterministic fields of one commit in `changes` (bff/README.md). */
export interface Confirmation extends Change {
  confirmation_id: string;
  reason: string;
  changes?: Change[];
}
export const changesOf = (c: Confirmation): Change[] => (c.changes?.length ? c.changes : [c]);
export const pathsOf = (c: Confirmation): string[] => changesOf(c).map((x) => x.path);
export interface Offer {
  offer_type: string;
  monthly: number;
  annual?: number;
  deductible: number;
}
export interface Quote {
  quote_id: string;
  version: number;
  offers: Offer[];
  reason?: string;
  created_at?: string;
  previous_version?: number;
  delta_monthly?: number;
}
export interface TraceRow {
  ms: number;
  type: string;
  label: string;
  detail?: unknown;
}

export interface AppState {
  sessionId: string | null;
  quoteNumber: string;
  messages: Msg[];
  /** The bot message the current stream writes into. */
  currentBot: string | null;
  lastInbound: Inbound | null;
  suggestions: Suggestion[];
  suggestionsKey: number;
  busy: boolean;
  surfaces: SurfacesState;
  chips: Record<string, Chip>;
  /** The surface that belongs on the form stage (the Stage animates toward it). */
  stage: string | null;
  /** How the next deleteSurface of a surface sets its chip, decided when the customer submits. */
  pendingClose: Record<string, ChipStatus>;
  mode: 'chat' | 'form';
  /** Mirror of the committed session state: field envelopes { value, source, status }. */
  doc: Record<string, unknown>;
  flash: { paths: string[]; n: number };
  signals: Signal[];
  quotes: Quote[];
  quotesPing: number;
  confirmations: Confirmation[];
  modal: { step: 1 | 2; ids: string[] } | null;
  handoff: { reference: string; rule_id: string | null; closed: boolean } | null;
  phase: string;
  dossier: { open: boolean; tab: 'states' | 'quotes' };
  peek: { text: string; n: number } | null;
  trace: TraceRow[];
}

export const initialState = (): AppState => ({
  sessionId: null,
  quoteNumber: '—',
  messages: [],
  currentBot: null,
  lastInbound: null,
  suggestions: [],
  suggestionsKey: 0,
  busy: true,
  surfaces: emptySurfaces(),
  chips: {},
  stage: null,
  pendingClose: {},
  mode: 'chat',
  doc: { quote: {}, vehicles: [], drivers: [], underwriting: {}, household: {} },
  flash: { paths: [], n: 0 },
  signals: [],
  quotes: [],
  quotesPing: 0,
  confirmations: [],
  modal: null,
  handoff: null,
  phase: 'intent',
  dossier: { open: false, tab: 'states' },
  peek: null,
  trace: [],
});

export type Action =
  | { type: 'init'; sessionId: string; quoteNumber: string }
  | { type: 'envelope'; env: Envelope }
  | { type: 'surface'; action: LocalAction }
  | { type: 'turnStart'; inbound: Inbound; userText?: string; typing: boolean }
  | { type: 'turnEnd' }
  | { type: 'turnFailed' }
  | { type: 'submitted'; surfaceId: string; status: ChipStatus }
  | { type: 'modal'; step: 1 | 2 | null }
  | { type: 'mode'; mode: 'chat' | 'form' }
  | { type: 'dossier'; open?: boolean; tab?: 'states' | 'quotes' }
  | { type: 'closeHandoff' }
  | { type: 'snapshot'; snapshot: Record<string, unknown> }
  | { type: 'trace'; row: Omit<TraceRow, 'ms'> }
  | { type: 'system'; text: string }
  | { type: 'peekOff' };

let ids = 0;
const nextId = () => `m${++ids}`;
const t0 = typeof performance === 'object' ? performance.now() : 0;
const now = () => Math.round((typeof performance === 'object' ? performance.now() : 0) - t0);

/** D15, defence in depth: the BFF already filters; the page never shows more than 4 short, PII-free items. */
const PII = [/@/, /\d{3}[ .-]?\d{3}[ .-]?\d{4}/, /\b\d{4}-\d{2}-\d{2}\b/, /\b[A-Z]\d{4}-\d{6}-\d{2}\b/i, /\b[A-Z]\d[A-Z] ?\d[A-Z]\d\b/i];
export const keepSuggestions = (items: Suggestion[]) =>
  items.filter((it) => it.label && it.label.length <= 40 && !PII.some((re) => re.test(it.label))).slice(0, 4);

const ERROR_TEXT = 'Désolé, un problème technique m’empêche de répondre. Vous pouvez réessayer.';

function patchMsg(s: AppState, id: string | null, fn: (m: Msg) => Msg): AppState {
  return id ? { ...s, messages: s.messages.map((m) => (m.id === id ? fn(m) : m)) } : s;
}
const botMsg = (text = '', extra: Partial<Msg> = {}): Msg => ({ id: nextId(), role: 'bot', text, chips: [], ...extra });

/** The bot message a new event writes into: the current one, or a fresh one. */
function ensureBot(s: AppState, reuseDone: boolean): [AppState, string] {
  const cur = s.messages.find((m) => m.id === s.currentBot);
  if (cur && (cur.typing || cur.streaming || reuseDone)) return [s, cur.id];
  const m = botMsg();
  return [{ ...s, messages: [...s.messages, m], currentBot: m.id }, m.id];
}

function onA2ui(s: AppState, msg: A2uiMessage, env: Envelope): AppState {
  const surfaces = surfacesReducer(s.surfaces, { type: 'message', message: msg });
  let next: AppState = { ...s, surfaces };
  const created = msg.createSurface?.surfaceId;
  if (created && surfaces.byId[created] && surfaces !== s.surfaces) {
    if (created === 'full_form') return { ...next, mode: 'form' };
    const old = next.chips[created];
    if (old) next = { ...next, chips: { ...next.chips, [created]: { ...old, status: 'open', prev: old.status, edit: true } } };
    else {
      const last = [...next.messages].reverse().find((m) => m.role === 'bot');
      let host = next.currentBot ?? last?.id;
      if (!host) [next, host] = ensureBot(next, true);
      const label = `Formulaire : ${env.chip ?? surfaceTitle(surfaces.byId[created])}`;
      next = patchMsg(next, host, (m) => ({ ...m, chips: [...m.chips, created] }));
      next.chips = {
        ...next.chips,
        [created]: { surfaceId: created, label, status: 'open', prev: 'open', edit: false, locked: false, messageId: host },
      };
    }
    next.stage = created;
    next.peek = null;
    if (created === 'end_of_form') next.suggestions = [];
  }
  const deleted = msg.deleteSurface?.surfaceId;
  if (deleted && s.surfaces.byId[deleted] && !s.surfaces.byId[deleted].deleted) {
    const chip = next.chips[deleted];
    const decided = next.pendingClose[deleted];
    const { [deleted]: _done, ...pendingClose } = next.pendingClose;
    next = { ...next, pendingClose };
    const note = (text: string) => (next.messages = [...next.messages, { id: nextId(), role: 'system', text, chips: [] }]);
    if (deleted === 'full_form') return { ...next, mode: 'chat' };
    if (chip && chip.status !== 'sent') {
      const status = decided ?? (chip.edit ? chip.prev : 'closed');
      next.chips = { ...next.chips, [deleted]: { ...chip, status, edit: false } };
      if (!chip.edit && decided === 'closed') note('Formulaire fermé. Vous pouvez répondre ici, ou le rouvrir depuis la conversation.');
    }
    const keepHandoff = deleted === 'end_of_form' && next.handoff && !next.handoff.closed;
    if (next.stage === deleted && !keepHandoff) next.stage = null;
  }
  return next;
}

function onEnvelope(s: AppState, env: Envelope): AppState {
  const d = env.data ?? {};
  switch (env.type) {
    case 'text.delta': {
      const [n, id] = ensureBot(s, false);
      return patchMsg(n, id, (m) => ({ ...m, typing: false, streaming: true, text: m.text + String(d.delta ?? '') }));
    }
    case 'text.done': {
      const [n, id] = ensureBot(s, false);
      const next = patchMsg(n, id, (m) => ({
        ...m,
        typing: false,
        streaming: false,
        text: String(d.text ?? m.text),
        citations: d.citations,
      }));
      return next.stage ? { ...next, peek: { text: String(d.text ?? ''), n: (s.peek?.n ?? 0) + 1 } } : next;
    }
    case 'suggestions':
      return { ...s, suggestions: keepSuggestions(d.items ?? []), suggestionsKey: s.suggestionsKey + 1 };
    case 'a2ui.message':
      return onA2ui(s, d as A2uiMessage, env);
    case 'state.delta': {
      let doc = s.doc;
      const paths: string[] = [];
      for (const ch of d.changes ?? []) {
        doc = ch.value === null ? remove(doc, ch.path) : set(doc, ch.path, { value: ch.value, source: ch.source, status: ch.status });
        paths.push(ch.path);
      }
      return { ...s, doc, flash: { paths, n: s.flash.n + 1 } };
    }
    case 'signals': {
      const known = new Set(s.signals.map((x) => x.code));
      return { ...s, signals: [...s.signals, ...(d.items ?? []).filter((x: Signal) => !known.has(x.code))] };
    }
    case 'rules.hit':
      if (d.severity !== 'flag' || s.signals.some((x) => x.code === d.rule_id)) return s;
      return { ...s, signals: [...s.signals, { code: d.rule_id, message: d.reason, flag: true }] };
    case 'confirm.required':
      if (s.confirmations.some((c) => c.confirmation_id === d.confirmation_id)) return s;
      return { ...s, confirmations: [...s.confirmations, d as Confirmation] };
    case 'confirm.rejected': {
      const confirmations = s.confirmations.filter((c) => c.confirmation_id !== d.confirmation_id);
      const ids = s.modal?.ids.filter((id) => id !== d.confirmation_id) ?? [];
      return { ...s, confirmations, modal: s.modal && ids.length ? { ...s.modal, ids } : null };
    }
    case 'quote.created':
    case 'quote.updated': {
      const quotes = [...s.quotes.filter((q) => !(q.quote_id === d.quote_id && q.version === d.version)), d as Quote];
      return { ...s, quotes, quotesPing: s.quotesPing + 1 };
    }
    case 'handoff.created': {
      const chips = Object.fromEntries(
        Object.entries(s.chips).map(([id, c]) => [
          id,
          id === 'end_of_form' ? { ...c, status: 'sent' as const, locked: true } : { ...c, locked: true },
        ]),
      );
      return { ...s, chips, handoff: { reference: d.reference, rule_id: d.rule_id ?? null, closed: false }, suggestions: [] };
    }
    case 'phase':
      return { ...s, phase: d.phase };
    case 'error': {
      const [n, id] = ensureBot(s, false);
      return patchMsg(n, id, (m) => ({
        ...m,
        typing: false,
        streaming: false,
        text: ERROR_TEXT,
        retry: d.retryable && s.lastInbound ? s.lastInbound : undefined,
      }));
    }
    default:
      return s; // turn.*, tool.*, guardrail, session.preferences: trace only
  }
}

function traceOf(env: Envelope): Omit<TraceRow, 'ms'> | null {
  if (env.type === 'text.delta') return null;
  const d = env.data ?? {};
  const a2 = env.type === 'a2ui.message' ? Object.keys(d).find((k) => k !== 'version') : '';
  const sid = a2 ? (d[a2]?.surfaceId ?? d[a2]?.functionCallId ?? '') : '';
  const label = a2
    ? `${a2} → ${sid}`
    : (d.name ??
      d.phase ??
      d.rule_id ??
      d.confirmation_id ??
      d.quote_id ??
      d.reference ??
      d.code ??
      (typeof d.text === 'string' ? d.text.slice(0, 80) : ''));
  return { type: env.type, label: `${env.actor ? `[${env.actor}] ` : ''}${label}`, detail: env };
}

export function reduce(s: AppState, a: Action): AppState {
  switch (a.type) {
    case 'init':
      return { ...s, sessionId: a.sessionId, quoteNumber: a.quoteNumber };
    case 'envelope': {
      const row = traceOf(a.env);
      const next = onEnvelope(s, a.env);
      return row ? { ...next, trace: [...next.trace.slice(-499), { ms: now(), ...row }] } : next;
    }
    case 'surface':
      return { ...s, surfaces: surfacesReducer(s.surfaces, a.action) };
    case 'turnStart': {
      const messages = [...s.messages];
      if (a.userText) messages.push({ id: nextId(), role: 'user', text: a.userText, chips: [] });
      const bot = a.typing ? botMsg('', { typing: true }) : null;
      if (bot) messages.push(bot);
      return { ...s, busy: true, suggestions: [], messages, currentBot: bot?.id ?? null, lastInbound: a.inbound };
    }
    case 'turnEnd': {
      const messages = s.messages.filter((m) => !(m.id === s.currentBot && m.role === 'bot' && !m.text && !m.chips.length));
      const open = s.confirmations.map((c) => c.confirmation_id);
      return { ...s, busy: false, messages, currentBot: null, modal: s.modal ?? (open.length ? { step: 1, ids: open } : null) };
    }
    case 'turnFailed': {
      const [n, id] = ensureBot(s, false);
      return {
        ...patchMsg(n, id, (m) => ({ ...m, typing: false, streaming: false, text: ERROR_TEXT, retry: s.lastInbound ?? undefined })),
        busy: false,
        currentBot: null,
      };
    }
    case 'submitted':
      return { ...s, pendingClose: { ...s.pendingClose, [a.surfaceId]: a.status } };
    case 'modal':
      if (a.step) return s.modal ? { ...s, modal: { ...s.modal, step: a.step } } : s;
      return { ...s, modal: null, confirmations: s.confirmations.filter((c) => !s.modal?.ids.includes(c.confirmation_id)) };
    case 'mode':
      return { ...s, mode: a.mode };
    case 'dossier':
      return { ...s, dossier: { open: a.open ?? s.dossier.open, tab: a.tab ?? s.dossier.tab } };
    case 'closeHandoff': {
      if (!s.handoff) return s;
      const messages: Msg[] = [...s.messages, { id: nextId(), role: 'system', text: 'Dossier transmis à un conseiller.', chips: [] }];
      return { ...s, handoff: { ...s.handoff, closed: true }, stage: null, messages, suggestions: [] };
    }
    case 'snapshot': {
      const snap = a.snapshot as {
        state?: Record<string, unknown>;
        signals?: Signal[];
        advisor_signals?: Signal[];
        surfaces?: { surface_id: string; status: ChipStatus }[];
        quote_number?: string;
      };
      const chips = { ...s.chips };
      for (const sf of snap.surfaces ?? []) {
        const c = chips[sf.surface_id];
        if (c && s.stage !== sf.surface_id && c.status !== 'sent' && sf.status !== 'open')
          chips[sf.surface_id] = { ...c, status: sf.status };
      }
      const flags = (snap.advisor_signals ?? []).map((x) => ({ ...x, flag: true }));
      return {
        ...s,
        doc: snap.state ?? s.doc,
        signals: snap.signals || snap.advisor_signals ? [...(snap.signals ?? []), ...flags] : s.signals,
        chips,
        quoteNumber: snap.quote_number ?? s.quoteNumber,
      };
    }
    case 'trace':
      return { ...s, trace: [...s.trace.slice(-499), { ms: now(), ...a.row }] };
    case 'system':
      return { ...s, messages: [...s.messages, { id: nextId(), role: 'system', text: a.text, chips: [] }] };
    case 'peekOff':
      return { ...s, peek: null };
  }
}

/* A minimal external store for useSyncExternalStore: no state library. */
export interface Store {
  get(): AppState;
  dispatch(a: Action): void;
  subscribe(fn: () => void): () => void;
}
export function createStore(init: AppState = initialState()): Store {
  let state = init;
  const subs = new Set<() => void>();
  return {
    get: () => state,
    dispatch(a) {
      state = reduce(state, a);
      for (const fn of subs) fn();
    },
    subscribe(fn) {
      subs.add(fn);
      return () => subs.delete(fn);
    },
  };
}
