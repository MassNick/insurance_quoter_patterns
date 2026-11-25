/* The prototype journeys against the built app and the mock BFF (desktop). */
import { expect, test } from '@playwright/test';
import { bot, chat, confirmTwice, drag, form, photo, send, start, suggestion } from './helpers';
import { mockBff } from './mock-route';

test('full journey: consent, documents, vehicle, board, history, offers', async ({ page }) => {
  const mock = await mockBff(page);
  await start(page);
  await suggestion(page, 'Obtenir une soumission auto').click();
  await expect(chat(page).locator('.msg.bot .typing')).toBeVisible({ timeout: 150 }); // FR-CHAT-1: dots before the reply
  await suggestion(page, 'Oui, j’accepte').click();
  await confirmTwice(page);
  await expect(chat(page).getByText('Consentement confirmé (2 étapes)')).toBeVisible();

  // Documents: three uploads read by the document agent, reviewed, then used
  await suggestion(page, 'Oui, j’ai mes documents').click();
  const docs = form(page, 'Vos documents');
  await expect(docs).toBeVisible();
  for (const [purpose, file] of [
    ['licence', 'permis.jpg'],
    ['registration', 'immatriculation.jpg'],
    ['policy', 'police.pdf'],
  ]) {
    await docs
      .locator(`[data-doc="${purpose}"] input[type=file]`)
      .setInputFiles(photo(file, file.endsWith('.pdf') ? 'application/pdf' : 'image/jpeg'));
    await expect(docs.locator(`[data-doc="${purpose}"]`)).toHaveClass(/done/);
  }
  await expect(docs.getByRole('textbox', { name: 'Prénom' })).toHaveValue('Marie');
  await expect(docs.getByText('99 %').first()).toBeVisible();
  await docs.getByRole('button', { name: 'Utiliser ces informations' }).click();
  await expect(chat(page).getByText('Formulaire envoyé : Vos documents')).toBeVisible();
  await expect(page.getByRole('alertdialog')).toContainText('Numéro de permis de conduire'); // deterministic field from the licence
  await confirmTwice(page);

  // Vehicle: prefilled from the registration, add its use
  const vehicle = form(page, 'Votre véhicule');
  await expect(vehicle.getByText(/Valide : 2021 Toyota RAV4/)).toBeVisible();
  await expect(vehicle.getByText('Lu sur votre document')).toBeVisible();
  await vehicle.getByRole('radio', { name: 'Aller au travail ou à l’école' }).click();
  await vehicle.getByRole('button', { name: 'Continuer' }).click();

  // Who drives what: drag Marie to the principal slot, tap Alex then the occasional slot
  const board = form(page, 'Qui conduit quoi?');
  await expect(board).toBeVisible();
  await board.getByRole('button', { name: 'Confirmer' }).click();
  await expect(board.getByText('Chaque véhicule a besoin d’un conducteur principal')).toBeVisible();
  await drag(page, board.locator('.person[data-p="0"]'), board.getByRole('button', { name: 'Conducteur principal, RAV4' }));
  await expect(board.locator('.slot.principal .pchip')).toHaveText(/Marie/);
  await board.getByRole('button', { name: /Alex Tremblay/ }).click();
  await board.getByRole('button', { name: 'Conducteurs occasionnels, RAV4' }).click();
  await board.getByRole('button', { name: 'Confirmer' }).click();

  // History: three protected answers, one grouped double confirmation
  const history = form(page, 'Vos antécédents');
  await expect(history.getByRole('radiogroup')).toHaveCount(3);
  for (let i = 0; i < 3; i++) await history.getByRole('radiogroup').nth(i).getByRole('radio', { name: 'Non' }).click();
  await history.getByRole('button', { name: 'Continuer' }).click();
  await expect(page.getByRole('alertdialog').locator('dt')).toHaveCount(3);
  await confirmTwice(page);

  // Quote and offers
  await suggestion(page, 'Obtenir ma soumission').click();
  const offers = form(page, 'Votre soumission : 3 options');
  await offers.getByRole('radio', { name: /Plus/ }).click();
  await offers.getByRole('button', { name: 'Choisir cette offre' }).click();
  await expect(bot(page, /Excellent choix/)).toBeVisible();
  await page.getByRole('button', { name: /Dossier/ }).click();
  await page.getByRole('tab', { name: /Soumissions/ }).click();
  await expect(page.locator('.q-card.current')).toContainText('Q-1A2B3C4D · version 1');

  const board_ = mock.received.find((i) => i.type === 'a2ui.action' && i.data.surfaceId === 'qui_conduit_quoi');
  expect((board_?.data.context as { values: Record<string, unknown> }).values).toEqual({
    '/household/driver_vehicle_assignments': [
      { driver_index: 0, vehicle_index: 0, role: 'principal' },
      { driver_index: 1, vehicle_index: 0, role: 'occasional' },
    ],
  });
});

