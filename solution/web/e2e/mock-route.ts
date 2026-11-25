/* Serves the mock BFF to the page over real HTTP: JSON endpoints, and SSE for turns (with a delay so
   the typing dots are observable). */
import type { Page } from '@playwright/test';
import { MockBff, sse } from '../fixtures/mock-bff';

export async function mockBff(page: Page, opts: { delayMs?: number } = {}): Promise<MockBff> {
  const mock = new MockBff();
  const json = (body: unknown, status = 200) => ({ status, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/**', async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/api/sessions' && req.method() === 'POST')
      return route.fulfill({
        ...json(mock.createSession(), 201),
        headers: { 'set-cookie': 'qsid=s_mock01.dev-only-signature; Path=/api; HttpOnly; SameSite=Strict' },
      });
    const m = /^\/api\/sessions\/[^/]+\/(turns|a2ui\/functions|documents|state)$/.exec(path);
    if (!m) return route.fulfill(json({ title: 'Not found', status: 404 }, 404));
    if (m[1] === 'turns') {
      const events = mock.handle(req.postDataJSON());
      await new Promise((r) => setTimeout(r, opts.delayMs ?? 300));
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: sse(events) });
    }
    if (m[1] === 'a2ui/functions') return route.fulfill(json(mock.fn(req.postDataJSON())));
    if (m[1] === 'documents') return route.fulfill(json(mock.upload(), 201));
    return route.fulfill(json(mock.snapshot()));
  });
  return mock;
}
