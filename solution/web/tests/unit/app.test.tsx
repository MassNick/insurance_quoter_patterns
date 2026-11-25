/* The shell against the mock BFF: chat, suggestions, chips, stage, modal, dossier, full form, header. */
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { MockBff, mockApi } from '../../fixtures/mock-bff';
import { App } from '../../src/app/components/App';
import { AppContext } from '../../src/app/context';
import { Session } from '../../src/app/session';
import { createStore } from '../../src/app/store';

async function boot(dev = false) {
  const mock = new MockBff();
  const session = new Session(createStore(), mockApi(mock));
  render(
    <AppContext.Provider value={session}>
      <App dev={dev} />
    </AppContext.Provider>,
  );
  await session.start();
  await transcript().findByText(/Que souhaitez-vous faire\?/);
  return { mock, session, user: userEvent.setup() };
}
/** The visible conversation (the screen-reader live region repeats the last reply). */
const transcript = () => within(document.getElementById('scroll') as HTMLElement);
const idle = (s: Session) => (s as unknown as { queue: Promise<void> }).queue;
const suggestion = (name: string | RegExp) =>
  within(screen.getByRole('group', { name: 'Réponses suggérées' })).getByRole('button', { name });

describe('chat', () => {
  it('greets, shows the suggestions, sends a clicked suggestion with via=suggestion', async () => {
    const { mock, session, user } = await boot();
    expect(screen.getByText('Q7K2M9TX')).toBeInTheDocument();
    await user.click(suggestion('Obtenir une soumission auto'));
    await idle(session);
    expect(mock.received[0]).toEqual({ type: 'user.message', data: { text: 'Obtenir une soumission auto', via: 'suggestion' } });
    expect(await transcript().findByText(/puis-je les utiliser pour préparer votre soumission\?/)).toBeInTheDocument();
    expect(suggestion('Oui, j’accepte')).toBeInTheDocument();
  });

  it('sends typed text with Enter and keeps Shift+Enter for a new line', async () => {
    const { mock, session, user } = await boot();
    const box = screen.getByRole('textbox', { name: 'Message' });
    await user.type(box, 'Bonjour{Shift>}{Enter}{/Shift}toi');
    expect(box).toHaveValue('Bonjour\ntoi');
    await user.type(box, '{Enter}');
    await idle(session);
    expect(mock.received[0].data.text).toBe('Bonjour\ntoi');
    expect(box).toHaveValue('');
  });

  it('lists the citations of a knowledge answer, linking only https sources', async () => {
    const { session } = await boot();
    const citations = [
      { id: 'p1', title: 'Guide des protections, section 2.4', url: 'https://example.com/guide' },
      { id: 'p2', title: 'Note interne', url: 'javascript:alert(1)' },
    ];
    act(() =>
      session.store.dispatch({
        type: 'envelope',
        env: { type: 'text.done', actor: 'main_agent', data: { text: 'La franchise [1].', citations } },
      }),
    );
    expect(transcript().getByRole('link', { name: 'Guide des protections, section 2.4' })).toHaveAttribute(
      'href',
      'https://example.com/guide',
    );
    expect(transcript().queryByRole('link', { name: 'Note interne' })).toBeNull();
    expect(transcript().getByText(/Note interne/)).toBeInTheDocument();
  });

  it('announces a reply once complete through a polite live region (NFR-ACC-1)', async () => {
    await boot();
    const live = document.querySelector('[aria-live="polite"].sr-only');
    expect(live?.textContent).toMatch(/^Bonjour!/);
  });
});