test('text-only preference: the agent asks in the conversation, no form opens', async ({ page }) => {
  await mockBff(page);
  await start(page);
  await send(page, 'Je préfère répondre par écrit');
  await expect(bot(page, 'Quel est votre nom complet?')).toBeVisible();
  await send(page, 'Marie Tremblay');
  await expect(bot(page, 'Quelle est votre date de naissance?')).toBeVisible();
  await send(page, '1987-01-18');
  await send(page, 'marie@exemple.com');
  await expect(bot(page, 'Quel véhicule voulez-vous assurer?')).toBeVisible();
  await expect(page.locator('.form-card')).toHaveCount(0);
  await page.getByRole('button', { name: /Dossier/ }).click();
  const dossier = page.getByRole('complementary', { name: 'Votre dossier' });
  await expect(dossier.locator('.row', { hasText: 'Adresse courriel' })).toContainText('marie@exemple.com');
  await expect(dossier.locator('.row', { hasText: 'Prénom' })).toContainText('À confirmer');
});

test('Lamborghini rule: confirmation question, end of form, send, chips locked', async ({ page }) => {
  await mockBff(page);
  await start(page);
  await send(page, 'Je préfère un formulaire');
  await form(page, 'À propos de vous')
    .getByRole('button', { name: /Fermer le formulaire/ })
    .click();
  await expect(chat(page).getByText(/Formulaire fermé/)).toBeVisible();
  await send(page, 'Je conduis une Lamborghini Huracán 2023');
  await expect(bot(page, /il s’agit bien d’une 2023 Lamborghini Huracán/)).toBeVisible();
  await suggestion(page, 'Oui, c’est bien ça').click();
  const end = form(page, 'Poursuivons avec un conseiller');
  await expect(end).toContainText('VEHICULE_EXOTIQUE');
  await end.getByRole('button', { name: 'Envoyer à un conseiller' }).click();
  await expect(end.getByText('Ce champ est obligatoire').first()).toBeVisible(); // phone and consent are required
  await end.getByLabel(/Prénom/).fill('Samir');
  await end.getByLabel(/Nom de famille/).fill('Haddad');
  await end.getByLabel(/Numéro de téléphone/).fill('5145550142');
  await end.getByRole('checkbox').check();
  await end.getByRole('button', { name: 'Envoyer à un conseiller' }).click();
  await expect(end.getByText('C’est envoyé!')).toBeVisible();
  await expect(end).toContainText('CNS-482139');
  await expect(end).toContainText('(514) 555-0142');
  await end.getByRole('button', { name: 'Fermer', exact: true }).click();
  await expect(page.locator('.form-card')).toHaveCount(0);
  await expect(chat(page).getByText('Dossier transmis à un conseiller.')).toBeVisible();
  await expect(page.locator('.form-chip[data-surface="end_of_form"]')).toContainText('envoyé');
  for (const chip of await page.locator('.form-chip').all()) await expect(chip).toBeDisabled();
});

test('chip reopen and save, with no agent turn', async ({ page }) => {
  const mock = await mockBff(page);
  await start(page);
  await send(page, 'Je préfère un formulaire');
  const about = form(page, 'À propos de vous');
  await about.getByLabel(/Date de naissance/).fill('1987-01-18');
  await about.getByLabel(/Adresse courriel/).fill('marie@exemple.com');
  await about.getByRole('button', { name: 'Continuer' }).click();
  const chip = page.locator('.form-chip[data-surface="a_propos"]');
  await expect(chip).toHaveAttribute('data-state', 'submitted');
  await chip.click();
  const edit = form(page, 'À propos de vous');
  await expect(edit.getByLabel(/Adresse courriel/)).toHaveValue('marie@exemple.com');
  await edit.getByLabel(/Adresse courriel/).fill('marie.t@exemple.com');
  await edit.getByRole('button', { name: 'Enregistrer' }).click();
  await expect(bot(page, 'Modifications enregistrées : À propos de vous.')).toBeVisible();
  await expect(chip).toHaveAttribute('data-state', 'submitted');
  expect(mock.received.at(-1)?.data).toMatchObject({ name: 'submit', surfaceId: 'a_propos' });
  expect(mock.received.filter((i) => i.type === 'ui.surface_reopen')).toHaveLength(1);
});

test('full-form mode: consent first, then the whole quote in one form', async ({ page }) => {
  await mockBff(page);
  await start(page);
  const toggle = page.getByRole('button', { name: /Formulaire complet/ });
  await toggle.click();
  await expect(bot(page, /j’ai besoin de votre consentement/)).toBeVisible();
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  await suggestion(page, 'Oui, j’accepte').click(); // a BFF action: the double confirmation, then the full form opens
  await confirmTwice(page);
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  const full = form(page, 'Votre soumission complète');
  await full.getByLabel(/Numéro de téléphone/).fill('514 555-0142');
  await full.getByRole('button', { name: 'Enregistrer et continuer' }).click();
  await expect(page.getByRole('button', { name: /Conversation/ })).toHaveAttribute('aria-pressed', 'true');
  await expect(bot(page, 'Modifications enregistrées : Votre soumission complète.')).toBeVisible();
});
