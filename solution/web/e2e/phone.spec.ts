/* The phone layout (390 px): the form covers the conversation while suggestions and the composer stay
   usable; the modal is a bottom sheet; the rail is a bottom tab bar; nothing scrolls sideways. */
import { expect, test } from '@playwright/test';
import { bot, consent, form, send, start } from './helpers';
import { mockBff } from './mock-route';

test('phone journey: consent sheet, documents form over the chat, composer usable, dossier sheet', async ({ page }) => {
  await mockBff(page);
  await start(page);
  const rail = await page.locator('.rail').boundingBox();
  expect(rail?.y).toBeGreaterThan(700); // bottom tab bar
  await consent(page);
  await page.getByRole('group', { name: 'Réponses suggérées' }).getByRole('button', { name: 'Oui, j’ai mes documents' }).click();
  const docs = form(page, 'Vos documents');
  await expect(docs).toBeVisible();
  await expect(page.locator('#chat')).toHaveClass(/pushed/);
  const card = await docs.boundingBox();
  const composer = await page.locator('.composer').boundingBox();
  expect(card && composer && card.y + card.height <= composer.y).toBe(true); // the form ends above the composer
  await send(page, 'Je ne les ai finalement pas'); // typed while the form is open
  await expect(page.locator('.form-card')).toHaveCount(0);
  await expect(bot(page, 'Quel est votre nom complet?')).toBeVisible();
  await page.locator('.rail').click();
  const sheet = await page.getByRole('complementary', { name: 'Votre dossier' }).boundingBox();
  expect(sheet && sheet.y > 100 && sheet.x === 0).toBe(true);
  const sideways = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(sideways).toBe(0);
});
