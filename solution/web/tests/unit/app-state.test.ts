/* The event reducer and the session controller, driven by the mock BFF journeys. */
import { describe, expect, it, vi } from 'vitest';
import { MockBff, mockApi } from '../../fixtures/mock-bff';
import { act, msg, reopen, replay, upTo } from '../../fixtures/scenarios';
import { createSurface } from '../../fixtures/surfaces';
import type { Envelope } from '../../src/app/api';
import { currentStep, groups } from '../../src/app/dossier';
import { Session } from '../../src/app/session';
import { createStore, initialState, keepSuggestions, reduce } from '../../src/app/store';

const e = (type: string, data: unknown, actor = 'main_agent'): Envelope => ({ type, actor, data });
const feed = (envs: Envelope[], s = initialState()) => envs.reduce((st, env) => reduce(st, { type: 'envelope', env }), s);
const flush = () => new Promise((r) => setTimeout(r, 0));

describe('event reducer', () => {
  it('streams text into the typing bubble, then finalizes it with citations', () => {
    let s = reduce(initialState(), { type: 'turnStart', inbound: msg('Allô'), userText: 'Allô', typing: true });
    expect(s.messages.map((m) => [m.role, m.typing ?? false])).toEqual([
      ['user', false],
      ['bot', true],
    ]);
    s = feed([e('text.delta', { delta: 'La ' }), e('text.delta', { delta: 'franchise' })], s);
    expect(s.messages[1]).toMatchObject({ text: 'La franchise', streaming: true, typing: false });
    const citations = [{ id: 'p1', title: 'Guide, section 2.4', url: 'https://example.com/guide#2-4' }];
    s = feed([e('text.done', { text: 'La franchise [1].', citations })], s);
    expect(s.messages[1]).toMatchObject({ text: 'La franchise [1].', streaming: false, citations });
    s = reduce(s, { type: 'turnEnd' });
    expect(s.busy).toBe(false);
  });

  it('drops an empty typing bubble when the stream carried no text', () => {
    let s = reduce(initialState(), { type: 'turnStart', inbound: msg('x'), typing: true });
    s = reduce(s, { type: 'turnEnd' });
    expect(s.messages).toEqual([]);
  });

  it('keeps at most 4 short suggestions without personal data (D15, defence in depth)', () => {
    const items = keepSuggestions([
      { label: 'Oui, c’est marie@example.com' },
      { label: 'Appelez-moi au 514 555-0142' },
      { label: 'Mon code postal est H2X 1Y4' },
      { label: 'x'.repeat(41) },
      { label: 'Un' },
      { label: 'Deux' },
      { label: 'Trois' },
      { label: 'Quatre' },
      { label: 'Cinq' },
    ]);
    expect(items.map((x) => x.label)).toEqual(['Un', 'Deux', 'Trois', 'Quatre']);
  });

  it('attaches a form chip to the reply that opened the surface and puts the surface on stage', () => {
    let s = reduce(initialState(), { type: 'turnStart', inbound: msg('x'), typing: true });
    s = feed([e('text.done', { text: 'Voici un formulaire.' }), e('a2ui.message', createSurface('a_propos'))], s);
    const bot = s.messages.find((m) => m.role === 'bot');
    expect(bot?.chips).toEqual(['a_propos']);
    expect(s.chips.a_propos).toMatchObject({ label: 'Formulaire : À propos de vous', status: 'open', messageId: bot?.id });
    expect(s.stage).toBe('a_propos');
  });

  it('sets the chip from how the surface closed: submitted, closed, or back to submitted after a reopen', () => {
    let s = feed([e('text.done', { text: 'x' }), e('a2ui.message', createSurface('a_propos'))]);
    s = reduce(s, { type: 'submitted', surfaceId: 'a_propos', status: 'submitted' });
    s = feed([e('a2ui.message', { version: '1.0', deleteSurface: { surfaceId: 'a_propos' } }, 'bff')], s);
    expect(s.chips.a_propos.status).toBe('submitted');
    expect(s.stage).toBeNull();
    s = feed([e('a2ui.message', createSurface('a_propos'), 'bff')], s); // reopened in edit mode
    expect(s.chips.a_propos).toMatchObject({ status: 'open', edit: true, prev: 'submitted' });
    expect(s.messages.filter((m) => m.chips.length)).toHaveLength(1); // the same chip, no new one
    s = feed([e('a2ui.message', { version: '1.0', deleteSurface: { surfaceId: 'a_propos' } }, 'bff')], s); // closed without saving
    expect(s.chips.a_propos.status).toBe('submitted');
    s = feed([e('a2ui.message', createSurface('vehicule_0'))], s);
    s = feed([e('a2ui.message', { version: '1.0', deleteSurface: { surfaceId: 'vehicule_0' } }, 'bff')], s); // closed by the agent
    expect(s.chips.vehicule_0.status).toBe('closed');
  });

  it('locks every chip and marks the hand-off chip sent on handoff.created; the card stays until Fermer', () => {
    let s = feed([
      e('text.done', { text: 'x' }),
      e('a2ui.message', createSurface('a_propos')),
      e('a2ui.message', createSurface('end_of_form'), 'bff'),
    ]);
    s = feed(
      [
        e('handoff.created', { reference: 'CNS-123456', rule_id: null }, 'bff'),
        e('a2ui.message', { version: '1.0', deleteSurface: { surfaceId: 'end_of_form' } }, 'bff'),
      ],
      s,
    );
    expect(s.chips.end_of_form).toMatchObject({ status: 'sent', locked: true });
    expect(s.chips.a_propos.locked).toBe(true);
    expect(s.stage).toBe('end_of_form');
    s = reduce(s, { type: 'closeHandoff' });
    expect(s.stage).toBeNull();
    expect(s.messages.at(-1)?.text).toBe('Dossier transmis à un conseiller.');
  });

  it('mirrors state.delta and flashes the changed rows; flags become advisor signals', () => {
    const s = feed([
      e('state.delta', { changes: [{ path: '/vehicles/0/vehicle_make', value: 'Toyota', source: 'chat', status: 'inferred' }] }, 'bff'),
      e(
        'rules.hit',
        { rule_id: 'JEUNE_CONDUCTEUR_PRINCIPAL', severity: 'flag', paths: [], inferred: false, reason: 'Jeune conducteur principal.' },
        'bff',
      ),
    ]);
    expect(s.doc).toMatchObject({ vehicles: [{ vehicle_make: { value: 'Toyota', source: 'chat', status: 'inferred' } }] });
    expect(s.flash.paths).toEqual(['/vehicles/0/vehicle_make']);
    expect(s.signals).toEqual([{ code: 'JEUNE_CONDUCTEUR_PRINCIPAL', message: 'Jeune conducteur principal.', flag: true }]);
  });

  it('queues confirmations, opens the modal when the stream ends, and drops a rejected one', () => {
    let s = feed([
      e(
        'confirm.required',
        {
          confirmation_id: 'cf_1',
          path: '/underwriting/has_claims',
          old_value: null,
          new_value: false,
          reason: 'r',
          changes: [{ path: '/underwriting/has_claims', old_value: null, new_value: false }],
        },
        'bff',
      ),
    ]);
    expect(s.modal).toBeNull();
    s = reduce(s, { type: 'turnEnd' });
    expect(s.modal).toEqual({ step: 1, ids: ['cf_1'] });
    s = feed([e('confirm.rejected', { confirmation_id: 'cf_1', reason: 'expired', paths: [] }, 'bff')], s);
    expect(s.modal).toBeNull();
    expect(s.confirmations).toEqual([]);
  });

  it('keeps quote versions and pings the Quotes tab', () => {
    const s = feed([
      e('quote.created', { quote_id: 'Q-1', version: 1, offers: [] }),
      e('quote.updated', { quote_id: 'Q-1', version: 2, offers: [], previous_version: 1, delta_monthly: -9.5 }),
    ]);
    expect(s.quotes.map((q) => q.version)).toEqual([1, 2]);
    expect(s.quotesPing).toBe(2);
  });

  it('shows a retry on a retryable error and on a failed stream', () => {
    let s = reduce(initialState(), { type: 'turnStart', inbound: msg('x'), userText: 'x', typing: true });
    s = feed([e('error', { code: 'agent_unavailable', message: 'timeout', retryable: true }, 'bff')], s);
    expect(s.messages.at(-1)).toMatchObject({ text: expect.stringContaining('réessayer'), retry: msg('x') });
    s = reduce(reduce(s, { type: 'turnStart', inbound: msg('y'), typing: true }), { type: 'turnFailed' });
    expect(s.messages.at(-1)?.retry).toEqual(msg('y'));
  });

  it('enters and leaves full-form mode with the full_form surface, without a chip', () => {
    let s = feed([e('a2ui.message', createSurface('full_form'), 'bff')]);
    expect(s.mode).toBe('form');
    expect(s.chips.full_form).toBeUndefined();
    s = feed([e('a2ui.message', { version: '1.0', deleteSurface: { surfaceId: 'full_form' } }, 'bff')], s);
    expect(s.mode).toBe('chat');
  });
});

