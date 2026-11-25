/* The BFF client (contracts/interfaces.md § 1) and the one SSE reader: fetch + ReadableStream, `data:`
   framing, keepalive comments and abort. No automatic re-send (the BFF does not replay turns). */

export interface Envelope {
  type: string;
  session_id?: string;
  turn?: number;
  seq?: number;
  ts?: string;
  actor?: string;
  // biome-ignore lint/suspicious/noExplicitAny: each event type has its own data shape (events.md)
  data: any;
  /** Optional chip label the BFF may add next to an a2ui.message createSurface (README, requests). */
  chip?: string;
}
export interface Inbound {
  type: 'user.message' | 'a2ui.action' | 'ui.surface_reopen' | 'confirm.response' | 'document.uploaded';
  data: Record<string, unknown>;
}

export class BffError extends Error {
  constructor(
    readonly status: number,
    detail: string,
  ) {
    super(detail);
  }
}

/** Incremental SSE parser. `feed` takes text chunks cut anywhere; `onEvent` gets each event's data and id. */
export function sseParser(onEvent: (data: string, id?: string) => void) {
  let buf = '';
  let data: string[] = [];
  let id: string | undefined;
  const dispatch = () => {
    if (data.length) onEvent(data.join('\n'), id);
    data = [];
  };
  return {
    feed(chunk: string) {
      buf += chunk;
      for (;;) {
        const m = /\r\n|\r|\n/.exec(buf);
        if (!m || (m[0] === '\r' && m.index === buf.length - 1)) return; // a lone \r may be half of \r\n
        const line = buf.slice(0, m.index);
        buf = buf.slice(m.index + m[0].length);
        if (line === '') dispatch();
        else if (!line.startsWith(':')) {
          const c = line.indexOf(':');
          const name = c < 0 ? line : line.slice(0, c);
          const value = c < 0 ? '' : line.slice(c + 1).replace(/^ /, '');
          if (name === 'data') data.push(value);
          else if (name === 'id') id = value;
        }
      }
    },
    end: dispatch,
  };
}

export interface BffApi {
  createSession(): Promise<{ session_id: string; quote_number: string; events: Envelope[] }>;
  turn(sid: string, inbound: Inbound, onEnvelope: (e: Envelope) => void, signal?: AbortSignal): Promise<void>;
  callFunction(sid: string, message: unknown): Promise<{ agentFunctionResponse?: { functionCallId: string; value: unknown } }>;
  upload(sid: string, file: File, purpose: string, surfaceId?: string, fieldPath?: string): Promise<{ document_id: string }>;
  state(sid: string): Promise<Record<string, unknown>>;
}

export function httpApi(base: string, fetchFn: typeof fetch = (...a) => fetch(...a)): BffApi {
  const url = (p: string) => `${base}/api${p}`;
  const json = async (res: Response) => {
    if (!res.ok) {
      const problem = await res.json().catch(() => ({}));
      throw new BffError(res.status, problem.detail ?? problem.title ?? res.statusText);
    }
    return res.json();
  };
  const post = (p: string, body: unknown) =>
    fetchFn(url(p), {
      method: 'POST',
      credentials: 'include',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    }).then(json);

  return {
    createSession: () => post('/sessions', { locale: 'fr-CA' }),
    callFunction: (sid, message) => post(`/sessions/${sid}/a2ui/functions`, message),
    state: (sid) => fetchFn(url(`/sessions/${sid}/state`), { credentials: 'include' }).then(json),
    upload(sid, file, purpose, surfaceId, fieldPath) {
      const form = new FormData();
      form.append('file', file);
      form.append('purpose', purpose);
      if (surfaceId) form.append('surface_id', surfaceId);
      if (fieldPath) form.append('field_path', fieldPath);
      return fetchFn(url(`/sessions/${sid}/documents`), { method: 'POST', credentials: 'include', body: form }).then(json);
    },
    async turn(sid, inbound, onEnvelope, signal) {
      // One attempt: the BFF does not replay a turn, so a re-post would run the inbound twice. A dropped
      // stream ends in the error bubble and its "Réessayer" button (the customer decides).
      const res = await fetchFn(url(`/sessions/${sid}/turns`), {
        method: 'POST',
        credentials: 'include',
        headers: { 'content-type': 'application/json', accept: 'text/event-stream', 'x-client-turn-id': crypto.randomUUID() },
        body: JSON.stringify(inbound),
        signal,
      });
      if (!res.ok) await json(res);
      if (!res.body) throw new BffError(res.status, 'empty stream');
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      const parser = sseParser((data) => onEnvelope(JSON.parse(data) as Envelope));
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        parser.feed(decoder.decode(value, { stream: true }));
      }
      parser.feed(decoder.decode());
      parser.end();
    },
  };
}
