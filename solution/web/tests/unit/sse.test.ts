/* The one SSE client: framing, chunk boundaries, no re-send, errors, abort. */
import { describe, expect, it, vi } from 'vitest';
import { sse } from '../../fixtures/mock-bff';
import { BffError, type Envelope, httpApi, sseParser } from '../../src/app/api';

const env = (seq: number, type = 'text.delta', data: unknown = { delta: `t${seq}` }): Envelope => ({
  type,
  turn: 3,
  seq,
  actor: 'main_agent',
  data,
});

function stream(chunks: string[], failAfter?: number) {
  const enc = new TextEncoder();
  let i = 0;
  return new ReadableStream<Uint8Array>({
    pull(c) {
      if (failAfter != null && i === failAfter) return c.error(new TypeError('network down'));
      if (i < chunks.length) c.enqueue(enc.encode(chunks[i++]));
      else c.close();
    },
  });
}
const sseResponse = (body: ReadableStream<Uint8Array>) =>
  new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } });

describe('sseParser', () => {
  it('splits events on blank lines across arbitrary chunk boundaries, CRLF included, and skips comments', () => {
    const got: [string, string | undefined][] = [];
    const p = sseParser((d, id) => got.push([d, id]));
    const text = ': keepalive\r\n\r\nid: 3:1\r\ndata: {"a":1}\r\n\r\ndata: line1\ndata: line2\n\n';
    for (const ch of text) p.feed(ch); // one character at a time
    p.end();
    expect(got).toEqual([
      ['{"a":1}', '3:1'],
      ['line1\nline2', '3:1'],
    ]);
  });

  it('flushes a final event without a trailing blank line', () => {
    const got: string[] = [];
    const p = sseParser((d) => got.push(d));
    p.feed('data: last');
    p.feed('\n');
    p.end();
    expect(got).toEqual(['last']);
  });
});

describe('httpApi.turn', () => {
  it('posts the inbound envelope with credentials and streams envelopes in order', async () => {
    const fetchFn = vi.fn(async () => sseResponse(stream([sse([env(1), env(2)]).slice(0, 25), sse([env(1), env(2)]).slice(25)])));
    const got: Envelope[] = [];
    await httpApi('https://bff.example', fetchFn as unknown as typeof fetch).turn(
      's1',
      { type: 'user.message', data: { text: 'Bonjour' } },
      (e) => got.push(e),
    );
    expect(got.map((e) => e.seq)).toEqual([1, 2]);
    const [url, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://bff.example/api/sessions/s1/turns');
    expect(init.credentials).toBe('include');
    expect(JSON.parse(String(init.body))).toEqual({ type: 'user.message', data: { text: 'Bonjour' } });
  });

  it('does not re-send after a network drop: the error reaches the caller once', async () => {
    const fetchFn = vi.fn(async () => sseResponse(stream([sse([env(1)])], 1)));
    const got: number[] = [];
    const p = httpApi('', fetchFn as unknown as typeof fetch).turn('s1', { type: 'user.message', data: { text: 'x' } }, (e) =>
      got.push(e.seq ?? 0),
    );
    await expect(p).rejects.toThrow('network down');
    expect(got).toEqual([1]);
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  it('does not retry an HTTP error and surfaces the problem detail', async () => {
    const fetchFn = vi.fn(
      async () => new Response(JSON.stringify({ title: 'Forbidden', status: 403, detail: 'session cookie missing' }), { status: 403 }),
    );
    const p = httpApi('', fetchFn as unknown as typeof fetch).turn('s1', { type: 'user.message', data: { text: 'x' } }, () => undefined);
    await expect(p).rejects.toEqual(new BffError(403, 'session cookie missing'));
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  it('stops on abort without retrying', async () => {
    const ctl = new AbortController();
    const fetchFn = vi.fn(async (_u: string, init: RequestInit) => {
      ctl.abort();
      throw Object.assign(new Error('aborted'), { name: 'AbortError', signal: init.signal });
    });
    await expect(
      httpApi('', fetchFn as unknown as typeof fetch).turn(
        's1',
        { type: 'user.message', data: { text: 'x' } },
        () => undefined,
        ctl.signal,
      ),
    ).rejects.toThrow('aborted');
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  it('uploads multipart with purpose and surface', async () => {
    const fetchFn = vi.fn(async () => new Response(JSON.stringify({ document_id: 'doc_9' }), { status: 201 }));
    const r = await httpApi('', fetchFn as unknown as typeof fetch).upload(
      's1',
      new File(['x'], 'p.jpg', { type: 'image/jpeg' }),
      'licence',
      'documents',
    );
    expect(r).toEqual({ document_id: 'doc_9' });
    const body = (fetchFn.mock.calls[0] as unknown as [string, RequestInit])[1].body as FormData;
    expect(body.get('purpose')).toBe('licence');
    expect(body.get('surface_id')).toBe('documents');
  });
});
