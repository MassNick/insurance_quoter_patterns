/* The real services behind the real UI: what the mock-BFF journeys (e2e/) cannot prove. */
import { expect, test } from '@playwright/test';
import { bot, chat, confirmTwice, consent, form, send, start, suggestion } from '../e2e/helpers';

test('consent, then the name in chat opens a small form; submitting it leaves a chip', async ({ page }) => {
  await start(page);
  await consent(page);
  await suggestion(page, /Je ne les ai pas|Non/)
    .first()
    .click();
  await send(page, 'Je m’appelle Marie Tremblay');
  const card = page.getByRole('dialog');
  await expect(card).toBeVisible();
  await card.getByLabel(/naissance/i).fill('1987-01-18');
  await card.getByRole('button', { name: /Continuer|Envoyer|Suivant/ }).click();
  await expect(card).toHaveCount(0);
  await expect(chat(page).getByText(/Formulaire envoyé/)).toBeVisible();
});

test('an exotic car stops the quote: the rule asks first, then the end-of-form hands off with a reference', async ({ page }) => {
  await start(page);
  await consent(page);
  await send(page, 'Je conduis une Lamborghini Huracán 2023');
  await expect(bot(page, /Lamborghini/)).toBeVisible();
  await suggestion(page, 'Oui, c’est bien ça').click();
  const end = form(page, 'Poursuivons avec un conseiller');
  await expect(end).toBeVisible();
  await end.getByRole('textbox', { name: /^Prénom/ }).fill('Samir');
  await end.getByRole('textbox', { name: /^Nom de famille/ }).fill('Haddad');
  await end.getByRole('textbox', { name: /^Numéro de téléphone/ }).fill('514 555-0142');
  await end.getByRole('checkbox', { name: /Consentement à transmettre/ }).check();
  await end.getByRole('button', { name: 'Envoyer à un conseiller' }).click();
  await expect(chat(page).getByText(/CNS-\d{6}/)).toBeVisible();
});

test('full-form mode asks consent first; after the double confirmation the whole form opens', async ({ page }) => {
  await start(page);
  const toggle = page.getByRole('button', { name: /Formulaire complet/ });
  await toggle.click();
  await expect(bot(page, /j’ai besoin de votre consentement/)).toBeVisible();
  await suggestion(page, 'Oui, j’accepte').click();
  await confirmTwice(page);
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('dialog').getByRole('textbox', { name: /^Numéro de téléphone/ })).toBeVisible();
});
