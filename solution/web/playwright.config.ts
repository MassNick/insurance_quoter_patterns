/* End-to-end (e2e/): the built app served by Vite preview, the BFF replaced by the scripted mock
   (fixtures/mock-bff.ts) through route interception, real HTTP + SSE parsing in the page. */
import { defineConfig, devices } from '@playwright/test';

const port = 5175;

export default defineConfig({
  testDir: 'e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report/e2e' }]],
  outputDir: 'test-results/e2e',
  use: {
    baseURL: `http://localhost:${port}`,
    browserName: 'chromium',
    locale: 'fr-CA',
    timezoneId: 'America/Toronto',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop', testIgnore: /phone/, use: { viewport: { width: 1280, height: 900 } } },
    { name: 'phone', testMatch: /phone/, use: { ...devices['Pixel 7'], browserName: 'chromium', viewport: { width: 390, height: 844 } } },
  ],
  webServer: {
    command: `npm run build && npx vite preview --port ${port} --strictPort`,
    port,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