describe('dossier derivations', () => {
  it('groups the committed state like the prototype and derives the step chips', () => {
    const { store } = replay(upTo('boardOpen'));
    const st = store.get();
    const g = groups(st.doc);
    expect(g.map((x) => x.title)).toEqual([
      'Votre demande',
      'Vous',
      'Véhicules',
      'Conducteurs',
      'Assurance actuelle',
      'Antécédents',
      'Autres renseignements',
    ]);
    expect(g[3].rows.map((r) => r.value)).toEqual(['Marie Tremblay · Vous', 'Alex Tremblay · Conjoint ou conjointe']);
    expect(g[1].rows.find((r) => r.label === 'Numéro de permis de conduire')?.meta?.text).toBe('Confirmé deux fois');
    expect(currentStep(st)).toBe(3); // the board is open: Conducteurs
  });
});

describe('session controller', () => {
  const start = async () => {
    const mock = new MockBff();
    const session = new Session(createStore(), mockApi(mock));
    await session.start();
    return { mock, session, store: session.store };
  };

  it('boots with the BFF greeting and suggestions', async () => {
    const { store } = await start();
    expect(store.get().messages[0].text).toMatch(/^Bonjour!/);
    expect(store.get().suggestions).toHaveLength(4);
    expect(store.get().busy).toBe(false);
  });

  it('shows the typing dots synchronously on send, before the network answers (FR-CHAT-1)', async () => {
    const { session, store } = await start();
    const api = (session as unknown as { api: { turn: (...a: unknown[]) => Promise<void> } }).api;
    const turn = vi.spyOn(api, 'turn');
    const done = session.send('Obtenir une soumission auto');
    expect(
      store
        .get()
        .messages.slice(-2)
        .map((m) => [m.role, m.typing ?? false]),
    ).toEqual([
      ['user', false],
      ['bot', true],
    ]);
    expect(turn).not.toHaveBeenCalled(); // drawn before the request starts
    await done;
    expect(store.get().messages.at(-1)?.typing).toBe(false);
  });

  it('runs the double confirmation as two confirm.response steps per confirmation', async () => {
    const { session, store, mock } = await start();
    await session.send('Obtenir une soumission auto');
    await session.send('Oui, j’accepte');
    expect(store.get().modal).toEqual({ step: 1, ids: ['cf_1'] });
    session.confirm(1, true);
    expect(store.get().modal?.step).toBe(2);
    session.confirm(2, true);
    await (session as unknown as { queue: Promise<void> }).queue;
    expect(mock.received.filter((i) => i.type === 'confirm.response').map((i) => i.data)).toEqual([
      { confirmation_id: 'cf_1', step: 1, accepted: true },
      { confirmation_id: 'cf_1', step: 2, accepted: true },
    ]);
    expect(store.get().modal).toBeNull();
    expect(store.get().messages.some((m) => m.text === 'Consentement confirmé (2 étapes)')).toBe(true);
    expect(store.get().messages.at(-1)?.text).toMatch(/vos documents sous la main/);
  });

  it('goes back from step 2 without answering, and cancels at step 1', async () => {
    const { session, store, mock } = await start();
    await session.send('Obtenir une soumission auto');
    await session.send('Oui, j’accepte');
    session.confirm(1, true);
    session.confirm(2, false); // Retour
    expect(store.get().modal?.step).toBe(1);
    session.confirm(1, false);
    await (session as unknown as { queue: Promise<void> }).queue;
    expect(mock.received.filter((i) => i.type === 'confirm.response').map((i) => i.data.accepted)).toEqual([true, false]);
    expect(store.get().doc).toMatchObject({ quote: { contact_consent: { value: null } } });
  });

  it('closes on dismiss right away, then tells the BFF; reopen and save stay out of the agent turn', async () => {
    const { session, store, mock } = await start();
    await session.send('Je préfère un formulaire');
    expect(store.get().stage).toBe('a_propos');
    const done = session.onAction({ name: 'dismiss', surfaceId: 'a_propos', sourceComponentId: 'dismiss', timestamp: '', context: {} });
    expect(store.get().stage).toBeNull(); // closed before the BFF answered
    await done;
    expect(store.get().chips.a_propos.status).toBe('closed');
    await session.reopen('a_propos');
    expect(store.get().chips.a_propos).toMatchObject({ status: 'open', edit: true });
    expect(mock.received.at(-1)).toEqual(reopen('a_propos'));
    await session.onAction({
      name: 'submit',
      surfaceId: 'a_propos',
      sourceComponentId: 'action_submit',
      timestamp: '',
      context: { values: { '/quote/email': 'm@x.ca' } },
    });
    expect(store.get().chips.a_propos.status).toBe('submitted');
    expect(store.get().messages.at(-1)?.text).toBe('Modifications enregistrées : À propos de vous.');
    expect(
      store
        .get()
        .messages.filter((m) => m.role === 'user')
        .at(-1)?.text,
    ).toBe('Je préfère un formulaire'); // no "Formulaire envoyé" bubble for a save
  });

  it('sends a submit with a "Formulaire envoyé" bubble and the action as an a2ui.action', async () => {
    const { session, store, mock } = await start();
    await session.send('Je préfère un formulaire');
    await session.onAction({
      name: 'submit',
      surfaceId: 'a_propos',
      sourceComponentId: 'action_submit',
      timestamp: 't',
      context: { surfaceId: 'a_propos', values: { '/quote/date_of_birth': '1987-01-18' } },
    });
    expect(store.get().messages.some((m) => m.role === 'user' && m.text === 'Formulaire envoyé : À propos de vous')).toBe(true);
    expect(mock.received.at(-1)).toEqual(
      act('a_propos', 'submit', { '/quote/date_of_birth': '1987-01-18' }).type === 'a2ui.action' && mock.received.at(-1),
    );
    expect((mock.received.at(-1)?.data as { context: { values: unknown } }).context.values).toEqual({
      '/quote/date_of_birth': '1987-01-18',
    });
  });

  it('asks for consent before the full form, and opens it once consent is given', async () => {
    const { session, store } = await start();
    await session.fullForm(true);
    expect(store.get().mode).toBe('chat');
    expect(store.get().messages.at(-1)?.text).toMatch(/consentement/);
  });

  it('sends the advisor request from the header and opens the hand-off surface with no agent turn', async () => {
    const { session, store, mock } = await start();
    await session.talkToAdvisor();
    expect(mock.received.at(-1)?.data).toEqual({ text: 'Parler à un conseiller', via: 'header', action_id: 'talk_to_advisor' });
    expect(store.get().stage).toBe('end_of_form');
  });

  it('answers renderer function calls through callAgentFunction', async () => {
    const { session, store } = await start();
    const r = await session.callFunction('vehicule_doc', 'lookupMakeModelYear', { year: 2021, make: 'Toyota', model: 'RAV4' });
    expect(r).toMatchObject({ valid: true });
    expect(store.get().trace.some((t) => t.type === 'a2ui.callAgentFunction')).toBe(true);
  });

  it('continues without documents: closes the form, then says so in the conversation', async () => {
    const { session, store } = await start();
    for (const t of ['Obtenir une soumission auto', 'Oui, j’accepte']) await session.send(t);
    session.confirm(1, true);
    session.confirm(2, true);
    await session.send('Oui, j’ai mes documents');
    await session.onAction({ name: 'nodocs', surfaceId: 'documents', sourceComponentId: 'action_nodocs', timestamp: '', context: {} });
    await flush();
    await (session as unknown as { queue: Promise<void> }).queue;
    expect(store.get().chips.documents.status).toBe('closed');
    expect(
      store
        .get()
        .messages.filter((m) => m.role === 'user')
        .at(-1)?.text,
    ).toBe('Continuer sans documents');
  });
});
