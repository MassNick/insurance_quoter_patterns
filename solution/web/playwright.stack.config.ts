/* Full stack (e2e-stack/): the built app in a real browser against the real BFF, quote-agent (fake model),
   mcp-quoting and mcp-a2ui. Locally ../e2e/stack.py starts everything on ports +30000; in Docker Compose
   (../e2e/docker-compose.yml) E2E_BFF_URL points at the bff container and only the web preview starts here. */
import { defineConfig } from '@playwright/test';

const compose = process.env.E2E_BFF_URL;
const port = compose ? 5174 : 35173;

export default defineConfig({
  testDir: 'e2e-stack',
  workers: 1, // one stack, one conversation at a time keeps the logs readable
  forbidOnly: !!process.env.CI,
  reporter: [['list'], ['html', { open: 'never', outputFolder: '../e2e/playwright-report' }]],
  outputDir: '../e2e/test-results',
  timeout: 60_000,
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    browserName: 'chromium',
    locale: 'fr-CA',
    timezoneId: 'America/Toronto',
    viewport: { width: 1280, height: 900 },
    trace: 'retain-on-failure',
  },
  webServer: {
    command: compose
      ? `npm run build && npx vite preview --port ${port} --strictPort --host 127.0.0.1`
      : 'npm run build && python3 ../e2e/stack.py up --offset 30000 --web',
    url: `http://127.0.0.1:${port}`,
    env: compose ? { BFF_PROXY_TARGET: compose } : {},
    reuseExistingServer: false,
    gracefulShutdown: { signal: 'SIGTERM', timeout: 15_000 }, // stack.py stops its services on SIGTERM
    timeout: 180_000,
  },
});
