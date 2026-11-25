/* Visual regression per element (tests/visual): one screenshot per element of src/visual/list.ts, at
   desktop width and, where relevant, 390 px. Baselines live in tests/visual/__screenshots__/<platform>/. */
import { defineConfig } from '@playwright/test';

const port = 5174;

export default defineConfig({
  testDir: 'tests/visual',
  snapshotPathTemplate: '{testDir}/__screenshots__/{platform}/{arg}{ext}',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report/visual' }]],
  outputDir: 'test-results/visual',
  expect: { toHaveScreenshot: { animations: 'disabled', caret: 'hide', scale: 'css', maxDiffPixelRatio: 0.002 } },
  use: {
    baseURL: `http://localhost:${port}`,
    browserName: 'chromium',
    reducedMotion: 'reduce',
    locale: 'fr-CA',
    timezoneId: 'America/Toronto',
    deviceScaleFactor: 1,
  },
  projects: [
    { name: 'desktop', grep: /@desktop/, use: { viewport: { width: 1280, height: 900 } } },
    { name: 'phone', grep: /@phone/, use: { viewport: { width: 390, height: 844 } } },
  ],
  webServer: { command: `npx vite --port ${port} --strictPort`, port, reuseExistingServer: !process.env.CI },
});
