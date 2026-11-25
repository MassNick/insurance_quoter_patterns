/* One screenshot per visual element, rendered alone by the harness (visual.html?el=<name>). */
import { expect, test } from '@playwright/test';
import { ELEMENTS } from '../../src/visual/list';

for (const el of ELEMENTS) {
  const tags = [el.desktop !== false && '@desktop', el.phone && '@phone'].filter(Boolean).join(' ');
  test(`${el.name} ${tags}`, async ({ page }, info) => {
    await page.goto(`/visual.html?el=${el.name}`);
    await page.waitForSelector('body[data-ready="1"]');
    await page.evaluate(() => document.fonts.ready);
    const target = el.target ? page.locator(el.target).first() : page.locator('#scene');
    await expect(target).toHaveScreenshot(`${el.name}-${info.project.name}.png`);
  });
}
