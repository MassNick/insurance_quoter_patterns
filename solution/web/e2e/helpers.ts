/* Customer gestures shared by the e2e specs. */
import { expect, type Locator, type Page } from '@playwright/test';

export const chat = (page: Page) => page.locator('#scroll');
export const suggestion = (page: Page, name: string | RegExp) =>
  page.getByRole('group', { name: 'Réponses suggérées' }).getByRole('button', { name });
export const form = (page: Page, name: string | RegExp) => page.getByRole('dialog', { name });
export const bot = (page: Page, text: string | RegExp) => chat(page).locator('.msg.bot .bubble-body', { hasText: text });

export async function start(page: Page) {
  await page.goto('/');
  await expect(bot(page, 'Que souhaitez-vous faire?')).toBeVisible();
}

export async function confirmTwice(page: Page) {
  await page.getByRole('alertdialog').getByRole('button', { name: 'C’est exact' }).click();
  const step2 = page.getByRole('alertdialog', { name: 'Confirmez-vous?' });
  await step2.getByRole('checkbox').check();
  await step2.getByRole('button', { name: 'Oui, je confirme' }).click();
  await expect(page.getByRole('alertdialog')).toHaveCount(0);
}

/** Greeting → quote → consent with the double confirmation → the document offer. */
export async function consent(page: Page) {
  await suggestion(page, 'Obtenir une soumission auto').click();
  await suggestion(page, 'Oui, j’accepte').click();
  await expect(page.getByRole('alertdialog', { name: 'Confirmez cette réponse' })).toBeVisible();
  await confirmTwice(page);
  await expect(bot(page, /vos documents sous la main/)).toBeVisible();
}

export async function send(page: Page, text: string) {
  await page.getByRole('textbox', { name: 'Message' }).fill(text);
  await page.getByRole('button', { name: 'Envoyer' }).click();
}

export const photo = (name: string, mimeType = 'image/jpeg') => ({ name, mimeType, buffer: Buffer.from('fake image bytes') });

/** Drags with the mouse (pointer events, pointer capture) from one element to another. */
export async function drag(page: Page, from: Locator, to: Locator) {
  const a = await from.boundingBox();
  const b = await to.boundingBox();
  if (!a || !b) throw new Error('drag target not visible');
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(a.x + a.width / 2 + 10, a.y + a.height / 2 + 10, { steps: 3 });
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 12 });
  await page.mouse.up();
}