describe('double confirmation, documents, chips', () => {
  it('confirms consent in two steps with Esc, focus and the acknowledgement box (FR-UI-6)', async () => {
    const { mock, session, user } = await boot();
    await session.send('Obtenir une soumission auto');
    await session.send('Oui, j’accepte');
    const dialog = await screen.findByRole('alertdialog', { name: 'Confirmez cette réponse' });
    expect(within(dialog).getByText(/non indiqué →/)).toHaveTextContent('non indiqué → Oui');
    expect(within(dialog).getByRole('button', { name: 'C’est exact' })).toHaveFocus();
    await user.tab();
    expect(within(dialog).getByRole('button', { name: 'Annuler' })).toHaveFocus(); // focus stays inside
    await user.click(within(dialog).getByRole('button', { name: 'C’est exact' }));
    const step2 = await screen.findByRole('alertdialog', { name: 'Confirmez-vous?' });
    expect(within(step2).getByRole('button', { name: 'Oui, je confirme' })).toBeDisabled();
    await user.keyboard('{Escape}'); // cancels the current step: back to step 1
    expect(await screen.findByRole('alertdialog', { name: 'Confirmez cette réponse' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'C’est exact' }));
    await user.click(screen.getByRole('checkbox', { name: /Je confirme que ces renseignements sont exacts/ }));
    await user.click(screen.getByRole('button', { name: 'Oui, je confirme' }));
    await idle(session);
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(mock.received.filter((r) => r.type === 'confirm.response').map((r) => r.data.step)).toEqual([1, 1, 2]); // step 1 again after going back
    expect(transcript().getByText('Consentement confirmé (2 étapes)')).toBeInTheDocument();
  });

  it('opens a form with a chip, submits it, and reopens the chip in edit mode', async () => {
    const { mock, session, user } = await boot();
    await session.send('Je préfère un formulaire');
    const card = await screen.findByRole('dialog', { name: 'À propos de vous' });
    const chip = screen.getByRole('button', { name: /Formulaire : À propos de vous/ });
    expect(chip).toBeDisabled();
    expect(chip).toHaveAttribute('data-state', 'open');
    await user.type(within(card).getByLabelText(/Date de naissance/), '1987-01-18');
    await user.type(within(card).getByLabelText(/Adresse courriel/), 'marie@exemple.com');
    await user.click(within(card).getByRole('button', { name: 'Continuer' }));
    await idle(session);
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'À propos de vous' })).toBeNull());
    expect(transcript().getByText('Formulaire envoyé : À propos de vous')).toBeInTheDocument();
    const done = screen.getByRole('button', { name: /Formulaire : À propos de vous/ });
    expect(done).toHaveAttribute('data-state', 'submitted');
    await user.click(done);
    await idle(session);
    expect(mock.received.at(-1)).toEqual({ type: 'ui.surface_reopen', data: { surface_id: 'a_propos' } });
    const edit = await screen.findByRole('dialog', { name: 'À propos de vous' });
    expect(within(edit).getByRole('button', { name: 'Enregistrer' })).toBeInTheDocument();
    expect(within(edit).getByLabelText(/Adresse courriel/)).toHaveValue('marie@exemple.com');
  });
});

describe('dossier, header, full form, trace', () => {
  it('opens Votre dossier from the rail with two tabs and help on hover', async () => {
    const { session, user } = await boot();
    await session.send('Je préfère un formulaire');
    const rail = screen.getByRole('button', { name: /Dossier/ });
    expect(rail).toHaveAttribute('aria-expanded', 'false');
    await user.click(rail);
    expect(rail).toHaveAttribute('aria-expanded', 'true');
    const panel = screen.getByRole('complementary', { name: 'Votre dossier' });
    expect(within(panel).getByRole('tab', { name: 'Renseignements' })).toHaveAttribute('aria-selected', 'true');
    await user.hover(within(panel).getAllByText('Prénom')[0]);
    expect(screen.getByRole('tooltip')).toHaveTextContent('Tel qu’inscrit sur votre permis de conduire.');
    await user.click(within(panel).getByRole('tab', { name: /Soumissions/ }));
    expect(within(panel).getByText(/Aucune soumission pour l’instant/)).toBeVisible();
  });

  it('header: "Parler à un conseiller" works by keyboard and opens the hand-off surface', async () => {
    const { mock, session, user } = await boot();
    within(screen.getByRole('banner')).getByRole('button', { name: 'Parler à un conseiller' }).focus();
    await user.keyboard('{Enter}');
    await idle(session);
    expect(mock.received[0].data).toMatchObject({ action_id: 'talk_to_advisor', via: 'header' });
    expect(await screen.findByRole('dialog', { name: 'Poursuivons avec un conseiller' })).toBeInTheDocument();
  });

  it('full-form toggle: asks for consent first, then shows the whole quote', async () => {
    const { session, user } = await boot();
    await user.click(screen.getByRole('button', { name: /Formulaire complet/ }));
    await idle(session);
    expect(transcript().getByText(/j’ai besoin de votre consentement/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Formulaire complet/ })).toHaveAttribute('aria-pressed', 'false');
    await session.send('Obtenir une soumission auto');
    await session.send('Oui, j’accepte');
    session.confirm(1, true);
    session.confirm(2, true);
    await idle(session);
    await user.click(screen.getByRole('button', { name: /Formulaire complet/ }));
    await idle(session);
    expect(screen.getByRole('button', { name: /Formulaire complet/ })).toHaveAttribute('aria-pressed', 'true');
    expect(await screen.findByRole('dialog', { name: 'Votre soumission complète' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Retour à la conversation' }));
    await idle(session);
    expect(screen.getByRole('button', { name: /Conversation/ })).toHaveAttribute('aria-pressed', 'true');
  });

  it('has no trace drawer unless ?dev=1 allows it', async () => {
    await boot(false);
    expect(screen.queryByRole('button', { name: /Trace/ })).toBeNull();
  });

  it('dev trace lists inbound and outbound events in English', async () => {
    const { session, user } = await boot(true);
    await session.send('Obtenir une soumission auto');
    await user.click(screen.getByRole('button', { name: /Trace/ }));
    expect(document.body).toHaveClass('dev');
    expect(screen.getAllByText('turn.completed').length).toBeGreaterThan(0);
    expect(screen.getByText('user.message')).toBeInTheDocument();
  });
});
